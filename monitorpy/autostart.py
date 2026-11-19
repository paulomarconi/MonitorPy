"""Run-on-startup, via the HKCU Run registry key.

Plain module functions (no state beyond the registry/filesystem), so they can be tested
without building a MonitorController or a Tk window.
"""
import os
import sys
import winreg

from monitorpy.logutil import log

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MonitorPy"


def autostart_command():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    pythonw = sys.executable
    # prefer pythonw to avoid a console when launching the script
    if pythonw.lower().endswith('python.exe'):
        pythonw = pythonw[:-len('python.exe')] + 'pythonw.exe'
    return f'"{pythonw}" "{os.path.abspath(sys.argv[0])}"'


def legacy_startup_files():
    """Startup-folder launchers created by older versions (.lnk / .vbs)."""
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return []
    startup = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
    return [os.path.join(startup, 'MonitorPy.lnk'), os.path.join(startup, 'MonitorPy.vbs')]


def remove_legacy_startup_files():
    for path in legacy_startup_files():
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            log.warning("Could not remove legacy startup file %s: %s", path, e)


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except FileNotFoundError:
        return any(os.path.exists(p) for p in legacy_startup_files())
    except OSError as e:
        log.error("Could not read autostart setting: %s", e)
        return False


def enable():
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
    except OSError as e:
        log.error("Could not enable autostart: %s", e)
        return False
    remove_legacy_startup_files()
    return True


def command_target(command):
    """The program a Run-key command line starts: its first token, quoted or not."""
    command = command.strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end > 0 else command[1:]
    return command.split(" ", 1)[0]


def repair_stale_entry():
    """If autostart points at an exe that no longer exists (MonitorPy.exe was moved or
    renamed), re-point it at the running exe. Returns True if the entry was rewritten.

    Only the frozen exe does this, and only when the old target is gone: running the
    script from source, or a second copy of the exe, must never take over an autostart
    entry that still works."""
    if not getattr(sys, 'frozen', False):
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            command, _ = winreg.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return False  # autostart is off
    except OSError as e:
        log.error("Could not read autostart setting: %s", e)
        return False
    if not isinstance(command, str) or os.path.exists(command_target(command)):
        return False
    log.info("Autostart pointed at missing %s; updating it to %s", command, autostart_command())
    return enable()


def disable():
    ok = True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.error("Could not disable autostart: %s", e)
        ok = False
    remove_legacy_startup_files()
    return ok and not any(os.path.exists(p) for p in legacy_startup_files())
