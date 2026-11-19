"""Process entry point: DPI awareness, the single-instance guard, then the app itself."""
from tkinter import messagebox

from monitorpy import autostart
from monitorpy.logutil import log
from monitorpy.platform_win import acquire_single_instance, enable_dpi_awareness


def main():
    enable_dpi_awareness()
    try:
        # Imported here, not at module level, so a missing package (monitorcontrol, pystray,
        # Pillow) gets a readable message instead of a bare traceback, and importing
        # monitorpy modules never has side effects.
        from monitorpy.controller import MonitorController
    except ImportError as e:
        log.error("Missing dependency: %s", e)
        messagebox.showerror("Missing Dependency",
                             f"MonitorPy cannot start because a required package is missing:\n\n{e}\n\n"
                             "Install the dependencies with: pip install -r requirements.txt")
        return 1
    if not acquire_single_instance():
        log.info("Another instance is already running; exiting")
        messagebox.showinfo("MonitorPy", "MonitorPy is already running.\nLook for its icon in the system tray.")
        return 0
    autostart.repair_stale_entry()
    try:
        MonitorController().run()
    except Exception as e:
        log.exception("Error starting application")
        messagebox.showerror("Error", f"Failed to start application: {e}")
        return 1
    return 0
