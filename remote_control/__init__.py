"""LAN remote-control tool: control a Mac from a Windows PC.

Modules:
    protocol      - length-prefixed binary framing over TCP
    config        - shared defaults and the ServerConfig dataclass
    input_handler - translate input events into pynput mouse/keyboard actions
    server        - the Mac-side agent (screen capture + input injection)
    client        - the Windows-side controller (Tkinter GUI)
    clientutil    - pure helpers for the client (coordinate + key mapping)
"""

__version__ = "1.0.0"
