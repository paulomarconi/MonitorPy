"""The application: tray icon, control window, and all the state that ties the pieces
in the other monitorpy modules together."""
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import pystray

from monitorpy import __version__
from monitorpy import autostart
from monitorpy.config import ConfigStore
from monitorpy.ddc import DdcWorker, probe_monitors
from monitorpy.hotkeys import HOTKEYS, HotkeyListener
from monitorpy.icon import load_tray_icon_image
from monitorpy.logutil import log
from monitorpy.monitors import get_display_signature, get_monitor_manufacturers

try:
    import sv_ttk
    import darkdetect
except ImportError:
    sv_ttk = None
    darkdetect = None

try:
    from monitorcontrol import get_monitors
except ImportError:
    log.error("monitorcontrol is not installed")
    messagebox.showerror("Missing Dependency", "Please install monitorcontrol: pip install monitorcontrol")
    sys.exit(1)


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
        self.config = ConfigStore()
        self._worker = DdcWorker()
        self._confirmed = {}    # (generation, monitor index, feature) -> last value the monitor accepted
        self._pump_id = None
        self._generation = 0    # bumped on every discovery; stale write results are ignored
        self._signature = None  # display configuration seen by the last discovery
        self._retry_on_open = False  # set when a detection failed
        self._hotkeys = None
        self.ui_scale = 1.0

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
        # Taken *before* probing: if the configuration changes meanwhile we merely refresh once more.
        self._signature = get_display_signature()
        self._refresh_monitor_list()
        self._worker.submit_call("discovery", self._probe_monitors)
        self._ensure_pump()

    def refresh_monitors(self, icon=None, item=None):
        """Re-detect monitors (tray menu). Ignored while a detection is already running."""
        if self.root and threading.current_thread() is not threading.main_thread():
            self.root.after(0, self.refresh_monitors)
            return
        if not self.discovering:
            self.start_discovery()

    def _check_display_change(self):
        """Cheap hot-plug check, run when the window is opened: refresh if monitors were
        plugged/unplugged since the last detection."""
        if self.discovering:
            return
        if self._retry_on_open:
            self._retry_on_open = False
            self.start_discovery()
            return
        if self._signature is None:
            return
        current = get_display_signature()
        if current is not None and current != self._signature:
            log.info("Display configuration changed; refreshing monitors")
            self.start_discovery()

    def _probe_monitors(self):
        """Runs on the worker thread; must not touch Tk or controller state."""
        return probe_monitors(get_monitors, get_monitor_manufacturers)

    def _finish_discovery(self, infos, error):
        """Tk thread: adopt the discovery result and refresh the UI."""
        self.discovering = False
        if error:
            messagebox.showerror("Error", f"Failed to detect monitors: {error}")
            infos = self.monitor_infos  # keep whatever worked before (empty on the first run)
            self._retry_on_open = True  # try again the next time the window is opened
        previous = None
        if self.monitor_infos and 0 <= self.selected_monitor_index < len(self.monitor_infos):
            old = self.monitor_infos[self.selected_monitor_index]
            previous = (old["manufacturer"], old["name"])
        self._generation += 1
        self.monitor_infos = infos
        # Keep the same monitor selected across a refresh if it is still there.
        self.selected_monitor_index = next(
            (i for i, info in enumerate(infos) if (info["manufacturer"], info["name"]) == previous), 0)
        self.monitor = infos[self.selected_monitor_index]["monitor"] if infos else None
        self.monitor_connected = bool(infos)
        self._confirmed = {(self._generation, i, f): info[f]
                           for i, info in enumerate(infos) for f in ("brightness", "contrast")}
        if infos:
            self.current_brightness = infos[self.selected_monitor_index]["brightness"]
            self.current_contrast = infos[self.selected_monitor_index]["contrast"]
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
        generation, idx, feature = key
        if generation != self._generation:
            return  # from before the last refresh: the index may now mean a different monitor
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

    def apply_theme(self, theme_name=None):
        if not sv_ttk:
            return
        if theme_name is None:
            theme_name = self.config.theme

        target_theme = theme_name
        if target_theme == "system" and darkdetect:
            # theme() returns None when Windows exposes no app theme setting
            # (e.g. Windows Server, CI runners); fall through to the default.
            target_theme = (darkdetect.theme() or "").lower()

        if target_theme not in ["dark", "light"]:
            target_theme = "dark"

        try:
            sv_ttk.set_theme(target_theme)
        except Exception as e:
            log.error("Failed to apply theme: %s", e)

    def change_theme(self, theme_name):
        self.config.theme = theme_name
        self.config.save()
        if self.root and threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.apply_theme(theme_name))
        else:
            self.apply_theme(theme_name)
        if self.tray_icon:
            try:
                self.tray_icon.menu = self.create_tray_menu()
            except Exception:
                pass

    # --- Autostart (Windows) support: delegates to monitorpy.autostart ---
    def is_autostart_enabled(self):
        return autostart.is_enabled()

    def enable_autostart(self):
        return autostart.enable()

    def disable_autostart(self):
        return autostart.disable()

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
        self._worker.submit_write((self._generation, self.selected_monitor_index, feature), self.monitor, feature, value)
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
            self._check_display_change()

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
        self.monitor_listbox = tk.Listbox(main_frame, height=3, exportselection=False)
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
        reset_b_var = tk.IntVar(value=self.config.preset_1.get("brightness", 100))
        reset_c_var = tk.IntVar(value=self.config.preset_1.get("contrast", 70))
        ttk.Label(frame, text="Brightness:").grid(row=1, column=0, sticky=tk.W, pady=(4,0))
        reset_b_entry = ttk.Entry(frame, textvariable=reset_b_var, width=6)
        reset_b_entry.grid(row=1, column=1, sticky=tk.W, pady=(4,0))
        ttk.Label(frame, text="Contrast:").grid(row=2, column=0, sticky=tk.W)
        reset_c_entry = ttk.Entry(frame, textvariable=reset_c_var, width=6)
        reset_c_entry.grid(row=2, column=1, sticky=tk.W)

        # Preset 2
        ttk.Separator(frame).grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=6)
        ttk.Label(frame, text="Night").grid(row=4, column=0, columnspan=2, sticky=tk.W)
        p_b_var = tk.IntVar(value=self.config.preset_2.get("brightness", 30))
        p_c_var = tk.IntVar(value=self.config.preset_2.get("contrast", 50))
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
            self.config.preset_1["brightness"] = rb
            self.config.preset_1["contrast"] = rc
            self.config.preset_2["brightness"] = pb
            self.config.preset_2["contrast"] = pc
            if not self.config.save():
                messagebox.showerror("Save failed", "The presets are active for this session but could not be saved to disk.\nSee the log for details.")
                return
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
            pystray.MenuItem("Light", lambda icon, item: self.change_theme("light"), checked=lambda item: self.config.theme == "light", radio=True),
            pystray.MenuItem("Dark", lambda icon, item: self.change_theme("dark"), checked=lambda item: self.config.theme == "dark", radio=True),
            pystray.MenuItem("System Default", lambda icon, item: self.change_theme("system"), checked=lambda item: self.config.theme == "system", radio=True)
        )

        return pystray.Menu(
            pystray.MenuItem(f"MonitorPy v{__version__} | Site", lambda icon, item: self.open_download_link(), enabled=True),
            pystray.MenuItem(f"Current Monitor: {current_monitor_name}", None, enabled=False),
            pystray.MenuItem("Show Controls", self.show_control_window, default=True),
            pystray.MenuItem("Edit Presets", lambda icon, item: self.open_edit_presets_window()),
            pystray.MenuItem("Day", self.quick_preset_1),
            pystray.MenuItem("Night", self.quick_preset_2),
            pystray.MenuItem("Refresh monitors", self.refresh_monitors, enabled=lambda item: not self.discovering),
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
        self.apply_preset(self.config.preset_1)

    def quick_preset_2(self):
        self.apply_preset(self.config.preset_2)

    def run(self):
        icon_image = load_tray_icon_image(128)
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
