#!/usr/bin/env python3
"""
MonitorPy - Monitor Control System Tray Application for Windows 11/10
Requires: pip install pystray pillow monitorcontrol pyinstaller 
"""

import tkinter as tk
from tkinter import ttk, messagebox
import pystray
from PIL import Image, ImageDraw
import threading
import sys
import os
import json
import logging
import queue
import winreg
import ctypes
from ctypes import wintypes
from logging.handlers import RotatingFileHandler

try:
    import sv_ttk
    import darkdetect
except ImportError:
    sv_ttk = None
    darkdetect = None

log = logging.getLogger("MonitorPy")


def setup_logging():
    """Log to monitorpy.log in the MonitorPy folder under %APPDATA% (and stderr when a console exists)."""
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    appdata = os.environ.get('APPDATA')
    if appdata:
        try:
            log_dir = os.path.join(appdata, "MonitorPy")
            os.makedirs(log_dir, exist_ok=True)
            fh = RotatingFileHandler(os.path.join(log_dir, "monitorpy.log"),
                                     maxBytes=256 * 1024, backupCount=1, encoding="utf-8")
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError:
            pass
    if sys.stderr:  # None in a windowed PyInstaller build
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


setup_logging()

# EDID/PnP manufacturer IDs -> brand. Only codes that are known to be correct are listed;
# anything else is shown as its raw 3-letter ID rather than a guessed name.
MFG_CODES = {
    'ACI': 'ASUS',  # registered to Ancor Communications, used by ASUS monitors
    'ACR': 'Acer', 'AOC': 'AOC', 'APP': 'Apple', 'AUO': 'AU Optronics', 'AUS': 'ASUS',
    'BNQ': 'BenQ', 'CMN': 'Chimei Innolux', 'CMO': 'Chi Mei', 'CPQ': 'Compaq',
    'DEL': 'Dell', 'ENC': 'Eizo', 'FUJ': 'Fujitsu', 'GBT': 'Gigabyte', 'GSM': 'LG',
    'HKC': 'HKC', 'HPN': 'HP', 'HWP': 'HP', 'IBM': 'IBM', 'IVM': 'Iiyama',
    'LEN': 'Lenovo', 'LGD': 'LG Display', 'LPL': 'LG Philips', 'MEI': 'Panasonic',
    'MSI': 'MSI', 'NEC': 'NEC', 'PHL': 'Philips', 'SAM': 'Samsung',
    'SDC': 'Samsung Display', 'SEC': 'Seiko Epson', 'SHP': 'Sharp', 'SNY': 'Sony',
    'VSC': 'ViewSonic', 'XMI': 'Xiaomi', 'ZOW': 'Zowie',
}


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


def manufacturer_from_device_id(device_id):
    """'MONITOR\\DEL4240\\{guid}\\0000' -> 'Dell'. Unknown IDs return the raw 3-letter code."""
    parts = (device_id or "").split("\\")
    if len(parts) < 2 or parts[0].upper() != "MONITOR" or len(parts[1]) < 3:
        return None
    code = parts[1][:3].upper()
    return MFG_CODES.get(code, code)


def get_monitor_manufacturers():
    """Manufacturer of each *connected* physical monitor, in the same order as
    monitorcontrol.get_monitors() (both walk EnumDisplayMonitors, one entry per physical
    monitor). Uses the active monitor's PnP device ID, so stale registry entries of
    monitors that were once connected cannot be mixed in. None where unknown."""
    user32, dxva2 = ctypes.windll.user32, ctypes.windll.dxva2
    hmonitors = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    if not user32.EnumDisplayMonitors(0, 0, proc(lambda h, dc, r, lp: hmonitors.append(h) or True), 0):
        return []
    result = []
    for hmonitor in hmonitors:
        count = wintypes.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)):
            continue
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        have_info = user32.GetMonitorInfoW(hmonitor, ctypes.byref(info))
        for j in range(count.value):
            device = _DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(device)
            if have_info and user32.EnumDisplayDevicesW(info.szDevice, j, ctypes.byref(device), 0):
                result.append(manufacturer_from_device_id(device.DeviceID))
            else:
                result.append(None)
    return result


try:
    from monitorcontrol import get_monitors
except ImportError:
    log.error("monitorcontrol is not installed")
    messagebox.showerror("Missing Dependency", "Please install monitorcontrol: pip install monitorcontrol")
    sys.exit(1)

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


class DdcWorker:
    """Runs every DDC/CI call on one background thread.

    DDC/CI transactions take 50 ms to several seconds, so they must never run on the Tk
    thread. Writes are coalesced: if a newer value for the same (monitor, feature) arrives
    before the previous one was sent, only the newest is written, so dragging a slider
    cannot build up a backlog. Results are collected in a queue that the Tk thread polls.
    """

    def __init__(self):
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._lock = threading.Lock()
        self._pending = {}      # key -> (monitor, feature, value); newest value wins
        self._outstanding = 0   # jobs queued or running
        self._thread = threading.Thread(target=self._loop, name="ddc-worker", daemon=True)
        self._thread.start()

    def submit_write(self, key, monitor, feature, value):
        with self._lock:
            queued = key in self._pending
            self._pending[key] = (monitor, feature, value)
            if not queued:
                self._outstanding += 1
        if not queued:
            self._jobs.put(("write", key))

    def submit_call(self, name, fn):
        """Run fn() on the worker; the result arrives as (name, value, error)."""
        with self._lock:
            self._outstanding += 1
        self._jobs.put(("call", name, fn))

    def is_pending(self, key):
        with self._lock:
            return key in self._pending

    def busy(self):
        with self._lock:
            return self._outstanding > 0 or not self._results.empty()

    def poll(self):
        """Return all results produced so far (non-blocking)."""
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                return out

    def shutdown(self, timeout=2.0):
        """Let queued writes finish (up to timeout seconds), then stop the thread."""
        self._jobs.put(None)
        self._thread.join(timeout)

    def _loop(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                if job[0] == "write":
                    self._do_write(job[1])
                else:
                    _, name, fn = job
                    try:
                        self._results.put((name, fn(), None))
                    except Exception as e:
                        log.exception("DDC job %s failed", name)
                        self._results.put((name, None, e))
            finally:
                with self._lock:
                    self._outstanding -= 1

    def _do_write(self, key):
        with self._lock:
            monitor, feature, value = self._pending.pop(key)
        try:
            with monitor:
                if feature == "brightness":
                    monitor.set_luminance(value)
                else:
                    monitor.set_contrast(value)
            ok = True
        except Exception as e:
            log.error("Failed to set %s to %d on monitor %d: %s", feature, value, key[0], e)
            ok = False
        self._results.put(("write", (key, value, ok), None))


class MonitorController:
    def __init__(self):
        self.monitor = None
        self.root = None
        self.brightness_var = None
        self.contrast_var = None
        self.tray_icon = None
        self.current_brightness = 50
        self.current_contrast = 50
        self.monitor_infos = []
        self._click_watch_id = None
        self.edit_win = None
        self.monitor_connected = False
        self.discovering = False
        self.selected_monitor_index = 0
        self.preset_1 = {"brightness": 100, "contrast": 70}
        self.preset_2 = {"brightness": 30, "contrast": 50}
        self.theme = "system"
        self._worker = DdcWorker()
        self._confirmed = {}    # (monitor index, feature) -> last value the monitor accepted
        self._pump_id = None
        self._hotkeys = None
        self.ui_scale = 1.0
        self.load_presets()

    def start_hotkeys(self):
        self._hotkeys = HotkeyListener(HOTKEYS, self._on_hotkey, self._on_hotkeys_failed)
        self._hotkeys.start()

    def _on_hotkey(self, method_name):
        """Listener thread -> Tk thread."""
        try:
            self.root.after(0, getattr(self, method_name))
        except Exception as e:
            log.warning("Could not dispatch hotkey %s: %s", method_name, e)

    def _on_hotkeys_failed(self, labels):
        try:
            self.root.after(0, lambda: messagebox.showwarning(
                "MonitorPy hotkeys",
                "These hotkeys could not be registered (another program already uses them):\n\n"
                + ", ".join(labels)))
        except Exception as e:
            log.warning("Could not report hotkey failure: %s", e)

    def start_discovery(self):
        """Detect monitors on the DDC worker thread; the UI is filled in when it finishes."""
        log.info("Discovering monitors...")
        self.discovering = True
        self._refresh_monitor_list()
        self._worker.submit_call("discovery", self._probe_monitors)
        self._ensure_pump()

    def _probe_monitors(self):
        """Blocking: enumerate monitors and read their names/values. Runs on the worker
        thread only, so it must not touch Tk or controller state."""
        monitors = get_monitors()
        try:
            manufacturers = get_monitor_manufacturers()
        except Exception as e:
            log.warning("Could not read monitor manufacturers: %s", e)
            manufacturers = []
        if len(manufacturers) != len(monitors):
            # Never guess: a wrong brand is worse than none.
            log.warning("Manufacturer list (%d) does not match monitor list (%d); ignoring it",
                        len(manufacturers), len(monitors))
            manufacturers = [None] * len(monitors)

        infos = []
        for i, monitor in enumerate(monitors):
            info = {"monitor": monitor, "name": f"Monitor {i+1}", "brightness": 50, "contrast": 50,
                    "manufacturer": manufacturers[i], "supported": {}}
            # Read each feature in its own transaction: a monitor that cannot report one
            # (many do not support contrast, or return no capabilities string) may still
            # work for the others.
            try:
                with monitor:
                    caps = self._probe_feature(i, "capabilities", monitor.get_vcp_capabilities)
                    if isinstance(caps, dict) and caps.get("model"):
                        info["name"] = caps["model"]
                    for feature, reader in (("brightness", monitor.get_luminance),
                                            ("contrast", monitor.get_contrast)):
                        value = self._probe_feature(i, feature, reader)
                        info["supported"][feature] = value is not None
                        if value is not None:
                            info[feature] = value
            except Exception as e:
                log.warning("Monitor %d: could not open DDC/CI session: %s", i, e)
            supported = info["supported"]
            if not any(supported.values()):
                info["name"] += " (No DDC/CI)"
            else:
                missing = [f for f in ("brightness", "contrast") if not supported.get(f)]
                if missing:
                    info["name"] += f" (no {' or '.join(missing)} control)"
            log.info("Monitor %d: %s | manufacturer=%s", i, info["name"], info["manufacturer"])
            infos.append(info)
        return infos

    @staticmethod
    def _probe_feature(index, what, reader):
        try:
            return reader()
        except Exception as e:
            log.warning("Monitor %d: reading %s failed: %s", index, what, e)
            return None

    def _finish_discovery(self, infos, error):
        """Tk thread: adopt the discovery result and refresh the UI."""
        self.discovering = False
        if error:
            infos = []
            messagebox.showerror("Error", f"Failed to detect monitors: {error}")
        self.monitor_infos = infos
        self.selected_monitor_index = 0
        self.monitor = infos[0]["monitor"] if infos else None
        self.monitor_connected = bool(infos)
        self._confirmed = {(i, f): info[f] for i, info in enumerate(infos) for f in ("brightness", "contrast")}
        if infos:
            self.current_brightness = infos[0]["brightness"]
            self.current_contrast = infos[0]["contrast"]
        self._refresh_monitor_list()
        self.update_controls()
        if self.tray_icon:
            self.tray_icon.title = f"Monitor Control - {'Connected' if self.monitor_connected else 'Disconnected'}"
            self.tray_icon.menu = self.create_tray_menu()

    # --- worker results are polled on the Tk thread, and only while work is outstanding ---
    def _ensure_pump(self):
        if self.root and self._pump_id is None:
            self._pump_id = self.root.after(30, self._pump)

    def _pump(self):
        self._pump_id = None
        for name, value, error in self._worker.poll():
            try:
                if name == "write":
                    self._on_write_result(*value)
                elif name == "discovery":
                    self._finish_discovery(value, error)
            except Exception:
                log.exception("Error handling %s result", name)
        if self._worker.busy():
            self._ensure_pump()

    def _on_write_result(self, key, value, ok):
        idx, feature = key
        if ok:
            self._confirmed[key] = value
            return
        # The monitor rejected the value: put the UI back to the last confirmed one,
        # unless a newer value is already on its way (that one will decide).
        confirmed = self._confirmed.get(key)
        if confirmed is None or self._worker.is_pending(key) or idx >= len(self.monitor_infos):
            return
        self.monitor_infos[idx][feature] = confirmed
        if idx == self.selected_monitor_index:
            setattr(self, f"current_{feature}", confirmed)
            self.update_controls()

    def _px(self, pixels):
        """Scale a 96-DPI pixel size to the current display scaling."""
        return int(round(pixels * self.ui_scale))

    def get_config_path(self):
        appdata = os.environ.get('APPDATA')
        if appdata:
            config_dir = os.path.join(appdata, "MonitorPy")
            os.makedirs(config_dir, exist_ok=True)
            return os.path.join(config_dir, "presets.json")
        return "presets.json"

    def load_presets(self):
        config_file = self.get_config_path()
        if os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    data = json.load(f)
                    if "preset_1" in data:
                        self.preset_1.update(data["preset_1"])
                    if "preset_2" in data:
                        self.preset_2.update(data["preset_2"])
                    if "theme" in data:
                        self.theme = data["theme"]
            except Exception as e:
                log.error("Failed to load presets: %s", e)

    def save_presets_to_file(self):
        config_file = self.get_config_path()
        try:
            with open(config_file, "w") as f:
                json.dump({"preset_1": self.preset_1, "preset_2": self.preset_2, "theme": self.theme}, f)
        except Exception as e:
            log.error("Failed to save presets: %s", e)

    def apply_theme(self, theme_name=None):
        if not sv_ttk:
            return
        if theme_name is None:
            theme_name = self.theme
        
        target_theme = theme_name
        if target_theme == "system" and darkdetect:
            target_theme = darkdetect.theme().lower()
            
        if target_theme not in ["dark", "light"]:
            target_theme = "dark"
            
        try:
            sv_ttk.set_theme(target_theme)
        except Exception as e:
            log.error("Failed to apply theme: %s", e)

    def change_theme(self, theme_name):
        self.theme = theme_name
        self.save_presets_to_file()
        if self.root and threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.apply_theme(theme_name))
        else:
            self.apply_theme(theme_name)
        if self.tray_icon:
            try:
                self.tray_icon.menu = self.create_tray_menu()
            except Exception:
                pass

    # --- Autostart (Windows) support: HKCU Run key ---
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    RUN_VALUE = "MonitorPy"

    def _autostart_command(self):
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}"'
        pythonw = sys.executable
        # prefer pythonw to avoid a console when launching the script
        if pythonw.lower().endswith('python.exe'):
            pythonw = pythonw[:-len('python.exe')] + 'pythonw.exe'
        return f'"{pythonw}" "{os.path.abspath(sys.argv[0])}"'

    def _legacy_startup_files(self):
        """Startup-folder launchers created by older versions (.lnk / .vbs)."""
        appdata = os.environ.get('APPDATA')
        if not appdata:
            return []
        startup = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
        return [os.path.join(startup, 'MonitorPy.lnk'), os.path.join(startup, 'MonitorPy.vbs')]

    def _remove_legacy_startup_files(self):
        for path in self._legacy_startup_files():
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                log.warning("Could not remove legacy startup file %s: %s", path, e)

    def is_autostart_enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                winreg.QueryValueEx(key, self.RUN_VALUE)
            return True
        except FileNotFoundError:
            return any(os.path.exists(p) for p in self._legacy_startup_files())
        except OSError as e:
            log.error("Could not read autostart setting: %s", e)
            return False

    def enable_autostart(self):
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                winreg.SetValueEx(key, self.RUN_VALUE, 0, winreg.REG_SZ, self._autostart_command())
        except OSError as e:
            log.error("Could not enable autostart: %s", e)
            return False
        self._remove_legacy_startup_files()
        return True

    def disable_autostart(self):
        ok = True
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.RUN_VALUE)
        except FileNotFoundError:
            pass
        except OSError as e:
            log.error("Could not disable autostart: %s", e)
            ok = False
        self._remove_legacy_startup_files()
        return ok and not any(os.path.exists(p) for p in self._legacy_startup_files())

    def toggle_autostart(self, icon=None, item=None):
        # Ensure runs on Tk main thread when invoked from tray
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, lambda: self.toggle_autostart(None, None))
            return
        try:
            enabling = not self.is_autostart_enabled()
            ok = self.enable_autostart() if enabling else self.disable_autostart()
            if not ok:
                messagebox.showerror("Autostart", f"Could not {'enable' if enabling else 'disable'} autostart. See the log for details.")
        finally:
            # Refresh tray menu to update checked state
            if self.tray_icon:
                try:
                    self.tray_icon.menu = self.create_tray_menu()
                except Exception:
                    pass

    def get_monitor_names(self):
        """Get monitor names with optional manufacturer."""
        return [info["name"] for info in self.monitor_infos]
    
    def get_monitor_display_names(self):
        """Formatted monitor names, prefixed with the manufacturer unless the name already has it."""
        names = []
        for info in self.monitor_infos:
            maker, name = info["manufacturer"], info["name"]
            if maker and not name.lower().startswith(maker.lower()):
                name = f"{maker} {name}"
            names.append(name)
        return names

    # --- get/set brightness and contrast with error handling ---
    def get_brightness(self):
        return self.current_brightness

    def _set_feature(self, feature, value):
        """Optimistically apply brightness/contrast to the selected monitor and queue the
        DDC/CI write. Returns False if there is no monitor to write to. If the monitor
        rejects the value, _on_write_result reverts the cached value and the UI."""
        if not self.monitor or not 0 <= self.selected_monitor_index < len(self.monitor_infos):
            log.warning("Cannot set %s: no monitor selected", feature)
            return False
        value = int(value)
        setattr(self, f"current_{feature}", value)
        self.monitor_infos[self.selected_monitor_index][feature] = value
        self._worker.submit_write((self.selected_monitor_index, feature), self.monitor, feature, value)
        self._ensure_pump()
        return True

    def set_brightness(self, value):
        return self._set_feature("brightness", value)

    def get_contrast(self):
        return self.current_contrast

    def set_contrast(self, value):
        return self._set_feature("contrast", value)

    # --- Keyboard shortcut helpers for brightness and contrast ---
    def _clamp(self, v, lo=0, hi=100):
        try:
            v = int(v)
        except Exception:
            return lo
        return max(lo, min(hi, v))

    def change_brightness_by(self, delta):
        cur = self._clamp(self.get_brightness())
        new = self._clamp(cur + int(delta))
        self.set_brightness(new)
        self.update_controls()

    def increase_brightness(self, event=None):
        self.change_brightness_by(getattr(self, 'brightness_step', 5))

    def decrease_brightness(self, event=None):
        self.change_brightness_by(-getattr(self, 'brightness_step', 5))

    def change_contrast_by(self, delta):
        cur = self._clamp(self.get_contrast())
        new = self._clamp(cur + int(delta))
        self.set_contrast(new)
        self.update_controls()

    def increase_contrast(self, event=None):
        self.change_contrast_by(getattr(self, 'contrast_step', 5))

    def decrease_contrast(self, event=None):
        self.change_contrast_by(-getattr(self, 'contrast_step', 5))

    def create_image(self, width, height, color1, color2):
        image = Image.new('RGB', (width, height), color1)
        dc = ImageDraw.Draw(image)
        # Draw monitor frame (white rectangle border)
        frame_x1, frame_y1 = 0, 0
        frame_x2, frame_y2 = width - 1, height - 1
        dc.rectangle([frame_x1, frame_y1, frame_x2, frame_y2], fill=None, outline='white', width=3)
        # Draw inner monitor bezel
        bezel_x1, bezel_y1 = 2, 2
        bezel_x2, bezel_y2 = width - 3, height - 3
        dc.rectangle([bezel_x1, bezel_y1, bezel_x2, bezel_y2], fill=None, outline='white', width=1)
        # Draw diagonally split rectangle - left side white, right side black
        x1, y1 = 4, 4
        x2, y2 = width - 5, height - 5
        # Left/top-left triangle: white
        dc.polygon([(x1, y1), (x2, y1), (x1, y2)], fill=color2)
        # Right/bottom-right triangle: black
        dc.polygon([(x2, y1), (x2, y2), (x1, y2)], fill=color1)
        # Draw outline
        dc.rectangle([x1, y1, x2, y2], fill=None, outline='white', width=1)
        return image

    def show_control_window(self, icon=None, item=None):
        """Toggle the control window visibility and position it near the tray icon"""
        # If invoked from pystray's thread, schedule on Tk main thread to avoid freezes
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, lambda: self.show_control_window(None, None))
            return
        if self.root and self.root.winfo_exists() and self.root.state() != 'withdrawn':
            self.hide_window()
            return

        if not self.root or not self.root.winfo_exists():
            self.create_control_window()
        else:
            self.position_near_tray()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._start_click_watch()

    def position_near_tray(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        try:
            import ctypes
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long),
                            ("top", ctypes.c_long),
                            ("right", ctypes.c_long),
                            ("bottom", ctypes.c_long)]
            SPI_GETWORKAREA = 0x0030
            rect = RECT()
            ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            if rect.top > 0:
                x = screen_width - width - self._px(20)
                y = rect.top + self._px(10)
            elif rect.bottom < screen_height:
                x = screen_width - width - self._px(20)
                y = rect.bottom - height - self._px(10)
            elif rect.left > 0:
                x = rect.left + self._px(10)
                y = screen_height - height - self._px(60)
            elif rect.right < screen_width:
                x = rect.right - width - self._px(10)
                y = screen_height - height - self._px(60)
            else:
                x = screen_width - width - self._px(20)
                y = screen_height - height - self._px(60)
        except Exception:
            x = screen_width - width - self._px(20)
            y = screen_height - height - self._px(60)
        self.root.geometry(f"+{x}+{y}")

    def _start_click_watch(self):
        """Start polling for outside clicks. Idempotent: at most one poll loop is ever scheduled."""
        self._stop_click_watch()
        if self.root:
            self._click_watch_id = self.root.after(100, self._outside_click_check)

    def _stop_click_watch(self):
        if self._click_watch_id is not None:
            try:
                self.root.after_cancel(self._click_watch_id)
            except Exception:
                pass
            self._click_watch_id = None

    def _outside_click_check(self):
        """Hide the control window when the left mouse button is pressed outside it
        (or outside the edit presets window). Polls only while the window is visible.
        Uses Win32 GetCursorPos and GetAsyncKeyState to detect clicks outside the app.
        """
        self._click_watch_id = None
        try:
            if not self.root or not self.root.winfo_exists() or self.root.state() == 'withdrawn':
                return  # not visible: stop polling; _start_click_watch() resumes it on show
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                mx, my = pt.x, pt.y

                rx = self.root.winfo_rootx()
                ry = self.root.winfo_rooty()
                rw = self.root.winfo_width()
                rh = self.root.winfo_height()

                inside = (rx <= mx <= rx + rw) and (ry <= my <= ry + rh)

                # Also check if click is inside the edit window
                edit_win = self.edit_win
                if edit_win and edit_win.winfo_exists() and edit_win.state() != 'withdrawn':
                    ex = edit_win.winfo_rootx()
                    ey = edit_win.winfo_rooty()
                    ew = edit_win.winfo_width()
                    eh = edit_win.winfo_height()
                    inside = inside or ((ex <= mx <= ex + ew) and (ey <= my <= ey + eh))

                # VK_LBUTTON == 0x01. GetAsyncKeyState returns negative if down (high bit set).
                lbutton = ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000

                # If left button is down and cursor is outside both windows, hide.
                if lbutton and not inside:
                    self.hide_window()
                    return
        except Exception as e:
            log.warning("Outside-click check failed: %s", e)
        self._start_click_watch()

    def create_control_window(self):
        self.root = tk.Tk()
        self.ui_scale = self.root.winfo_fpixels('1i') / 96.0
        self.root.title("MonitorPy")
        self.root.geometry(f"{self._px(320)}x{self._px(200)}")
        self.root.resizable(False, False)
        self.root.attributes('-toolwindow', True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.overrideredirect(True)
        self.apply_theme()

        main_frame = ttk.Frame(self.root, padding="8")
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X)
        ttk.Label(top_frame, text="MonitorPy", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(top_frame, text="Edit", command=self.open_edit_presets_window, width=len("Edit")).pack(side=tk.RIGHT, padx=1)
        ttk.Button(top_frame, text="Night", command=self.quick_preset_2, width=len("Preset 2")).pack(side=tk.RIGHT, padx=1)
        ttk.Button(top_frame, text="Day", command=self.quick_preset_1, width=len("Preset 1")).pack(side=tk.RIGHT, padx=1)

        ttk.Label(main_frame, text="Select Monitor").pack(anchor=tk.W)
        self.monitor_listbox = tk.Listbox(main_frame, height=3)
        self.monitor_listbox.pack(fill=tk.X, pady=3)
        self.monitor_listbox.bind("<<ListboxSelect>>", self.on_monitor_select)
        self._refresh_monitor_list()

        sliders_frame = ttk.Frame(main_frame)
        sliders_frame.pack(fill=tk.X, pady=8)

        ttk.Label(sliders_frame, text="Brightness (Ctrl+F10/F11)", font=("Segoe UI", 8)).grid(row=0, column=0, sticky=tk.W)
        brightness_value = getattr(self, 'last_brightness', self.get_brightness())
        self.brightness_var = tk.IntVar(value=brightness_value)
        self.brightness_scale = ttk.Scale(sliders_frame, from_=0, to=100, variable=self.brightness_var,
                                      command=self.update_brightness_label, orient=tk.HORIZONTAL, length=self._px(140))
        self.brightness_scale.grid(row=1, column=0, padx=(0, self._px(10)))
        self.brightness_scale.bind("<ButtonRelease-1>", self.on_brightness_release)
        self.brightness_scale.bind("<KeyRelease>", self.on_brightness_release)
        self.brightness_label = ttk.Label(sliders_frame, text=f"{self.brightness_var.get()}%")
        self.brightness_label.grid(row=2, column=0, sticky=tk.E)

        ttk.Label(sliders_frame, text="Contrast (Alt+F10/F11)", font=("Segoe UI", 8)).grid(row=0, column=1, sticky=tk.W)
        contrast_value = getattr(self, 'last_contrast', self.get_contrast())
        self.contrast_var = tk.IntVar(value=contrast_value)
        self.contrast_scale = ttk.Scale(sliders_frame, from_=0, to=100, variable=self.contrast_var,
                                    command=self.update_contrast_label, orient=tk.HORIZONTAL, length=self._px(140))
        self.contrast_scale.grid(row=1, column=1, padx=(0, self._px(10)))
        self.contrast_scale.bind("<ButtonRelease-1>", self.on_contrast_release)
        self.contrast_scale.bind("<KeyRelease>", self.on_contrast_release)
        self.contrast_label = ttk.Label(sliders_frame, text=f"{self.contrast_var.get()}%")
        self.contrast_label.grid(row=2, column=1, sticky=tk.E)

        self.position_near_tray()
        self.root.focus_force()
        # keyboard shortcut defaults and bindings
        self.brightness_step = getattr(self, 'brightness_step', 5)
        self.contrast_step = getattr(self, 'contrast_step', 5)
        self._start_click_watch()

    def _refresh_monitor_list(self):
        listbox = getattr(self, 'monitor_listbox', None)
        if not listbox:
            return
        listbox.delete(0, tk.END)
        names = self.get_monitor_display_names()
        if not names:
            listbox.insert(tk.END, "Detecting monitors..." if self.discovering else "No monitors found")
            return
        for name in names:
            listbox.insert(tk.END, name)
        listbox.select_set(self.selected_monitor_index)

    def on_monitor_select(self, event=None):
        sel = self.monitor_listbox.curselection()
        if sel and sel[0] < len(self.monitor_infos):
            self.selected_monitor_index = sel[0]
            info = self.monitor_infos[self.selected_monitor_index]
            self.monitor = info["monitor"]
            self.current_brightness = info["brightness"]
            self.current_contrast = info["contrast"]
            self.update_controls()
            if self.tray_icon:
                self.tray_icon.menu = self.create_tray_menu() 

    def update_controls(self):
        if not self.brightness_var or not self.contrast_var:
            return
        self.brightness_var.set(self.get_brightness())
        self.brightness_label.config(text=f"{self.brightness_var.get()}%")
        self.contrast_var.set(self.get_contrast())
        self.contrast_label.config(text=f"{self.contrast_var.get()}%")

    def update_brightness_label(self, value):
        # ttk.Scale calls this only for user interaction (not var.set), so it is safe to
        # apply live; the worker coalesces the writes.
        value = int(float(value))
        self.brightness_label.config(text=f"{value}%")
        if value != self.current_brightness:
            self.set_brightness(value)

    def on_brightness_release(self, event=None):
        if self.brightness_var.get() != self.current_brightness:
            self.set_brightness(self.brightness_var.get())

    def update_contrast_label(self, value):
        value = int(float(value))
        self.contrast_label.config(text=f"{value}%")
        if value != self.current_contrast:
            self.set_contrast(value)

    def on_contrast_release(self, event=None):
        if self.contrast_var.get() != self.current_contrast:
            self.set_contrast(self.contrast_var.get())

    def open_download_link(self):
        import webbrowser
        webbrowser.open("https://github.com/paulomarconi/MonitorPy")
            
    def hide_window(self):
        self._stop_click_watch()
        if self.root:
            self.root.withdraw()

    def open_edit_presets_window(self, icon=None, item=None):
        """Open a modal to edit preset values for Preset 1 and Preset 2"""
        # If invoked from the tray (pystray) thread, schedule on Tk main thread
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, lambda: self.open_edit_presets_window(None, None))
            return
        if not self.root:
            return

        # If an edit window already exists, bring it to front instead of
        # creating a new one (ensure single-instance editor window).
        existing = getattr(self, 'edit_win', None)
        try:
            if existing and existing.winfo_exists():
                try:
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                except Exception:
                    pass
                return
        except Exception:
            pass
        edit_win = tk.Toplevel(self.root)
        # remember the edit window so we can reuse it
        self.edit_win = edit_win
        edit_win.title("Edit Presets")
        edit_win.resizable(False, False)
        # Match main GUI style: no border, no title or close button
        try:
            edit_win.attributes('-toolwindow', True)
        except Exception:
            pass
        try:
            edit_win.overrideredirect(True)
        except Exception:
            pass
        # Only set transient to the main root if the root is visible.
        # If the root is withdrawn (hidden), making the Toplevel transient
        # to a hidden root can prevent it from being shown. Create the
        # Toplevel without transient in that case so it will appear.
        try:
            if self.root and self.root.winfo_exists() and self.root.state() != 'withdrawn':
                edit_win.transient(self.root)
        except Exception:
            pass
        frame = ttk.Frame(edit_win, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)

        # Preset 1
        ttk.Label(frame, text="Day").grid(row=0, column=0, columnspan=2, sticky=tk.W)
        reset_b_var = tk.IntVar(value=self.preset_1.get("brightness", 100))
        reset_c_var = tk.IntVar(value=self.preset_1.get("contrast", 70))
        ttk.Label(frame, text="Brightness:").grid(row=1, column=0, sticky=tk.W, pady=(4,0))
        reset_b_entry = ttk.Entry(frame, textvariable=reset_b_var, width=6)
        reset_b_entry.grid(row=1, column=1, sticky=tk.W, pady=(4,0))
        ttk.Label(frame, text="Contrast:").grid(row=2, column=0, sticky=tk.W)
        reset_c_entry = ttk.Entry(frame, textvariable=reset_c_var, width=6)
        reset_c_entry.grid(row=2, column=1, sticky=tk.W)

        # Preset 2
        ttk.Separator(frame).grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=6)
        ttk.Label(frame, text="Night").grid(row=4, column=0, columnspan=2, sticky=tk.W)
        p_b_var = tk.IntVar(value=self.preset_2.get("brightness", 30))
        p_c_var = tk.IntVar(value=self.preset_2.get("contrast", 50))
        ttk.Label(frame, text="Brightness:").grid(row=5, column=0, sticky=tk.W, pady=(4,0))
        p_b_entry = ttk.Entry(frame, textvariable=p_b_var, width=6)
        p_b_entry.grid(row=5, column=1, sticky=tk.W, pady=(4,0))
        ttk.Label(frame, text="Contrast:").grid(row=6, column=0, sticky=tk.W)
        p_c_entry = ttk.Entry(frame, textvariable=p_c_var, width=6)
        p_c_entry.grid(row=6, column=1, sticky=tk.W)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=(8,0))

        def save_presets():
            try:
                rb = int(reset_b_var.get())
                rc = int(reset_c_var.get())
                pb = int(p_b_var.get())
                pc = int(p_c_var.get())
            except Exception:
                messagebox.showerror("Invalid value", "Preset values must be integers 0-100")
                return
            for v in (rb, rc, pb, pc):
                if v < 0 or v > 100:
                    messagebox.showerror("Invalid range", "Values must be between 0 and 100")
                    return
            self.preset_1["brightness"] = rb
            self.preset_1["contrast"] = rc
            self.preset_2["brightness"] = pb
            self.preset_2["contrast"] = pc
            self.save_presets_to_file()
            # Update controls if visible
            if self.brightness_var:
                self.brightness_label.config(text=f"{self.brightness_var.get()}%")
            try:
                edit_win.destroy()
            finally:
                # clear reference so a new window can be created later
                self.edit_win = None

        ttk.Button(btn_frame, text="Save", command=save_presets, width=len("Save")).pack(side=tk.RIGHT, padx=4)
        def close_edit_window():
            try:
                edit_win.destroy()
            finally:
                self.edit_win = None
        ttk.Button(btn_frame, text="Cancel", command=close_edit_window, width=len("Cancel")).pack(side=tk.RIGHT)
        edit_win.protocol("WM_DELETE_WINDOW", close_edit_window)
        # Ensure main GUI is visible and positioned near tray so we can
        # place the editor window adjacent to it.
        try:
            if self.root.state() == 'withdrawn':
                self.root.deiconify()
                self._start_click_watch()
            self.position_near_tray()
            self.root.update_idletasks()
        except Exception:
            pass

        # Position editor to left or right of main GUI depending on GUI x
        try:
            self.root.update_idletasks()
            edit_win.update_idletasks()
            screen_width = self.root.winfo_screenwidth()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            ew = edit_win.winfo_reqwidth()
            eh = edit_win.winfo_reqheight()

            # If GUI is on right half of screen, put editor to the left
            if (rx + rw/2) > (screen_width / 2):
                ex = rx - ew - self._px(8)
            else:
                ex = rx + rw + self._px(8)

            # Align tops; clamp to screen
            ey = ry
            if ex < 0:
                ex = 8
            if ey < 0:
                ey = 8
            edit_win.geometry(f"+{ex}+{ey}")
            edit_win.deiconify()
            edit_win.lift()
            edit_win.focus_force()
        except Exception:
            pass

    def quit_app(self, icon=None, item=None):
        # Ensure quit runs on the Tk main thread when triggered from tray
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, lambda: self.quit_app(None, None))
            return
        if self._hotkeys:
            self._hotkeys.stop()
        self._worker.shutdown()  # flush queued writes (e.g. a preset applied just before exit)
        if self.root:
            self.root.quit()
        if self.tray_icon:
            self.tray_icon.stop()

    def create_tray_menu(self):
        monitor_names = self.get_monitor_display_names()
        current_monitor_name = monitor_names[self.selected_monitor_index] if monitor_names else "No monitor"
        
        theme_menu = pystray.Menu(
            pystray.MenuItem("Light", lambda icon, item: self.change_theme("light"), checked=lambda item: self.theme == "light", radio=True),
            pystray.MenuItem("Dark", lambda icon, item: self.change_theme("dark"), checked=lambda item: self.theme == "dark", radio=True),
            pystray.MenuItem("System Default", lambda icon, item: self.change_theme("system"), checked=lambda item: self.theme == "system", radio=True)
        )
        
        return pystray.Menu(
            pystray.MenuItem("MonitorPy v1.0.3 | Site", lambda icon, item: self.open_download_link(), enabled=True),
            pystray.MenuItem(f"Current Monitor: {current_monitor_name}", None, enabled=False),
            pystray.MenuItem("Show Controls", self.show_control_window, default=True),
            pystray.MenuItem("Edit Presets", lambda icon, item: self.open_edit_presets_window()),
            pystray.MenuItem("Day", self.quick_preset_1),
            pystray.MenuItem("Night", self.quick_preset_2),
            pystray.MenuItem("Theme", theme_menu),
            pystray.MenuItem(
                "Autostart on Windows startup",
                lambda icon, item: self.toggle_autostart(),
                checked=lambda item: self.is_autostart_enabled()
            ),
            pystray.MenuItem("Exit", self.quit_app)
        )

    def apply_preset(self, preset):
        # If invoked from non-main thread (tray), schedule on Tk thread
        if self.root and threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.apply_preset(preset))
            return
        self.set_brightness(preset.get("brightness", 100))
        self.set_contrast(preset.get("contrast", 70))
        self.update_controls()

    def quick_preset_1(self):
        self.apply_preset(self.preset_1)

    def quick_preset_2(self):
        self.apply_preset(self.preset_2)

    def run(self):
        icon_image = self.create_image(64, 64, 'black', 'white')
        self.tray_icon = pystray.Icon(
            "monitor_control",
            icon_image,
            "Monitor Control - Detecting monitors...",
            self.create_tray_menu()
        )
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
        self.create_control_window()
        self.hide_window()
        self.start_hotkeys()
        self.start_discovery()

        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit_app()

def main():
    enable_dpi_awareness()
    if not acquire_single_instance():
        log.info("Another instance is already running; exiting")
        messagebox.showinfo("MonitorPy", "MonitorPy is already running.\nLook for its icon in the system tray.")
        return 0
    try:
        MonitorController().run()
    except Exception as e:
        log.exception("Error starting application")
        messagebox.showerror("Error", f"Failed to start application: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
