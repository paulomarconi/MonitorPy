"""System-wide brightness/contrast hotkeys, via the Win32 RegisterHotKey API."""
import ctypes
import threading
from ctypes import wintypes

from monitorpy.logutil import log

MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
VK_F10, VK_F11 = 0x79, 0x7A

# (modifiers, virtual key, controller method, label). Ctrl+F10/F11 and Alt+F10/F11 rather than
# bare F10/F11: a global hotkey takes the key away from every other application, and bare F10
# (menu bar) / F11 (fullscreen) are used everywhere. Shift+F10 is the context-menu key.
HOTKEYS = [
    (MOD_CONTROL, VK_F10, "decrease_brightness", "Ctrl+F10"),
    (MOD_CONTROL, VK_F11, "increase_brightness", "Ctrl+F11"),
    (MOD_ALT, VK_F10, "decrease_contrast", "Alt+F10"),
    (MOD_ALT, VK_F11, "increase_contrast", "Alt+F11"),
]


class HotkeyListener:
    """System-wide hotkeys via RegisterHotKey.

    WM_HOTKEY is posted to the thread that registered the key, so this owns a small thread
    that only runs a message loop. on_hotkey(method_name) and on_failed(labels) are called
    on that thread; the caller must hand the work over to the Tk thread.
    """
    WM_HOTKEY, WM_QUIT, WM_USER, PM_NOREMOVE = 0x0312, 0x0012, 0x0400, 0x0000

    def __init__(self, hotkeys, on_hotkey, on_failed):
        self._hotkeys = hotkeys
        self._on_hotkey = on_hotkey
        self._on_failed = on_failed
        self._ready = threading.Event()
        self._thread_id = None
        self.failed = []
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)

    def start(self):
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        self._thread.join(1.0)

    def _run(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        registered = {}
        msg = wintypes.MSG()
        try:
            # Touch the message queue so PostThreadMessage (from stop) has somewhere to post.
            user32.PeekMessageW(ctypes.byref(msg), None, self.WM_USER, self.WM_USER, self.PM_NOREMOVE)
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            for hotkey_id, (mods, vk, action, label) in enumerate(self._hotkeys, 1):
                if user32.RegisterHotKey(None, hotkey_id, mods, vk):
                    registered[hotkey_id] = action
                else:
                    err = ctypes.WinError(ctypes.get_last_error())
                    log.warning("Could not register hotkey %s: %s", label, err)
                    self.failed.append(label)
        finally:
            self._ready.set()
        if self.failed:
            self._on_failed(self.failed)
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # 0 = WM_QUIT, -1 = error
                if msg.message == self.WM_HOTKEY and msg.wParam in registered:
                    self._on_hotkey(registered[msg.wParam])
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)
