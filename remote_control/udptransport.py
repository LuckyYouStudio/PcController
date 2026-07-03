"""P2P transport over a (hole-punched) UDP socket.

UDP is unreliable, so this multiplexes two sub-channels over one socket:

* a **reliable, ordered byte stream** (sliding window + cumulative ACK +
  retransmit) for control traffic — auth, screen info, input, clipboard. It is
  exposed as a socket-like object so the existing ``protocol.send_msg`` /
  ``recv_msg`` framing works over it unchanged.
* an **unreliable frame channel** for video: each JPEG frame is split into UDP
  fragments; the receiver reassembles a frame and simply drops it if a fragment
  is missing when the next frame starts (a dropped frame, not a broken stream).

Every packet starts with a 1-byte tag so the receive loop can demultiplex.
This module has no NAT logic; it just needs a UDP socket already pointed at the
peer (see ``holepunch``). Fully unit-testable over loopback, including loss.
"""

import struct
import threading
import time

TAG_CTRL = 0x43   # 'C' reliable data      : tag, seq(u32), payload
TAG_ACK = 0x41    # 'A' cumulative ack      : tag, next_expected(u32)
TAG_FRAME = 0x46  # 'F' frame fragment      : tag, frame_id(u32), idx(u16), cnt(u16), data
TAG_PUNCH = 0x4B  # 'K' keepalive / punch   : ignored here

_CTRL = struct.Struct(">BI")
_ACK = struct.Struct(">BI")
_FRAG = struct.Struct(">BIHH")

MAX_PAYLOAD = 1100      # bytes of data per UDP packet (safe under common MTU)
WINDOW = 256            # reliable in-flight packets
RTO = 0.20              # retransmit timeout (s)
KEEP_FRAMES = 4         # in-progress frames to keep while reassembling


class UDPTransport:
    def __init__(self, sock, peer_addr):
        self._sock = sock
        self._peer = peer_addr
        self._closed = False

        # reliable sender
        self._lock = threading.Lock()
        self._snd_next = 0
        self._snd_base = 0
        self._snd_buf = {}          # seq -> [packet_bytes, last_send_time]
        self._snd_pending = bytearray()

        # reliable receiver
        self._rcv_next = 0
        self._rcv_buf = {}          # seq -> payload
        self._rcv_stream = bytearray()
        self._rcv_cond = threading.Condition()

        # frames
        self._frame_id = 0
        self._frags = {}            # frame_id -> {idx: data}
        self._frame_q = []
        self._frame_cond = threading.Condition()

        self._sock.settimeout(RTO / 3.0)
        self._rx = threading.Thread(target=self._recv_loop, daemon=True)
        self._rx.start()

    # -- reliable control (byte stream) ------------------------------------
    def send_stream(self, data):
        with self._lock:
            self._snd_pending.extend(data)
            self._pump_locked()

    def _pump_locked(self):
        while self._snd_pending and (self._snd_next - self._snd_base) < WINDOW:
            chunk = bytes(self._snd_pending[:MAX_PAYLOAD])
            del self._snd_pending[:MAX_PAYLOAD]
            seq = self._snd_next
            pkt = _CTRL.pack(TAG_CTRL, seq) + chunk
            self._snd_buf[seq] = [pkt, time.time()]
            self._snd_next += 1
            self._safe_send(pkt)

    def recv_stream(self, n):
        with self._rcv_cond:
            while not self._rcv_stream and not self._closed:
                self._rcv_cond.wait(0.5)
            if not self._rcv_stream and self._closed:
                raise ConnectionError("udp transport closed")
            out = bytes(self._rcv_stream[:n])
            del self._rcv_stream[:n]
            return out

    # -- unreliable frames --------------------------------------------------
    def send_frame(self, data):
        with self._lock:
            fid = self._frame_id
            self._frame_id += 1
        count = max(1, (len(data) + MAX_PAYLOAD - 1) // MAX_PAYLOAD)
        for idx in range(count):
            chunk = data[idx * MAX_PAYLOAD:(idx + 1) * MAX_PAYLOAD]
            self._safe_send(_FRAG.pack(TAG_FRAME, fid, idx, count) + chunk)

    def recv_frame(self, timeout=None):
        with self._frame_cond:
            if not self._frame_q:
                self._frame_cond.wait(timeout)
            if self._frame_q:
                return self._frame_q.pop(0)
            return None

    # -- socket-like adapter for the reliable channel ----------------------
    def control_socket(self):
        return _StreamSocket(self)

    # -- internals ----------------------------------------------------------
    def _safe_send(self, pkt):
        try:
            self._sock.sendto(pkt, self._peer)
        except OSError:
            pass

    def _recv_loop(self):
        while not self._closed:
            try:
                data, _addr = self._sock.recvfrom(65535)
            except OSError:
                if self._closed:
                    break
                self._retransmit()
                continue
            if not data:
                continue
            tag = data[0]
            if tag == TAG_CTRL:
                self._on_ctrl(data)
            elif tag == TAG_ACK:
                self._on_ack(data)
            elif tag == TAG_FRAME:
                self._on_frag(data)
            # TAG_PUNCH / unknown: ignore
            self._retransmit()

    def _retransmit(self):
        now = time.time()
        with self._lock:
            for seq, entry in self._snd_buf.items():
                if now - entry[1] > RTO:
                    entry[1] = now
                    self._safe_send(entry[0])
            self._pump_locked()

    def _on_ctrl(self, data):
        _, seq = _CTRL.unpack_from(data)
        payload = data[_CTRL.size:]
        deliver = False
        with self._rcv_cond:
            if seq == self._rcv_next:
                self._rcv_stream.extend(payload)
                self._rcv_next += 1
                while self._rcv_next in self._rcv_buf:
                    self._rcv_stream.extend(self._rcv_buf.pop(self._rcv_next))
                    self._rcv_next += 1
                deliver = True
            elif seq > self._rcv_next and seq < self._rcv_next + WINDOW:
                self._rcv_buf[seq] = payload
            # seq < rcv_next: duplicate, drop
            if deliver:
                self._rcv_cond.notify_all()
            ack = self._rcv_next
        self._safe_send(_ACK.pack(TAG_ACK, ack))

    def _on_ack(self, data):
        _, cum = _ACK.unpack_from(data)
        with self._lock:
            if cum > self._snd_base:
                for seq in [s for s in self._snd_buf if s < cum]:
                    del self._snd_buf[seq]
                self._snd_base = cum
                self._pump_locked()

    def _on_frag(self, data):
        _, fid, idx, count = _FRAG.unpack_from(data)
        chunk = data[_FRAG.size:]
        frags = self._frags.setdefault(fid, {})
        frags[idx] = chunk
        if len(frags) == count:
            frame = b"".join(frags[i] for i in range(count))
            self._frags.pop(fid, None)
            # drop any older, still-incomplete frames
            for old in [f for f in self._frags if f < fid]:
                del self._frags[old]
            with self._frame_cond:
                self._frame_q.append(frame)
                self._frame_cond.notify_all()
        elif len(self._frags) > KEEP_FRAMES:
            for old in sorted(self._frags)[:-KEEP_FRAMES]:
                del self._frags[old]

    def close(self):
        self._closed = True
        with self._rcv_cond:
            self._rcv_cond.notify_all()
        with self._frame_cond:
            self._frame_cond.notify_all()
        try:
            self._sock.close()
        except OSError:
            pass


class _StreamSocket:
    """Minimal socket-like wrapper so protocol.send_msg / recv_msg run over the
    reliable control channel."""

    def __init__(self, transport):
        self._t = transport

    def sendall(self, data):
        self._t.send_stream(data)

    def recv(self, n):
        return self._t.recv_stream(n)

    def close(self):
        self._t.close()
