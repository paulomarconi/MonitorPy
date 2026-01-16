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
from pathlib import Path
import time

try:
    from monitorcontrol import get_monitors
except ImportError:
    print("Please install monitorcontrol: pip install monitorcontrol")
    sys.exit(1)

class MonitorController:
    def __init__(self):
        self.monitors = []
        self.monitor = None
        self.root = None
        self.brightness_var = None
        self.contrast_var = None
        self.tray_icon = None
        self.current_brightness = []
        self.current_contrast = []
        self.monitor_connected = False
        self.selected_monitor_index = 0
        self.preset_1 = {"brightness": 100, "contrast": 70}
        self.preset_2 = {"brightness": 30, "contrast": 50}

        self.discover_monitors()
    
    def discover_monitors(self):
        print("Discovering monitors...")
        self.monitors = get_monitors()
        self.monitor_infos = []
        for i, monitor in enumerate(self.monitors):
            info = {"monitor": monitor, "name": f"Monitor {i+1}", "brightness": 50, "contrast": 50}
            try:
                with monitor:
                    caps = monitor.get_vcp_capabilities()
                    if isinstance(caps, dict) and "model" in caps:
                        info["name"] = caps["model"]
                    info["brightness"] = monitor.get_luminance()
                    info["contrast"] = monitor.get_contrast()
            except Exception:
                info["name"] += " (No DDC/CI)"
            self.monitor_infos.append(info)
        self.monitor = self.monitors[0] if self.monitors else None
        self.monitor_connected = bool(self.monitors)
        if self.monitor_infos:
            self.current_brightness = self.monitor_infos[0]["brightness"]
            self.current_contrast = self.monitor_infos[0]["contrast"]

    # --- Autostart (Windows) support ---
    def _get_startup_paths(self):
        """Return possible startup file paths (lnk and vbs) in the user's Startup folder."""
        appdata = os.environ.get('APPDATA')
        if not appdata:
            return None, None
        startup = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
        lnk = os.path.join(startup, 'MonitorPy.lnk')
        vbs = os.path.join(startup, 'MonitorPy.vbs')
        return lnk, vbs

    def is_autostart_enabled(self):
        lnk, vbs = self._get_startup_paths()
        try:
            return (lnk and os.path.exists(lnk)) or (vbs and os.path.exists(vbs))
        except Exception:
            return False

    def enable_autostart(self):
        lnk, vbs = self._get_startup_paths()
        # Prefer creating a .lnk via Windows Script Host. Fall back to a .vbs launcher if pywin32 isn't available.
        try:
            try:
                from win32com.client import Dispatch
            except Exception:
                Dispatch = None

            if Dispatch and lnk:
                shell = Dispatch('WScript.Shell')
                shortcut = shell.CreateShortCut(lnk)
                if getattr(sys, 'frozen', False):
                    shortcut.Targetpath = sys.executable
                    shortcut.WorkingDirectory = os.path.dirname(sys.executable)
                    shortcut.Arguments = ''
                else:
                    pythonw = sys.executable
                    # prefer pythonw to avoid a console when launching the script
                    if pythonw.lower().endswith('python.exe'):
                        pythonw = pythonw[:-len('python.exe')] + 'pythonw.exe'
                    script = os.path.abspath(sys.argv[0])
                    shortcut.Targetpath = pythonw
                    shortcut.Arguments = f'"{script}"'
                    shortcut.WorkingDirectory = os.path.dirname(script)
                try:
                    ico = os.path.join(os.path.dirname(sys.executable), 'python.exe')
                    shortcut.IconLocation = ico
                except Exception:
                    pass
                shortcut.save()
                return True

            # Fallback: create a .vbs in Startup that launches pythonw + script without showing a console
            if vbs:
                if getattr(sys, 'frozen', False):
                    exe = sys.executable
                    cmd = f'"{exe}"'
                else:
                    pythonw = sys.executable
                    if pythonw.lower().endswith('python.exe'):
                        pythonw = pythonw[:-len('python.exe')] + 'pythonw.exe'
                    script = os.path.abspath(sys.argv[0])
                    cmd = f'"{pythonw}" "{script}"'
                content = (
                    'Set WshShell = CreateObject("WScript.Shell")\n'
                    f'WshShell.Run {cmd}, 0\n'
                )
                try:
                    with open(vbs, 'w', encoding='utf-8') as f:
                        f.write(content)
                    return True
                except Exception:
                    return False
        except Exception:
            return False

    def disable_autostart(self):
        lnk, vbs = self._get_startup_paths()
        ok = False
        try:
            if lnk and os.path.exists(lnk):
                try:
                    os.remove(lnk)
                    ok = True
                except Exception:
                    pass
            if vbs and os.path.exists(vbs):
                try:
                    os.remove(vbs)
                    ok = True
                except Exception:
                    pass
        except Exception:
            pass
        return ok

    def toggle_autostart(self, icon=None, item=None):
        # Ensure runs on Tk main thread when invoked from tray
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, lambda: self.toggle_autostart(None, None))
            return
        try:
            if self.is_autostart_enabled():
                self.disable_autostart()
            else:
                self.enable_autostart()
        finally:
            # Refresh tray menu to update checked state
            if self.tray_icon:
                try:
                    self.tray_icon.menu = self.create_tray_menu()
                except Exception:
                    pass

    def get_monitor_names(self):
        return [info["name"] for info in self.monitor_infos]

    def get_brightness(self):
        return self.current_brightness

    def set_brightness(self, value):
        if self.monitor:
            try:
                with self.monitor:
                    self.monitor.set_luminance(int(value))
            except Exception:
                pass
        self.current_brightness = int(value)
        self.monitor_infos[self.selected_monitor_index]["brightness"] = int(value)

    def get_contrast(self):
        return self.current_contrast

    def set_contrast(self, value):
        if self.monitor:
            try:
                with self.monitor:
                    self.monitor.set_contrast(int(value))
            except Exception:
                pass
        self.current_contrast = int(value)
        self.monitor_infos[self.selected_monitor_index]["contrast"] = int(value)

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
            self.root.withdraw()
            return

        if not self.root or not self.root.winfo_exists():
            self.create_control_window()
        else:          
            self.position_near_tray()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            try:
                # start polling for outside clicks
                self.root.after(100, self._outside_click_check)
            except Exception:
                pass

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
                x = screen_width - width - 20
                y = rect.top + 10
            elif rect.bottom < screen_height:
                x = screen_width - width - 20
                y = rect.bottom - height - 10
            elif rect.left > 0:
                x = rect.left + 10
                y = screen_height - height - 60
            elif rect.right < screen_width:
                x = rect.right - width - 10
                y = screen_height - height - 60
            else:
                x = screen_width - width - 20
                y = screen_height - height - 60
        except Exception:
            x = screen_width - width - 20
            y = screen_height - height - 60
        self.root.geometry(f"+{x}+{y}")

    def _outside_click_check(self):
        """Periodically check whether the user clicked outside the control window.
        If a left-button click occurs outside the window bounds, hide the window.
        Uses Win32 GetCursorPos and GetAsyncKeyState to detect clicks outside the app.
        Also accounts for the edit presets (Day and Night) window.
        """
        try:
            if not self.root or not self.root.winfo_exists() or self.root.state() == 'withdrawn':
                return
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                # Failed to get cursor position; schedule next check
                self.root.after(100, self._outside_click_check)
                return
            mx, my = pt.x, pt.y

            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()

            inside = (rx <= mx <= rx + rw) and (ry <= my <= ry + rh)

            # Also check if click is inside the edit window
            edit_win = getattr(self, 'edit_win', None)
            if edit_win and edit_win.winfo_exists() and edit_win.state() != 'withdrawn':
                ex = edit_win.winfo_rootx()
                ey = edit_win.winfo_rooty()
                ew = edit_win.winfo_width()
                eh = edit_win.winfo_height()
                inside_edit = (ex <= mx <= ex + ew) and (ey <= my <= ey + eh)
                inside = inside or inside_edit

            # VK_LBUTTON == 0x01. GetAsyncKeyState returns negative if down (high bit set).
            lbutton = ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000

            # If left button is down and cursor is outside both windows, hide.
            if lbutton and not inside:
                try:
                    self.hide_window()
                except Exception:
                    pass
                return
        except Exception:
            # Ignore errors and continue polling
            pass
        finally:
            try:
                # keep polling while the window exists
                if self.root and self.root.winfo_exists():
                    self.root.after(100, self._outside_click_check)
            except Exception:
                pass


    def create_control_window(self):
        self.root = tk.Tk()
        self.root.title("MonitorPy")
        self.root.geometry("280x200")
        self.root.resizable(False, False)
        self.root.attributes('-toolwindow', True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.overrideredirect(True)

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
        for i, name in enumerate(self.get_monitor_names()):
            self.monitor_listbox.insert(tk.END, name)
        self.monitor_listbox.bind("<<ListboxSelect>>", self.on_monitor_select)

        # Restore last selection if available
        if hasattr(self, 'last_selected_monitor_index'):
            self.selected_monitor_index = self.last_selected_monitor_index
        else:
            self.selected_monitor_index = 0
        self.monitor_listbox.select_set(self.selected_monitor_index)

        sliders_frame = ttk.Frame(main_frame)
        sliders_frame.pack(fill=tk.X, pady=8)

        ttk.Label(sliders_frame, text="Brightness").grid(row=0, column=0, sticky=tk.W)
        brightness_value = getattr(self, 'last_brightness', self.get_brightness())
        self.brightness_var = tk.IntVar(value=brightness_value)
        self.brightness_scale = ttk.Scale(sliders_frame, from_=0, to=100, variable=self.brightness_var,
                                      command=self.on_brightness_change, orient=tk.HORIZONTAL, length=120)
        self.brightness_scale.grid(row=1, column=0, padx=(0,10))
        self.brightness_label = ttk.Label(sliders_frame, text=f"{self.brightness_var.get()}%")
        self.brightness_label.grid(row=2, column=0, sticky=tk.E)

        ttk.Label(sliders_frame, text="Contrast").grid(row=0, column=1, sticky=tk.W)
        contrast_value = getattr(self, 'last_contrast', self.get_contrast())
        self.contrast_var = tk.IntVar(value=contrast_value)
        self.contrast_scale = ttk.Scale(sliders_frame, from_=0, to=100, variable=self.contrast_var,
                                    command=self.on_contrast_change, orient=tk.HORIZONTAL, length=120)
        self.contrast_scale.grid(row=1, column=1, padx=(10,0))
        self.contrast_label = ttk.Label(sliders_frame, text=f"{self.contrast_var.get()}%")
        self.contrast_label.grid(row=2, column=1, sticky=tk.E)

        self.position_near_tray()
        self.root.focus_force()
        try:
            # begin outside-click watcher
            self.root.after(100, self._outside_click_check)
        except Exception:
            pass

    def on_monitor_select(self, event=None):
        sel = self.monitor_listbox.curselection()
        if sel:
            self.selected_monitor_index = sel[0]
            info = self.monitor_infos[self.selected_monitor_index]
            self.monitor = info["monitor"]
            self.current_brightness = info["brightness"]
            self.current_contrast = info["contrast"]
            self.update_controls()
            if self.tray_icon:
                self.tray_icon.menu = self.create_tray_menu() 

    def update_controls(self):
        self.brightness_var.set(self.get_brightness())
        self.brightness_label.config(text=f"{self.brightness_var.get()}%")
        self.contrast_var.set(self.get_contrast())
        self.contrast_label.config(text=f"{self.contrast_var.get()}%")

    def on_brightness_change(self, value):
        if self.monitor:
            self.set_brightness(int(float(value)))
            self.brightness_label.config(text=f"{int(float(value))}%")

    def on_contrast_change(self, value):
        if self.monitor:
            self.set_contrast(int(float(value)))
            self.contrast_label.config(text=f"{int(float(value))}%")

    def open_download_link(self):
        import webbrowser
        webbrowser.open("https://github.com/paulomarconi/MonitorPy")
            
    def hide_window(self):
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
                ex = rx - ew - 8
            else:
                ex = rx + rw + 8

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
        if self.root:
            self.root.quit()
        if self.tray_icon:
            self.tray_icon.stop()

    def create_tray_menu(self):
        monitor_names = self.get_monitor_names()
        current_monitor_name = monitor_names[self.selected_monitor_index] if monitor_names else "No monitor"
        return pystray.Menu(
            pystray.MenuItem("MonitorPy v1.0.1 | Site", lambda icon, item: self.open_download_link(), enabled=True),
            pystray.MenuItem(f"Current Monitor: {current_monitor_name}", None, enabled=False),
            pystray.MenuItem("Show Controls", self.show_control_window, default=True),
            pystray.MenuItem("Edit Presets", lambda icon, item: self.open_edit_presets_window()),
            pystray.MenuItem("Day", self.quick_preset_1),
            pystray.MenuItem("Night", self.quick_preset_2),
            pystray.MenuItem(
                "Autostart on Windows startup",
                lambda icon, item: self.toggle_autostart(),
                checked=lambda item: self.is_autostart_enabled()
            ),
            pystray.MenuItem("Exit", self.quit_app)
        )

    def quick_preset_1(self):
        # If invoked from non-main thread (tray), schedule on Tk thread
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, self.quick_preset_1)
            return

        self.set_brightness(self.preset_1.get("brightness", 100))
        self.set_contrast(self.preset_1.get("contrast", 70))
        if self.brightness_var:
            self.brightness_var.set(self.preset_1.get("brightness", 100))
            self.brightness_label.config(text=f"{self.brightness_var.get()}%")
        if self.contrast_var:
            self.contrast_var.set(self.preset_1.get("contrast", 70))
            self.contrast_label.config(text=f"{self.contrast_var.get()}%")
    
    def quick_preset_2(self):
        """Set preset 1"""
        # If invoked from non-main thread (tray), schedule on Tk thread
        if self.root and threading.current_thread() is not threading.main_thread():
            if self.root:
                self.root.after(0, self.quick_preset_2)
            return

        self.set_brightness(self.preset_2.get("brightness", 30))
        self.set_contrast(self.preset_2.get("contrast", 50))
        if self.brightness_var:
            self.brightness_var.set(self.preset_2.get("brightness", 30))
            self.brightness_label.config(text=f"{self.brightness_var.get()}%")
        if self.contrast_var:
            self.contrast_var.set(self.preset_2.get("contrast", 50))
            self.contrast_label.config(text=f"{self.contrast_var.get()}%")
    
    
    def run(self):
        icon_image = self.create_image(64, 64, 'black', 'white')
        self.tray_icon = pystray.Icon(
            "monitor_control",
            icon_image,
            f"Monitor Control - {'Connected' if self.monitor_connected else 'Disconnected'}",
            self.create_tray_menu()
        )
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
        self.create_control_window()
        self.root.withdraw()

        def check_interrupt():
            try:
                pass  
            except KeyboardInterrupt:
                self.quit_app()
            self.root.after(100, check_interrupt)

        self.root.after(100, check_interrupt)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit_app()

if __name__ == "__main__":
    try:
        app = MonitorController()
        app.run()
    except Exception as e:
        print(f"Error starting application: {e}")
        messagebox.showerror("Error", f"Failed to start application: {e}")
        sys.exit(1)