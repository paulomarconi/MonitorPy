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


# ------------------------------------------------------------------ repairing a stale entry

def _write_run_value(command):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
        winreg.SetValueEx(key, autostart.RUN_VALUE, 0, winreg.REG_SZ, command)


def _read_run_value():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
        return winreg.QueryValueEx(key, autostart.RUN_VALUE)[0]


@pytest.fixture
def frozen_exe(tmp_path, monkeypatch):
    """Pretend to be the PyInstaller exe, running from tmp_path/new/MonitorPy.exe."""
    exe = tmp_path / "new" / "MonitorPy.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", str(exe))
    return exe


@pytest.mark.parametrize("command, target", [
    (r'"C:\Some Dir\MonitorPy.exe"', r"C:\Some Dir\MonitorPy.exe"),
    (r'"C:\py\pythonw.exe" "C:\x\MonitorPy.py"', r"C:\py\pythonw.exe"),
    (r"C:\Tools\MonitorPy.exe --flag", r"C:\Tools\MonitorPy.exe"),
    (r'  "C:\unterminated', r"C:\unterminated"),
])
def test_command_target_extracts_the_program(command, target):
    assert autostart.command_target(command) == target


def test_entry_pointing_at_a_moved_exe_is_repointed(frozen_exe, tmp_path):
    _write_run_value(f'"{tmp_path / "old" / "MonitorPy.exe"}"')  # no longer exists
    assert autostart.repair_stale_entry() is True
    assert _read_run_value() == f'"{frozen_exe}"'


def test_entry_pointing_at_an_existing_exe_is_left_alone(frozen_exe, tmp_path):
    other = tmp_path / "MonitorPy.exe"
    other.write_bytes(b"")
    _write_run_value(f'"{other}"')
    assert autostart.repair_stale_entry() is False
    assert _read_run_value() == f'"{other}"'


def test_repair_never_turns_autostart_on(frozen_exe):
    assert autostart.repair_stale_entry() is False
    assert autostart.is_enabled() is False


def test_running_from_source_never_rewrites_the_entry(tmp_path, monkeypatch):
    monkeypatch.delattr(autostart.sys, "frozen", raising=False)
    stale = f'"{tmp_path / "gone" / "MonitorPy.exe"}"'
    _write_run_value(stale)
    assert autostart.repair_stale_entry() is False
    assert _read_run_value() == stale
