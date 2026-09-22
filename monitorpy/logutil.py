"""Logging setup, shared by every module. Importing this module (once, from anywhere)
attaches the handlers; every other module just does `from monitorpy.logutil import log`."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

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
