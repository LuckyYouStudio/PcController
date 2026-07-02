"""macOS permission helpers — Screen Recording + Accessibility.

macOS never lets an app grant itself these permissions: the user must approve
them in System Settings. What an app *can* do is detect what's missing, trigger
the system prompt, and jump straight to the right Settings pane — turning a hunt
into one or two clicks. On non-macOS these are no-ops that report "granted".

Notes:
- Screen Recording: a newly granted app only starts capturing other windows
  after it is fully quit and relaunched. ``request_screen_recording`` registers
  the app in the list and shows the prompt the first time it runs.
- Accessibility: ``request_accessibility`` prompts and (with prompt=True) opens
  the pane; injection may start working without a restart once toggled on.
"""

import subprocess
import sys

IS_MAC = sys.platform == "darwin"

# System Settings deep links (work on Monterey through the current macOS).
_URL_SCREEN = ("x-apple.systempreferences:com.apple.preference.security"
               "?Privacy_ScreenCapture")
_URL_ACCESS = ("x-apple.systempreferences:com.apple.preference.security"
               "?Privacy_Accessibility")


def has_screen_recording():
    """True if this process may capture other apps' windows (or non-macOS)."""
    if not IS_MAC:
        return True
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True  # can't determine -> don't block the user


def request_screen_recording():
    """Show the Screen Recording prompt / register the app. Returns current grant."""
    if not IS_MAC:
        return True
    try:
        import Quartz
        return bool(Quartz.CGRequestScreenCaptureAccess())
    except Exception:
        return False


def has_accessibility():
    """True if this process is trusted to inject input (or non-macOS)."""
    if not IS_MAC:
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True


def request_accessibility(prompt=True):
    """Check Accessibility trust; with ``prompt`` show the system prompt."""
    if not IS_MAC:
        return True
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt)
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        return has_accessibility()


def open_screen_recording_settings():
    """Open System Settings at the Screen Recording pane."""
    _open(_URL_SCREEN)


def open_accessibility_settings():
    """Open System Settings at the Accessibility pane."""
    _open(_URL_ACCESS)


def _open(url):
    if not IS_MAC:
        return
    try:
        subprocess.Popen(["open", url])
    except Exception:
        pass


def status():
    """Return a dict of current grants (both True on non-macOS)."""
    return {
        "screen_recording": has_screen_recording(),
        "accessibility": has_accessibility(),
    }


def diagnose():
    """Detailed report: whether the macOS frameworks loaded + the grant state.

    Used by ``agent.py --check-perms`` to confirm a packaged .app can actually
    query permissions (i.e. pyobjc got bundled) rather than silently assuming
    everything is granted.
    """
    info = {"is_mac": IS_MAC, "quartz": False, "appservices": False,
            "screen_recording": None, "accessibility": None}
    if not IS_MAC:
        return info
    try:
        import Quartz
        info["quartz"] = True
        info["screen_recording"] = bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception as exc:
        info["quartz_err"] = repr(exc)
    try:
        from ApplicationServices import AXIsProcessTrusted
        info["appservices"] = True
        info["accessibility"] = bool(AXIsProcessTrusted())
    except Exception as exc:
        info["appservices_err"] = repr(exc)
    return info
