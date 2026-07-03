"""Persistent per-machine remote ID (like a TeamViewer ID).

Stored in ``~/.pccontroller/id`` so a machine keeps the same ID across runs.
Controllers enter this ID (plus the relay address and password) to reach the
machine over the internet.
"""

import pathlib
import random


def _id_path():
    base = pathlib.Path.home() / ".pccontroller"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base / "id"


def _generate():
    return "".join(random.choices("0123456789", k=9))


def get_or_create_id():
    """Return this machine's stable 9-digit remote ID, creating it if needed."""
    path = _id_path()
    try:
        if path.exists():
            val = path.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    val = _generate()
    try:
        path.write_text(val, encoding="utf-8")
    except Exception:
        pass
    return val
