"""System-wide hotkey registration. Uses the real Win32 API (RegisterHotKey is cheap and
self-contained), but never sends real key presses here - that only happens in the
end-to-end test in test_controller.py."""
import time

from monitorpy.hotkeys import HOTKEYS, HotkeyListener


def test_all_hotkeys_register_when_free():
    calls = []
    listener = HotkeyListener(HOTKEYS, lambda a: calls.append(a), lambda labels: calls.append(("failed", labels)))
    try:
        listener.start()
        assert listener.failed == []
    finally:
        listener.stop()


def test_conflicting_registration_is_reported_not_silent():
    first = HotkeyListener(HOTKEYS, lambda a: None, lambda labels: None)
    first.start()
    try:
        reported = []
        second = HotkeyListener(HOTKEYS, lambda a: None, lambda labels: reported.append(labels))
        second.start()
        try:
            assert second.failed == ["Ctrl+F10", "Ctrl+F11", "Alt+F10", "Alt+F11"]
            assert reported == [second.failed]
        finally:
            second.stop()
    finally:
        first.stop()


def test_stop_releases_the_keys_for_the_next_listener():
    first = HotkeyListener(HOTKEYS, lambda a: None, lambda labels: None)
    first.start()
    first.stop()
    second = HotkeyListener(HOTKEYS, lambda a: None, lambda labels: None)
    second.start()
    try:
        assert second.failed == []
    finally:
        second.stop()

