"""UDP LAN discovery so the controller can list agents by name + IP.

The agent runs a small responder that listens for a broadcast probe and
replies with its hostname and TCP port. The controller broadcasts the probe
and collects replies for a short window.

    controller  --(UDP broadcast PROBE)-->  all agents on the LAN
    controller  <--(UDP reply name+port)--  each agent
"""

import json
import socket
import time

DISCOVERY_PORT = 50506
PROBE = b"PCCTRL_PROBE_v1"
REPLY_PREFIX = b"PCCTRL_REPLY_v1:"


def run_responder(tcp_port, stop_event, name=None, discovery_port=DISCOVERY_PORT):
    """Agent side: answer discovery probes until ``stop_event`` is set."""
    if name is None:
        name = socket.gethostname()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass
    try:
        sock.bind(("", discovery_port))
    except OSError as exc:
        print(f"[discovery] responder disabled: {exc}")
        return
    sock.settimeout(0.5)
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break
        if data.strip() == PROBE:
            info = {"name": name, "port": int(tcp_port)}
            payload = REPLY_PREFIX + json.dumps(info).encode("utf-8")
            try:
                sock.sendto(payload, addr)
            except OSError:
                pass
    sock.close()


def discover(timeout=1.5, discovery_port=DISCOVERY_PORT,
             broadcast_addr="255.255.255.255"):
    """Controller side: return a list of {name, ip, port} found on the LAN."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    sock.settimeout(0.3)
    try:
        sock.sendto(PROBE, (broadcast_addr, discovery_port))
    except OSError:
        pass

    found = {}
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        if data.startswith(REPLY_PREFIX):
            try:
                info = json.loads(data[len(REPLY_PREFIX):].decode("utf-8"))
            except ValueError:
                continue
            ip = addr[0]
            found[ip] = {
                "name": str(info.get("name", ip)),
                "ip": ip,
                "port": int(info.get("port", 0)),
            }
    sock.close()
    return sorted(found.values(), key=lambda m: m["name"].lower())
