"""Integration tests: the real MonitorController, a real (hidden) Tk window and a real
DdcWorker thread, driven against fake monitors."""
import gc
import os
import time
import weakref
from tkinter import ttk

import pytest

from tests.conftest import (FakeMonitor, create_control_window_with_retry, device_id, pump_tk,
                            run_steps_under_mainloop)


def settle(c, timeout=10):
    pump_tk(c.root, lambda: not c.discovering and not c._worker.busy(), timeout=timeout)


def names(c):
    return list(c.monitor_listbox.get(0, "end"))


# ------------------------------------------------------------------------------- discovery

def test_discovery_is_asynchronous_and_never_blocks_the_ui(controller, fake_monitor_env):
    fake_monitor_env["monitors"] = [FakeMonitor("AAA", caps_delay=0.5)]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    t0 = time.time()
    controller.start_discovery()
    assert time.time() - t0 < 0.2, "start_discovery must return immediately"
    assert names(controller) == ["Detecting monitors..."]
    slow_ticks = 0
    while controller.discovering:
        tick_start = time.time()
        controller.root.update()
        slow_ticks += (time.time() - tick_start) > 0.1
        time.sleep(0.005)
    assert slow_ticks == 0, "the UI thread must stay responsive during a slow probe"
    assert names(controller) == ["Dell AAA"]


def test_no_monitors_does_not_crash_the_ui(controller, fake_monitor_env):
    fake_monitor_env["monitors"] = []
    fake_monitor_env["device_ids"] = []
    controller.start_discovery()
    settle(controller)
    assert names(controller) == ["No monitors found"]
    assert controller.set_brightness(50) is False
    assert controller.set_contrast(50) is False


def test_failed_enumeration_keeps_the_previous_list_and_retries_on_next_open(controller, fake_monitor_env):
    fake_monitor_env["monitors"] = [FakeMonitor("AAA")]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)
    assert names(controller) == ["Dell AAA"]

    errors = []
    import monitorpy.controller as controller_module
    orig = controller_module.messagebox.showerror
    controller_module.messagebox.showerror = lambda *a, **k: errors.append(a)
    try:
        fake_monitor_env["fail"] = True
        controller.refresh_monitors()
        settle(controller)
        assert names(controller) == ["Dell AAA"] and errors
        assert controller._retry_on_open is True

        fake_monitor_env["fail"] = False
        probes_before = fake_monitor_env["probe_calls"]
        controller.show_control_window()  # window was hidden -> opening it retries once
        settle(controller)
        assert fake_monitor_env["probe_calls"] == probes_before + 1
        assert controller._retry_on_open is False
    finally:
        controller_module.messagebox.showerror = orig


# ------------------------------------------------------------------------- writes & coalescing

def test_slider_drag_is_instant_and_writes_are_coalesced(controller, fake_monitor_env):
    mon = FakeMonitor("AAA", write_delay=0.15)
    fake_monitor_env["monitors"] = [mon]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)

    t0 = time.time()
    for v in range(41, 91):
        controller.brightness_var.set(v)
        controller.update_brightness_label(v)  # what ttk.Scale's command does while dragging
    ui_time = time.time() - t0
    assert ui_time < 0.1, f"50 slider updates took {ui_time*1000:.0f} ms on the UI thread"
    settle(controller)
    writes = [v for f, v in mon.writes if f == "brightness"]
    assert writes[-1] == 90 and len(writes) < 10, writes


def test_release_with_unchanged_value_sends_nothing(controller, fake_monitor_env):
    mon = FakeMonitor("AAA")
    fake_monitor_env["monitors"] = [mon]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)
    n = len(mon.writes)
    controller.on_brightness_release()
    settle(controller)
    assert len(mon.writes) == n


def test_shortcut_and_preset_are_optimistic_and_then_applied(controller, fake_monitor_env):
    mon = FakeMonitor("AAA")
    fake_monitor_env["monitors"] = [mon]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)

    t0 = time.time()
    controller.increase_brightness()
    assert time.time() - t0 < 0.05 and controller.brightness_var.get() == 55

    controller.quick_preset_2()
    assert (controller.brightness_var.get(), controller.contrast_var.get()) == (30, 50)
    settle(controller)
    assert ("brightness", 30) in mon.writes and ("contrast", 50) in mon.writes


def test_failed_write_reverts_the_cached_value_and_the_ui(controller, fake_monitor_env):
    good, bad = FakeMonitor("AAA", brightness=40), FakeMonitor("BBB", brightness=40, fail_writes=True)
    fake_monitor_env["monitors"] = [good, bad]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1), device_id("SAM", 2)]
    controller.start_discovery()
    settle(controller)

    controller.monitor_listbox.selection_clear(0, "end")
    controller.monitor_listbox.select_set(1)
    controller.on_monitor_select()
    assert controller.brightness_var.get() == 40

    controller.set_brightness(80)
    controller.update_controls()
    assert controller.brightness_var.get() == 80  # optimistic

    settle(controller)
    assert controller.brightness_var.get() == 40
    assert controller.current_brightness == 40
    assert controller.monitor_infos[1]["brightness"] == 40


def test_quit_flushes_writes_queued_just_before_it(controller, fake_monitor_env):
    mon = FakeMonitor("AAA")
    fake_monitor_env["monitors"] = [mon]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)
    controller.set_brightness(11)
    controller.set_contrast(22)
    controller.quit_app()
    assert mon.writes[-2:] == [("brightness", 11), ("contrast", 22)]


# --------------------------------------------------------------------------- monitor refresh

def test_refresh_keeps_selection_and_rereads_values(controller, fake_monitor_env):
    aaa, bbb = FakeMonitor("AAA", brightness=11), FakeMonitor("BBB", brightness=22)
    fake_monitor_env["monitors"] = [aaa, bbb]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1), device_id("SAM", 2)]
    controller.start_discovery()
    settle(controller)
    controller.monitor_listbox.selection_clear(0, "end")
    controller.monitor_listbox.select_set(1)
    controller.on_monitor_select()

    bbb.brightness = 77  # changed via the monitor's own buttons, outside the app
    controller.refresh_monitors()
    settle(controller)
    assert controller.selected_monitor_index == 1 and controller.brightness_var.get() == 77


def test_refresh_ignored_while_already_running(controller, fake_monitor_env):
    fake_monitor_env["monitors"] = [FakeMonitor("AAA", caps_delay=0.3)]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)

    probes_before = fake_monitor_env["probe_calls"]
    controller.refresh_monitors()
    controller.refresh_monitors()
    controller.refresh_monitors()
    settle(controller)
    assert fake_monitor_env["probe_calls"] == probes_before + 1


def test_hotplug_detected_on_window_open_and_selection_survives_reindex(controller, fake_monitor_env):
    aaa, bbb = FakeMonitor("AAA"), FakeMonitor("BBB", brightness=77)
    fake_monitor_env["monitors"] = [aaa, bbb]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1), device_id("SAM", 2)]
    controller.start_discovery()
    settle(controller)
    controller.monitor_listbox.selection_clear(0, "end")
    controller.monitor_listbox.select_set(1)
    controller.on_monitor_select()

    ccc = FakeMonitor("CCC", brightness=5)
    fake_monitor_env["monitors"] = [ccc, aaa, bbb]
    fake_monitor_env["device_ids"] = [device_id("LEN", 3), device_id("DEL", 1), device_id("SAM", 2)]
    controller.show_control_window()  # was hidden -> opening it checks the configuration
    assert controller.discovering
    settle(controller)
    assert names(controller) == ["Lenovo CCC", "Dell AAA", "Samsung BBB"]
    assert controller.selected_monitor_index == 2 and controller.brightness_var.get() == 77


def test_unchanged_configuration_does_not_reprobe_on_open(controller, fake_monitor_env):
    fake_monitor_env["monitors"] = [FakeMonitor("AAA")]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)
    controller.hide_window()
    probes_before = fake_monitor_env["probe_calls"]
    controller.show_control_window()
    settle(controller)
    assert fake_monitor_env["probe_calls"] == probes_before


def test_unplugging_the_selected_monitor_falls_back_to_the_first(controller, fake_monitor_env):
    aaa, bbb = FakeMonitor("AAA"), FakeMonitor("BBB", brightness=5)
    fake_monitor_env["monitors"] = [aaa, bbb]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1), device_id("SAM", 2)]
    controller.start_discovery()
    settle(controller)
    controller.monitor_listbox.selection_clear(0, "end")
    controller.monitor_listbox.select_set(1)
    controller.on_monitor_select()

    fake_monitor_env["monitors"] = [aaa]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.show_control_window()
    settle(controller)
    assert names(controller) == ["Dell AAA"] and controller.selected_monitor_index == 0


def test_stale_write_result_from_before_a_refresh_is_ignored(controller, fake_monitor_env):
    fake_monitor_env["monitors"] = [FakeMonitor("AAA", brightness=40)]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)
    before = (controller.current_brightness, [i["brightness"] for i in controller.monitor_infos])
    controller._on_write_result((controller._generation - 1, 0, "brightness"), 99, False)
    controller._on_write_result((controller._generation - 1, 0, "brightness"), 99, True)
    after = (controller.current_brightness, [i["brightness"] for i in controller.monitor_infos])
    assert before == after


def test_old_monitor_objects_are_released_after_a_refresh(controller, fake_monitor_env):
    old = FakeMonitor("AAA")
    fake_monitor_env["monitors"] = [old]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_discovery()
    settle(controller)
    ref = weakref.ref(old)
    del old

    fake_monitor_env["monitors"] = [FakeMonitor("NEW")]
    fake_monitor_env["device_ids"] = [device_id("ACR", 9)]
    controller.refresh_monitors()
    settle(controller)
    gc.collect()
    assert ref() is None, "the previous Monitor object must be released (frees its Windows handle)"


# --------------------------------------------------------------------------------- UI details

def test_listbox_keeps_selection_when_focus_moves_to_an_entry(controller):
    controller.monitor_listbox.insert("end", "A")
    controller.monitor_listbox.insert("end", "B")
    controller.monitor_listbox.select_set(1)
    entry = ttk.Entry(controller.root)
    entry.pack()
    entry.insert(0, "12345")
    entry.selection_range(0, "end")
    controller.root.update()
    assert controller.monitor_listbox.curselection() == (1,)


def test_edit_presets_save_persists_and_closes(controller):
    controller.open_edit_presets_window()
    controller.root.update()

    def find(widget_cls, text):
        stack = [controller.edit_win]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            if isinstance(w, widget_cls) and w.cget("text") == text:
                return w

    find(ttk.Button, "Save").invoke()
    controller.root.update()
    assert controller.edit_win is None
    assert controller.config.preset_1 == {"brightness": 100, "contrast": 70}


def test_edit_presets_failed_save_reports_error_and_keeps_window_open(controller):
    import monitorpy.controller as controller_module
    errors = []
    orig = controller_module.messagebox.showerror
    controller_module.messagebox.showerror = lambda *a, **k: errors.append(a)
    try:
        controller.open_edit_presets_window()
        controller.root.update()
        controller.config.save = lambda: False

        def find_save():
            stack = [controller.edit_win]
            while stack:
                w = stack.pop()
                stack.extend(w.winfo_children())
                if isinstance(w, ttk.Button) and w.cget("text") == "Save":
                    return w

        find_save().invoke()
        controller.root.update()
        assert errors and controller.edit_win is not None and controller.edit_win.winfo_exists()
    finally:
        controller_module.messagebox.showerror = orig


def test_dpi_scaling_keeps_the_layout_from_clipping(fake_monitor_env, monkeypatch):
    import tkinter as tk
    from monitorpy.controller import MonitorController

    for scale in (1.0, 1.25, 1.5, 2.0, 3.0):
        class ScaledTk(tk.Tk):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.tk.call("tk", "scaling", scale * 96 / 72)

        monkeypatch.setattr(tk, "Tk", ScaledTk)
        c = MonitorController()
        try:
            create_control_window_with_retry(c)
            c.root.update_idletasks()
            frame = c.root.winfo_children()[0]
            need_w, need_h = frame.winfo_reqwidth(), frame.winfo_reqheight()
            have_w, have_h = c.root.winfo_width(), c.root.winfo_height()
            assert abs(c.ui_scale - scale) < 0.01
            assert (have_w, have_h) == (c._px(320), c._px(200))
            assert need_w <= have_w and need_h <= have_h, f"content clips at {scale:.0%} scaling"
        finally:
            c._worker.shutdown()
            c.root.destroy()
        monkeypatch.undo()


# ------------------------------------------------------------------------------ end-to-end hotkeys

def test_global_hotkeys_drive_brightness_and_contrast_while_hidden(controller, fake_monitor_env):
    """Sends real key events through Windows; only meaningful on an interactive desktop."""
    import ctypes

    mon = FakeMonitor("AAA")
    fake_monitor_env["monitors"] = [mon]
    fake_monitor_env["device_ids"] = [device_id("DEL", 1)]
    controller.start_hotkeys()
    try:
        assert controller._hotkeys.failed == [], controller._hotkeys.failed
        controller.start_discovery()

        user32 = ctypes.windll.user32
        KEYUP = 0x0002

        def press(modifier_vk, vk):
            user32.keybd_event(modifier_vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, KEYUP, 0)
            user32.keybd_event(modifier_vk, 0, KEYUP, 0)

        from monitorpy.hotkeys import VK_F10, VK_F11
        CTRL, ALT = 0x11, 0x12

        # A hotkey lands via HotkeyListener's thread calling controller.root.after(), which
        # Tkinter only honors while the main thread is genuinely inside mainloop() - so this
        # must be driven by run_steps_under_mainloop(), not repeated root.update() calls.
        steps = [
            (lambda: None, lambda: not controller.discovering),
            (lambda: None, lambda: controller.root.state() == "withdrawn"),
            (lambda: press(CTRL, VK_F11), lambda: controller.current_brightness == 55),
            (lambda: press(CTRL, VK_F10), lambda: controller.current_brightness == 50),
            (lambda: press(ALT, VK_F11), lambda: controller.current_contrast == 55),
            (lambda: press(ALT, VK_F10), lambda: controller.current_contrast == 50),
            (lambda: None, lambda: not controller._worker.busy()),
        ]
        run_steps_under_mainloop(controller.root, steps)
        assert ("brightness", 55) in mon.writes and ("contrast", 55) in mon.writes
    finally:
        controller._hotkeys.stop()
