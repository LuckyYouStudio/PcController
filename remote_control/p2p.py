"""Establish a remote session: signaling -> hole punch -> direct P2P, with an
automatic relay fallback for strict NATs.

``establish`` returns a socket-like object that the existing PcController
protocol (auth / frames / input / clipboard via ``protocol.send_msg`` /
``recv_msg``) runs over unchanged:

* On a successful punch it is the P2P transport's **reliable control channel**
  (so everything, frames included, flows peer-to-peer and never touches the
  server — the whole point: the server carries no video).
* On punch failure it is a normal TCP socket **relayed** through the same
  server, so a connection is always possible.

(The transport also has an unreliable frame channel for smoother video on lossy
links; wiring that in is a later optimization — reliability here is fine and
keeps the protocol code unchanged.)
"""

import socket
import time

from . import holepunch
from . import relayclient
from . import signalingclient
from .udptransport import UDPTransport


def establish(server_host, server_port, role, session_id,
              timeout=20, punch_timeout=6):
    """Return ``(sock_like, mode)`` where mode is ``"p2p"`` or ``"relay"``.

    ``role`` is ``"agent"`` or ``"controller"``. For an agent this blocks up to
    ``timeout`` waiting for a controller to appear; raises ConnectionError if
    none does (or, for a controller, if the peer is offline and relay fails).
    """
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("", 0))
    try:
        peer_ep = signalingclient.rendezvous(
            server_host, server_port, role, session_id, udp, timeout)
    except ConnectionError:
        udp.close()
        raise

    if holepunch.punch(udp, peer_ep, timeout=punch_timeout):
        transport = UDPTransport(udp, peer_ep)
        return transport.control_socket(), "p2p"

    # punch failed (strict NAT) -> relay through the same server
    udp.close()
    if role == "agent":
        return relayclient.connect_agent(server_host, server_port, session_id), "relay"

    # controller: the agent may still be switching to relay mode, so retry
    last = None
    for _ in range(12):
        try:
            return (relayclient.connect_controller(
                server_host, server_port, session_id, timeout=3), "relay")
        except ConnectionError as exc:
            last = exc
            time.sleep(0.4)
    raise last or ConnectionError("relay fallback failed")
