"""Shared pytest fixtures.

IMPORTANT: monitorpy.logutil attaches its log handlers as a side effect of being
imported (matching the app's own startup behavior), and it is imported transitively
the moment any monitorpy submodule is. To make sure that first import ever happens
writes its log under a throwaway %APPDATA%, never the real user's, this module
redirects APPDATA *before* anything below it imports monitorpy - conftest.py is
always collected before test modules, so this runs first for the whole session.
"""
import os
import tempfile

_TEST_APPDATA = tempfile.mkdtemp(prefix="monitorpy_tests_")
os.environ["APPDATA"] = _TEST_APPDATA

import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from monitorpy import config as config_module  # noqa: E402
from monitorpy import autostart  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Every test gets its own empty %APPDATA%, so config/log files never collide
    between tests and never touch the real one."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    yield tmp_path


@pytest.fixture(autouse=True)
def isolated_autostart_value(monkeypatch):
    """Never touch the real 'MonitorPy' Run-key entry or Startup-folder files."""
    monkeypatch.setattr(autostart, "RUN_VALUE", "MonitorPyPytest")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_APPDATA, ignore_errors=True)


class FakeMonitor:
    """A monitorcontrol.Monitor stand-in: same context-manager/get_*/set_* surface,
    with everything controllable and observable for assertions."""

    def __init__(self, name="FAKE", brightness=50, contrast=50, fail_open=False,
                 fail_caps=False, fail_brightness=False, fail_contrast=False,
                 fail_writes=False, write_delay=0.0, caps_delay=0.0):
        self.name = name
        self.brightness = brightness
        self.contrast = contrast
        self.fail_open = fail_open
        self.fail_caps = fail_caps
        self.fail_brightness = fail_brightness
        self.fail_contrast = fail_contrast
        self.fail_writes = fail_writes
        self.write_delay = write_delay
        self.caps_delay = caps_delay
        self.writes = []  # [(feature, value), ...] accepted writes, in order

    def __enter__(self):
        if self.fail_open:
            raise RuntimeError("cannot open DDC/CI session")
        return self

    def __exit__(self, *exc):
        return False

    def get_vcp_capabilities(self):
        if self.caps_delay:
            time.sleep(self.caps_delay)
        if self.fail_caps:
            raise RuntimeError("failed to get VCP capabilities")
        return {"model": self.name}

    def get_luminance(self):
        if self.fail_brightness:
            raise RuntimeError("failed to get VCP feature")
        return self.brightness

    def get_contrast(self):
        if self.fail_contrast:
            raise RuntimeError("failed to get VCP feature")
        return self.contrast

    def set_luminance(self, value):
        if self.write_delay:
            time.sleep(self.write_delay)
        if self.fail_writes:
            raise RuntimeError("DDC rejected")
        self.brightness = value
        self.writes.append(("brightness", value))

    def set_contrast(self, value):
        if self.write_delay:
            time.sleep(self.write_delay)
        if self.fail_writes:
            raise RuntimeError("DDC rejected")
        self.contrast = value
        self.writes.append(("contrast", value))


def device_id(code, index):
    """A plausible MONITOR\\<3-letter code><digits>\\{guid}\\NNNN PnP device id."""
    return rf"MONITOR\{code}{index:04d}\{{4d36e96e-e325-11ce-bfc1-08002be10318}}\{index:04d}"


def pump_tk(root, until, timeout=10, interval=0.005):
    """Run the Tk event loop until `until()` is true (or raise on timeout)."""
    deadline = time.time() + timeout
    while not until():
        if time.time() > deadline:
            raise AssertionError("timed out waiting for a Tk/worker condition")
        root.update()
        time.sleep(interval)
    root.update()


def run_steps_under_mainloop(root, steps, step_timeout=5):
    """Run `root.mainloop()` and advance through `steps` (a list of (action, condition)
    pairs), calling each action once and waiting for its condition before moving to the
    next. Needed instead of pump_tk() whenever another thread schedules work onto this
    root via .after() (e.g. HotkeyListener) - Tkinter only honors a cross-thread .after()
    while the main thread is actually inside mainloop(), not while it is being driven by
    repeated .update() calls, and raises 'main thread is not in main loop' otherwise.
    """
    state = {"i": 0, "started": False, "deadline": 0.0, "failed": None}

    def tick():
        if state["i"] >= len(steps):
            root.quit()
            return
        action, condition = steps[state["i"]]
        if not state["started"]:
            action()
            state["started"] = True
            state["deadline"] = time.time() + step_timeout
        if condition():
            state["i"] += 1
            state["started"] = False
        elif time.time() > state["deadline"]:
            state["failed"] = state["i"]
            root.quit()
            return
        root.after(10, tick)

    root.after(10, tick)
    root.mainloop()
    if state["failed"] is not None:
        raise AssertionError(f"step {state['failed']} did not reach its condition in time")


@pytest.fixture
def fake_monitor_env(monkeypatch):
    """Patch monitorpy.controller's monitor sources. Returns a mutable dict the test
    can update (`env['monitors']`, `env['device_ids']`, `env['fail']`) to simulate
    plug/unplug/enumeration failure between calls."""
    import monitorpy.controller as controller_module

    env = {"monitors": [], "device_ids": [], "fail": False, "probe_calls": 0}

    def fake_get_monitors():
        env["probe_calls"] += 1
        if env["fail"]:
            raise RuntimeError("enumeration failed")
        return list(env["monitors"])

    def fake_get_manufacturers():
        from monitorpy.monitors import manufacturer_from_device_id
        return [manufacturer_from_device_id(d) for d in env["device_ids"]]

    monkeypatch.setattr(controller_module, "get_monitors", fake_get_monitors)
    monkeypatch.setattr(controller_module, "get_monitor_manufacturers", fake_get_manufacturers)
    monkeypatch.setattr(controller_module, "get_display_signature", lambda: (
        None if env["fail"] else tuple(env["device_ids"])))
    return env


def create_control_window_with_retry(controller_obj, attempts=3, delay=0.2):
    """controller_obj.create_control_window(), tolerating a known Windows/Tcl flake: this
    environment's Tcl interpreter init occasionally (roughly 1 in 20 calls, seen even from
    a properly activated conda prompt) fails to read a library file that verifiably exists
    on disk (tk.tcl, init.tcl, ...) with 'no such file or directory' - a transient read
    failure, not a missing or misconfigured install (confirmed: the file is there before
    and after). Most likely a real-time antivirus/indexer scan momentarily touching the
    file during rapid successive Tcl interpreter creation, which only tests do; the app
    itself only ever creates one Tk root per run and has never shown this. Retrying is
    purely a test-suite affordance - nothing here changes application behavior.
    """
    import tkinter as tk
    for attempt in range(attempts):
        try:
            controller_obj.create_control_window()
            return
        except tk.TclError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


@pytest.fixture
def controller(fake_monitor_env):
    """A MonitorController with a real (hidden) Tk window and a real DdcWorker thread,
    but fake monitors. Always cleaned up, even if the test fails."""
    from monitorpy.controller import MonitorController

    c = MonitorController()
    create_control_window_with_retry(c)
    c.hide_window()
    try:
        yield c
    finally:
        try:
            c._worker.shutdown()
        except Exception:
            pass
        if c._hotkeys:
            try:
                c._hotkeys.stop()
            except Exception:
                pass
        try:
            c.root.destroy()
        except Exception:
            pass
