"""DPI awareness and the single-instance mutex. The mutex is tested with real
subprocesses since it is meant to work *across* processes."""
import subprocess
import sys
import time

SRC = None  # set in setup_module


def setup_module():
    global SRC
    import os
    SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(code, timeout=30):
    return subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{SRC}')\n" + code],
        capture_output=True, text=True, timeout=timeout)


def test_dpi_awareness_is_set_and_idempotent():
    r = run('''
import ctypes
from monitorpy.platform_win import enable_dpi_awareness

def awareness():
    v = ctypes.c_int(-1)
    ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(v))
    return v.value

before = awareness()
enable_dpi_awareness()
after = awareness()
enable_dpi_awareness()  # calling twice must be harmless
again = awareness()
print(before, after, again)
''')
    assert r.returncode == 0, r.stderr
    before, after, again = map(int, r.stdout.split())
    assert before == 0, "test process must start DPI-unaware for this to be meaningful"
    assert after == 1 and again == 1  # PROCESS_SYSTEM_DPI_AWARE


def test_single_instance_blocks_a_second_process_and_releases_on_exit():
    holder = subprocess.Popen(
        [sys.executable, "-c", f"""
import sys, time
sys.path.insert(0, r'{SRC}')
from monitorpy.platform_win import acquire_single_instance
assert acquire_single_instance("Local\\\\MonitorPyPytest-SingleInstance") is True
print('held', flush=True)
time.sleep(6)
"""],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"

        r = run('''
from monitorpy.platform_win import acquire_single_instance
print(acquire_single_instance("Local\\\\MonitorPyPytest-SingleInstance"))
''')
        assert r.stdout.strip() == "False", (r.stdout, r.stderr)

        holder.wait(timeout=15)
        r = run('''
from monitorpy.platform_win import acquire_single_instance
print(acquire_single_instance("Local\\\\MonitorPyPytest-SingleInstance"))
''')
        assert r.stdout.strip() == "True"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()


def test_single_instance_lock_is_released_even_if_the_holder_is_killed():
    holder = subprocess.Popen(
        [sys.executable, "-c", f"""
import sys, time
sys.path.insert(0, r'{SRC}')
from monitorpy.platform_win import acquire_single_instance
acquire_single_instance("Local\\\\MonitorPyPytest-KillTest")
print('held', flush=True)
time.sleep(60)
"""],
        stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    holder.kill()
    holder.wait()
    r = run('''
from monitorpy.platform_win import acquire_single_instance
print(acquire_single_instance("Local\\\\MonitorPyPytest-KillTest"))
''')
    assert r.stdout.strip() == "True"
