"""Identifying connected monitors: manufacturer lookup and hot-plug detection.

Deliberately has no DDC/CI traffic (see ddc.py for that) and no dependency on
monitorcontrol, so it stays cheap enough to call every time the control window opens.
"""
import ctypes
from ctypes import wintypes

from monitorpy.logutil import log

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


def get_monitor_device_ids():
    """PnP device ID of each *connected* physical monitor, in the same order as
    monitorcontrol.get_monitors() (both walk EnumDisplayMonitors, one entry per physical
    monitor). Cheap (no DDC/CI traffic, no monitor handles). None where unknown."""
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
                result.append(device.DeviceID)
            else:
                result.append(None)
    return result


def get_monitor_manufacturers():
    """Manufacturer of each connected monitor (same order as get_monitors()); None where unknown.
    Uses the active monitor's device ID, so stale registry entries cannot be mixed in."""
    return [manufacturer_from_device_id(device_id) for device_id in get_monitor_device_ids()]


def get_display_signature():
    """Snapshot of which monitors are connected, to detect plug/unplug. None if unavailable."""
    try:
        return tuple(get_monitor_device_ids())
    except Exception as e:
        log.warning("Could not read display configuration: %s", e)
        return None
