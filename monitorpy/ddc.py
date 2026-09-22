"""Talking to monitors over DDC/CI: the background worker thread, and probing a fresh
list of monitors into the info dicts the UI displays.

Nothing here touches Tk; probe_monitors() and DdcWorker are driven from a background
thread and reported back to the controller through a queue.
"""
import queue
import threading

from monitorpy.logutil import log


def probe_monitors(get_monitors_fn, get_manufacturers_fn):
    """Blocking: enumerate monitors and read their name/brightness/contrast/manufacturer.
    Must run on a worker thread, not the Tk thread (DDC/CI can take seconds per monitor)."""
    monitors = get_monitors_fn()
    try:
        manufacturers = get_manufacturers_fn()
    except Exception as e:
        log.warning("Could not read monitor manufacturers: %s", e)
        manufacturers = []
    if len(manufacturers) != len(monitors):
        # Never guess: a wrong brand is worse than none.
        log.warning("Manufacturer list (%d) does not match monitor list (%d); ignoring it",
                    len(manufacturers), len(monitors))
        manufacturers = [None] * len(monitors)

    infos = []
    for i, monitor in enumerate(monitors):
        info = {"monitor": monitor, "name": f"Monitor {i+1}", "brightness": 50, "contrast": 50,
                "manufacturer": manufacturers[i], "supported": {}}
        # Read each feature in its own transaction: a monitor that cannot report one
        # (many do not support contrast, or return no capabilities string) may still
        # work for the others.
        try:
            with monitor:
                caps = _probe_feature(i, "capabilities", monitor.get_vcp_capabilities)
                if isinstance(caps, dict) and caps.get("model"):
                    info["name"] = caps["model"]
                for feature, reader in (("brightness", monitor.get_luminance),
                                        ("contrast", monitor.get_contrast)):
                    value = _probe_feature(i, feature, reader)
                    info["supported"][feature] = value is not None
                    if value is not None:
                        info[feature] = value
        except Exception as e:
            log.warning("Monitor %d: could not open DDC/CI session: %s", i, e)
        supported = info["supported"]
        if not any(supported.values()):
            info["name"] += " (No DDC/CI)"
        else:
            missing = [f for f in ("brightness", "contrast") if not supported.get(f)]
            if missing:
                info["name"] += f" (no {' or '.join(missing)} control)"
        log.info("Monitor %d: %s | manufacturer=%s", i, info["name"], info["manufacturer"])
        infos.append(info)
    return infos


def _probe_feature(index, what, reader):
    try:
        return reader()
    except Exception as e:
        log.warning("Monitor %d: reading %s failed: %s", index, what, e)
        return None


class DdcWorker:
    """Runs every DDC/CI call on one background thread.

    DDC/CI transactions take 50 ms to several seconds, so they must never run on the Tk
    thread. Writes are coalesced: if a newer value for the same (monitor, feature) arrives
    before the previous one was sent, only the newest is written, so dragging a slider
    cannot build up a backlog. Results are collected in a queue that the Tk thread polls.
    """

    def __init__(self):
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._lock = threading.Lock()
        self._pending = {}      # key -> (monitor, feature, value); newest value wins
        self._outstanding = 0   # jobs queued or running
        self._thread = threading.Thread(target=self._loop, name="ddc-worker", daemon=True)
        self._thread.start()

    def submit_write(self, key, monitor, feature, value):
        with self._lock:
            queued = key in self._pending
            self._pending[key] = (monitor, feature, value)
            if not queued:
                self._outstanding += 1
        if not queued:
            self._jobs.put(("write", key))

    def submit_call(self, name, fn):
        """Run fn() on the worker; the result arrives as (name, value, error)."""
        with self._lock:
            self._outstanding += 1
        self._jobs.put(("call", name, fn))

    def is_pending(self, key):
        with self._lock:
            return key in self._pending

    def busy(self):
        with self._lock:
            return self._outstanding > 0 or not self._results.empty()

    def poll(self):
        """Return all results produced so far (non-blocking)."""
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                return out

    def shutdown(self, timeout=2.0):
        """Let queued writes finish (up to timeout seconds), then stop the thread."""
        self._jobs.put(None)
        self._thread.join(timeout)

    def _loop(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                if job[0] == "write":
                    self._do_write(job[1])
                else:
                    _, name, fn = job
                    try:
                        self._results.put((name, fn(), None))
                    except Exception as e:
                        log.exception("DDC job %s failed", name)
                        self._results.put((name, None, e))
            finally:
                with self._lock:
                    self._outstanding -= 1

    def _do_write(self, key):
        with self._lock:
            monitor, feature, value = self._pending.pop(key)
        try:
            with monitor:
                if feature == "brightness":
                    monitor.set_luminance(value)
                else:
                    monitor.set_contrast(value)
            ok = True
        except Exception as e:
            log.error("Failed to set %s to %d on monitor %d: %s", feature, value, key[1], e)
            ok = False
        self._results.put(("write", (key, value, ok), None))
