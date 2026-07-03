"""Shared defaults and the server configuration object."""

from dataclasses import dataclass

DEFAULT_PORT = 50505
DEFAULT_RELAY_PORT = 50510    # public relay/rendezvous server port
DEFAULT_SERVER = "47.239.121.107:50510"   # our deployed rendezvous server
DEFAULT_PASSWORD = "changeme"
DEFAULT_FPS = 15
DEFAULT_QUALITY = 60          # JPEG quality 1-95
DEFAULT_SCALE = 1.0           # server-side downscale factor (0 < scale <= 1)
DEFAULT_MONITOR = 1           # mss monitor index (1 = primary, 0 = all-in-one)


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    password: str = DEFAULT_PASSWORD
    fps: int = DEFAULT_FPS
    quality: int = DEFAULT_QUALITY
    scale: float = DEFAULT_SCALE
    monitor: int = DEFAULT_MONITOR
    input_enabled: bool = True  # set False in tests to avoid moving the mouse
    clipboard: bool = True      # bidirectional clipboard text sync
