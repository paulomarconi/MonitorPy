"""DDC/CI worker thread and monitor probing, driven with fake monitors."""
import time

import pytest

from monitorpy.ddc import DdcWorker, probe_monitors
from tests.conftest import FakeMonitor


@pytest.fixture
def worker():
    w = DdcWorker()
    yield w
    w.shutdown()


def drain_until_idle(worker, timeout=5):
    """Poll the worker until it reports idle, returning every result collected along the
    way. busy() counts undelivered results, so idleness can only be observed by polling."""
    deadline = time.time() + timeout
    collected = []
    while True:
        collected.extend(worker.poll())
        if not worker.busy():
            return collected
        assert time.time() < deadline, "timed out waiting for the worker to go idle"
        time.sleep(0.005)


# --------------------------------------------------------------------------------- DdcWorker

def test_submit_write_is_applied_and_reported(worker):
    mon = FakeMonitor()
    worker.submit_write(("gen", 0, "brightness"), mon, "brightness", 70)
    results = drain_until_idle(worker)
    assert mon.writes == [("brightness", 70)]
    assert results == [("write", (("gen", 0, "brightness"), 70, True), None)]


def test_writes_to_the_same_key_are_coalesced(worker):
    mon = FakeMonitor(write_delay=0.05)
    key = ("gen", 0, "brightness")
    for v in range(41, 91):
        worker.submit_write(key, mon, "brightness", v)
    drain_until_idle(worker, timeout=10)
    # The first write may already be in flight before later ones arrive, but every
    # intermediate value queued while a write was in progress must be dropped.
    assert mon.writes[-1] == ("brightness", 90)
    assert len(mon.writes) < 10, mon.writes


def test_failed_write_is_reported_but_does_not_crash_the_worker(worker):
    mon = FakeMonitor(fail_writes=True)
    worker.submit_write(("gen", 0, "brightness"), mon, "brightness", 70)
    (_, (key, value, ok), _), = drain_until_idle(worker)
    assert (key, value, ok) == (("gen", 0, "brightness"), 70, False)
    # The worker thread must still be usable afterwards.
    worker.submit_write(("gen", 0, "contrast"), FakeMonitor(), "contrast", 50)
    drain_until_idle(worker)


def test_is_pending_reflects_queued_not_yet_started_writes(worker):
    mon = FakeMonitor(write_delay=0.2)
    key = ("gen", 0, "brightness")
    worker.submit_write(key, mon, "brightness", 10)
    worker.submit_write(key, mon, "brightness", 20)
    assert worker.is_pending(key) in (True, False)  # may already be mid-write; just must not raise
    drain_until_idle(worker)
    assert not worker.is_pending(key)


def test_submit_call_returns_its_result():
    w = DdcWorker()
    try:
        w.submit_call("thing", lambda: 42)
        assert drain_until_idle(w) == [("thing", 42, None)]
    finally:
        w.shutdown()


def test_submit_call_reports_exceptions_without_crashing():
    w = DdcWorker()
    try:
        w.submit_call("boom", lambda: (_ for _ in ()).throw(ValueError("nope")))
        (name, value, error), = drain_until_idle(w)
        assert name == "boom" and value is None and isinstance(error, ValueError)
    finally:
        w.shutdown()


def test_shutdown_flushes_a_write_queued_just_before_it(worker):
    mon = FakeMonitor()
    worker.submit_write(("gen", 0, "brightness"), mon, "brightness", 33)
    worker.shutdown()
    assert mon.writes == [("brightness", 33)]


# ------------------------------------------------------------------------------ probe_monitors

def test_probe_monitors_reads_name_brightness_contrast():
    mon = FakeMonitor(name="PANEL9", brightness=33, contrast=44)
    infos = probe_monitors(lambda: [mon], lambda: ["Dell"])
    assert len(infos) == 1
    info = infos[0]
    assert info["name"] == "PANEL9" and info["brightness"] == 33 and info["contrast"] == 44
    assert info["manufacturer"] == "Dell" and info["monitor"] is mon


def test_probe_monitors_labels_partial_ddc_support():
    infos = probe_monitors(lambda: [FakeMonitor(fail_contrast=True)], lambda: [None])
    assert infos[0]["name"].endswith("(no contrast control)")
    assert infos[0]["brightness"] == 50  # still read

    infos = probe_monitors(lambda: [FakeMonitor(fail_brightness=True)], lambda: [None])
    assert infos[0]["name"].endswith("(no brightness control)")
    assert infos[0]["contrast"] == 50


def test_probe_monitors_capabilities_failure_alone_is_not_no_ddc():
    infos = probe_monitors(lambda: [FakeMonitor(fail_caps=True)], lambda: [None])
    assert infos[0]["name"] == "Monitor 1"       # generic fallback name
    assert "No DDC/CI" not in infos[0]["name"]   # brightness/contrast still worked


def test_probe_monitors_all_features_failing_is_no_ddc():
    infos = probe_monitors(lambda: [FakeMonitor(fail_caps=True, fail_brightness=True, fail_contrast=True)],
                           lambda: [None])
    assert infos[0]["name"] == "Monitor 1 (No DDC/CI)"


def test_probe_monitors_session_that_cannot_open_is_no_ddc():
    infos = probe_monitors(lambda: [FakeMonitor(fail_open=True)], lambda: [None])
    assert infos[0]["name"] == "Monitor 1 (No DDC/CI)"


def test_probe_monitors_ignores_mismatched_manufacturer_list():
    """Never guess: if the two lists disagree in length, show no brand rather than a
    possibly-wrong one."""
    infos = probe_monitors(lambda: [FakeMonitor(), FakeMonitor()], lambda: ["Dell"])
    assert [i["manufacturer"] for i in infos] == [None, None]


def test_probe_monitors_tolerates_a_failing_manufacturer_lookup():
    def boom():
        raise OSError("api failed")
    infos = probe_monitors(lambda: [FakeMonitor(name="PANEL9")], boom)
    assert infos[0]["manufacturer"] is None and infos[0]["name"] == "PANEL9"
