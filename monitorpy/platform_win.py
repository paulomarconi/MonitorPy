"""Small, independent Windows platform hooks: DPI awareness and the single-instance guard."""
import ctypes
from ctypes import wintypes

from monitorpy.logutil import log


def enable_dpi_awareness():
    """Make the process DPI-aware (system DPI) so Tk renders crisply instead of being
    bitmap-stretched by Windows. Must run before any window is created. The popup lives on
    the primary monitor, so system awareness is enough (Tk 8.6 cannot re-scale per monitor)."""
    try:
        # 1 = PROCESS_SYSTEM_DPI_AWARE. Fails with E_ACCESSDENIED if already set (e.g. by a manifest).
        result = ctypes.windll.shcore.SetProcessDpiAwareness(1) & 0xFFFFFFFF  # HRESULT as unsigned
        if result not in (0, 0x80070005):
            log.warning("SetProcessDpiAwareness returned 0x%08X", result)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7 fallback
        except (AttributeError, OSError) as e:
            log.warning("Could not enable DPI awareness: %s", e)


ERROR_ALREADY_EXISTS = 183
_instance_mutex = None  # keeps the mutex handle alive for the life of the process


def acquire_single_instance(name="Local\\MonitorPy-SingleInstance"):
    """Return True if this is the only running instance (per Windows session), False otherwise.
    The named mutex is released by Windows when the process exits, even after a crash."""
    global _instance_mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        # Could not create it at all: do not block the user from starting the app.
        log.warning("CreateMutexW failed (%s); skipping single-instance check", ctypes.WinError(ctypes.get_last_error()))
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True
