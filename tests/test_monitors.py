"""Manufacturer identification and hot-plug signatures. Mostly pure; a couple of tests
touch the real, currently-connected hardware (read-only Win32 calls, no DDC/CI)."""
import pytest

from monitorpy import monitors
from tests.conftest import device_id


@pytest.mark.parametrize("raw, expected", [
    (device_id("DEL", 1), "Dell"),
    (device_id("SAM", 2), "Samsung"),
    (device_id("HSJ", 3), "HSJ"),        # unknown code -> the raw code, never a guess
    (device_id("RTK", 4), "RTK"),        # the old registry-based table wrongly said "Samsung"
    ("", None),
    (None, None),
    (r"DISPLAY\DEL4240", None),          # not a MONITOR\... id
    ("garbage", None),
])
def test_manufacturer_from_device_id(raw, expected):
    assert monitors.manufacturer_from_device_id(raw) == expected


def test_get_monitor_manufacturers_uses_device_ids_in_order(monkeypatch):
    ids = [device_id("DEL", 1), device_id("SAM", 2), device_id("LEN", 3)]
    monkeypatch.setattr(monitors, "get_monitor_device_ids", lambda: ids)
    assert monitors.get_monitor_manufacturers() == ["Dell", "Samsung", "Lenovo"]


def test_get_display_signature_is_stable_and_order_sensitive(monkeypatch):
    monkeypatch.setattr(monitors, "get_monitor_device_ids", lambda: ["A", "B"])
    assert monitors.get_display_signature() == ("A", "B")
    monkeypatch.setattr(monitors, "get_monitor_device_ids", lambda: ["B", "A"])
    assert monitors.get_display_signature() != ("A", "B")


def test_get_display_signature_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(monitors, "get_monitor_device_ids",
                         lambda: (_ for _ in ()).throw(OSError("api failed")))
    assert monitors.get_display_signature() is None


# ------------------------------------------------------------------------------- real hardware

@pytest.mark.hardware
def test_real_get_monitor_device_ids_matches_monitorcontrol_count():
    """Read-only: no DDC/CI traffic, no monitor handles opened. Confirms the two APIs walk
    the displays the same way, which get_monitor_manufacturers()'s pairing depends on."""
    monitorcontrol = pytest.importorskip("monitorcontrol")
    try:
        real_monitors = monitorcontrol.get_monitors()
    except Exception as e:
        pytest.skip(f"could not enumerate real monitors: {e}")
    if not real_monitors:
        pytest.skip("no monitors connected")
    ids = monitors.get_monitor_device_ids()
    assert len(ids) == len(real_monitors)
