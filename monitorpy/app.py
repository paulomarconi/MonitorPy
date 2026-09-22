"""Process entry point: DPI awareness, the single-instance guard, then the app itself."""
from tkinter import messagebox

from monitorpy.controller import MonitorController
from monitorpy.logutil import log
from monitorpy.platform_win import acquire_single_instance, enable_dpi_awareness


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
