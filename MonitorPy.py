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
        dc.rectangle([2, 4, width-3, height-8], fill=color2, outline=color1, width=2)
        dc.rectangle([4, 6, width-5, height-10], fill=color1, outline=color2)
        indicator_color = color2 if self.monitor_connected else 'red'
        dc.rectangle([6, 8, width-7, height-12], fill=indicator_color)
        dc.rectangle([width//2-3, height-6, width//2+3, height-2], fill=color2)
        return image

    def show_control_window(self, icon=None, item=None):
        """Toggle the control window visibility and position it near the tray icon"""
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

    def create_control_window(self):
        self.root = tk.Tk()
        self.root.title("MonitorPy")
        self.root.geometry("300x200")
        self.root.resizable(False, False)
        self.root.attributes('-toolwindow', True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.overrideredirect(True)

        main_frame = ttk.Frame(self.root, padding="8")
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X)
        ttk.Label(top_frame, text="MonitorPy", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(top_frame, text="Reset to 50%", command=self.reset_values).pack(side=tk.RIGHT, padx=5)

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

    def reset_values(self):
        self.set_brightness(50)
        self.set_contrast(50)
        self.update_controls()
        
    def open_download_link(self):
        import webbrowser
        webbrowser.open("https://github.com/paulomarconi/MonitorPy")
            
    def hide_window(self):
        if self.root:
            self.root.withdraw()

    def quit_app(self, icon=None, item=None):
        if self.root:
            self.root.quit()
        if self.tray_icon:
            self.tray_icon.stop()

    def create_tray_menu(self):
        monitor_names = self.get_monitor_names()
        current_monitor_name = monitor_names[self.selected_monitor_index] if monitor_names else "No monitor"
        return pystray.Menu(
            pystray.MenuItem("MonitorPy v1.0.0 | Site", lambda icon, item: self.open_download_link(), enabled=True),
            pystray.MenuItem(f"Current Monitor: {current_monitor_name}", None, enabled=False),
            pystray.MenuItem("Show Controls", self.show_control_window, default=True),
            pystray.MenuItem("Reset to 50%", self.quick_reset),
            pystray.MenuItem("Exit", self.quit_app)
        )

    def quick_reset(self):
        self.set_brightness(50)
        self.set_contrast(50)
        if self.brightness_var:
            self.brightness_var.set(50)
            self.brightness_label.config(text="50%")
        if self.contrast_var:
            self.contrast_var.set(50)
            self.contrast_label.config(text="50%")
    
    

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
                pass  # Just a placeholder, KeyboardInterrupt will be handled by Tkinter events
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