"""Run-on-startup via the HKCU Run key. isolated_autostart_value (conftest, autouse)
points RUN_VALUE at a name distinct from the real app's, so the real 'MonitorPy'
Run-key entry is never touched."""
import os

import pytest

from monitorpy import autostart


@pytest.fixture(autouse=True)
def clean_run_key():
    """Every test starts and ends with the (pytest-only) Run-key entry absent."""
    autostart.disable()
    yield
    autostart.disable()


def test_disabled_by_default():
    assert autostart.is_enabled() is False


def test_enable_then_is_enabled_then_disable():
    assert autostart.enable() is True
    assert autostart.is_enabled() is True
    assert autostart.disable() is True
    assert autostart.is_enabled() is False


def test_enabling_writes_a_runnable_command_line():
    autostart.enable()
    cmd = autostart.autostart_command()
    assert cmd.strip('"').lower().endswith(("pythonw.exe", ".exe")) or "python" in cmd.lower()


def test_a_legacy_startup_file_counts_as_enabled_and_is_migrated_away(tmp_path, monkeypatch):
    legacy_lnk, legacy_vbs = autostart.legacy_startup_files()
    os.makedirs(os.path.dirname(legacy_lnk), exist_ok=True)
    open(legacy_lnk, "w").close()
    try:
        assert autostart.is_enabled() is True         # detected via the legacy file
        assert autostart.enable() is True
        assert not os.path.exists(legacy_lnk), "the legacy launcher must be removed once the Run key is set"
        assert autostart.is_enabled() is True
    finally:
        for p in (legacy_lnk, legacy_vbs):
            if os.path.exists(p):
                os.remove(p)
        autostart.disable()


def test_disable_also_removes_legacy_files_even_if_the_run_key_was_never_set(tmp_path):
    legacy_lnk, legacy_vbs = autostart.legacy_startup_files()
    os.makedirs(os.path.dirname(legacy_lnk), exist_ok=True)
    open(legacy_vbs, "w").close()
    try:
        assert autostart.disable() is True
        assert not os.path.exists(legacy_vbs)
    finally:
        for p in (legacy_lnk, legacy_vbs):
            if os.path.exists(p):
                os.remove(p)


def test_double_enable_and_double_disable_are_idempotent():
    assert autostart.enable() is True
    assert autostart.enable() is True
    assert autostart.disable() is True
    assert autostart.disable() is True
