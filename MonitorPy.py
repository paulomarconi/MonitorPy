#!/usr/bin/env python3
"""
MonitorPy - Monitor Control System Tray Application for Windows 11/10
Requires: pip install pystray pillow monitorcontrol pyinstaller

This file is the entry point (kept as a top-level script for `python MonitorPy.py`
and for PyInstaller). The implementation lives in the monitorpy/ package.
"""
import sys

from monitorpy.app import main

if __name__ == "__main__":
    sys.exit(main())
