"""Cross-platform LAN remote-control tool (any platform controls any platform).

Modules:
    protocol      - length-prefixed binary framing over TCP
    config        - shared defaults and the ServerConfig dataclass
    input_handler - translate input events into pynput mouse/keyboard actions
    discovery     - UDP broadcast so controllers can list agents by name + IP
    server        - the agent (screen capture + input injection)
    client        - the controller (Tkinter GUI)
    clientutil    - pure helpers for the client (coordinate + key mapping)
    winhook       - Windows low-level keyboard hook (full combo capture)
    macperms      - macOS Screen Recording / Accessibility permission checks
"""

__version__ = "1.1.0"
