"""Loading/saving presets and theme. Pure logic - no Tk, no monitors."""
import glob
import json
import os
import threading

import pytest

from monitorpy import config

DEFAULT_1, DEFAULT_2 = dict(config.DEFAULT_PRESET_1), dict(config.DEFAULT_PRESET_2)


def cfg_path():
    return config.config_path()


def legacy_path():
    return config.legacy_config_path()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content if isinstance(content, str) else json.dumps(content))


# ---------------------------------------------------------------- sanitize_preset / parse_config

@pytest.mark.parametrize("raw, expected", [
    ({"brightness": 150, "contrast": -5}, DEFAULT_1),           # out of range
    ({"brightness": "70", "contrast": True}, DEFAULT_1),        # wrong types (bool excluded even though bool is int)
    ({"brightness": 70.0, "contrast": 3.5}, {"brightness": 70, "contrast": 70}),  # whole float ok, fractional rejected
    ({"brightness": 0, "contrast": 100}, {"brightness": 0, "contrast": 100}),      # boundaries accepted
    ("oops", DEFAULT_1),                                          # not a dict at all
    ({"brightness": 55}, {"brightness": 55, "contrast": 70}),    # partial preset keeps the other field
    ({}, DEFAULT_1),
])
def test_sanitize_preset(raw, expected):
    assert config.sanitize_preset(raw, DEFAULT_1) == expected


def test_parse_config_validation_matrix():
    bad = {"preset_1": {"brightness": 150, "contrast": -5},
           "preset_2": {"brightness": "70", "contrast": True},
           "theme": "blue"}
    p1, p2, theme, extra = config.parse_config(bad, DEFAULT_1, DEFAULT_2, "system")
    assert (p1, p2, theme) == (DEFAULT_1, DEFAULT_2, "system")


def test_parse_config_rejects_non_dict_top_level():
    p1, p2, theme, extra = config.parse_config([1, 2, 3], DEFAULT_1, DEFAULT_2, "system")
    assert (p1, p2, theme, extra) == (DEFAULT_1, DEFAULT_2, "system", {})


def test_parse_config_rejects_non_dict_presets():
    p1, p2, theme, extra = config.parse_config({"preset_1": "oops", "preset_2": [1, 2]},
                                                DEFAULT_1, DEFAULT_2, "system")
    assert (p1, p2) == (DEFAULT_1, DEFAULT_2)


def test_parse_config_preserves_unknown_keys_for_forward_compatibility():
    p1, p2, theme, extra = config.parse_config(
        {"theme": "dark", "future_option": {"x": 1}, "version": 7}, DEFAULT_1, DEFAULT_2, "system")
    assert theme == "dark" and extra == {"future_option": {"x": 1}}


# ---------------------------------------------------------------------------- write_json_atomic

def test_write_json_atomic_creates_valid_file(tmp_path):
    path = str(tmp_path / "sub" / "config.json")
    config.write_json_atomic(path, {"a": 1})
    assert json.load(open(path)) == {"a": 1}


def test_write_json_atomic_failure_leaves_previous_file_untouched(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    config.write_json_atomic(path, {"theme": "dark"})
    before = open(path, "rb").read()

    def half_dump(obj, f, **k):
        f.write('{"theme": "li')
        raise OSError("disk full")

    monkeypatch.setattr(config.json, "dump", half_dump)
    with pytest.raises(OSError):
        config.write_json_atomic(path, {"theme": "light"})
    assert open(path, "rb").read() == before
    assert not glob.glob(path + "*.tmp"), "a failed write must not leave a temp file behind"


def test_write_json_atomic_retries_a_transient_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    config.write_json_atomic(path, {"a": 1})
    calls = {"n": 0}
    real_replace = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr(config.os, "replace", flaky)
    config.write_json_atomic(path, {"a": 2})
    assert calls["n"] == 3 and json.load(open(path)) == {"a": 2}  # succeeds within the retry budget


def test_write_json_atomic_gives_up_after_persistent_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    config.write_json_atomic(path, {"a": 1})
    monkeypatch.setattr(config.os, "replace",
                         lambda *a: (_ for _ in ()).throw(PermissionError("locked forever")))
    with pytest.raises(PermissionError):
        config.write_json_atomic(path, {"a": 2})
    assert not glob.glob(path + "*.tmp")


# Note: write_json_atomic() is not tested with multiple unsynchronized concurrent
# callers - its docstring says that is the caller's job. In this app, ConfigStore.save()
# is the only caller and it holds a lock, which test_concurrent_saves_from_multiple_threads_
# never_corrupt_the_file() below exercises. Calling write_json_atomic() itself from several
# threads at once races Windows' os.replace() onto the same destination, which can and does
# transiently fail (ERROR_ACCESS_DENIED) beyond any fixed retry budget - a real Windows
# limitation, not a bug, and not a path this app ever takes.


# ---------------------------------------------------------------------------------- ConfigStore

def test_first_run_uses_defaults_and_writes_nothing():
    store = config.ConfigStore()
    assert (store.preset_1, store.preset_2, store.theme) == (DEFAULT_1, DEFAULT_2, "system")
    assert not os.path.exists(cfg_path())


def test_save_writes_a_versioned_config():
    store = config.ConfigStore()
    assert store.save() is True
    assert json.load(open(cfg_path())) == {
        "version": 1, "preset_1": DEFAULT_1, "preset_2": DEFAULT_2, "theme": "system"}


def test_migrates_legacy_presets_json():
    """This is the exact content of a real presets.json from before the config.json rename."""
    write(legacy_path(), {"preset_1": {"brightness": 100, "contrast": 70},
                          "preset_2": {"brightness": 30, "contrast": 50}, "theme": "light"})
    store = config.ConfigStore()
    assert store.theme == "light" and store.preset_2 == DEFAULT_2
    assert os.path.exists(cfg_path()) and not os.path.exists(legacy_path())
    # And the migration is durable: a second start reads config.json, not presets.json.
    assert config.ConfigStore().theme == "light"


def test_failed_migration_write_keeps_the_legacy_file(monkeypatch):
    write(legacy_path(), {"theme": "dark"})
    monkeypatch.setattr(config, "write_json_atomic",
                         lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    store = config.ConfigStore()
    assert store.theme == "dark"
    assert os.path.exists(legacy_path()) and not os.path.exists(cfg_path())


def test_corrupt_config_falls_back_to_defaults_and_preserves_the_bad_file():
    write(cfg_path(), '{"preset_1": {')  # truncated JSON
    store = config.ConfigStore()
    assert (store.preset_1, store.theme) == (DEFAULT_1, "system")
    assert os.path.exists(cfg_path() + ".bad") and not os.path.exists(cfg_path())
    # Saving afterwards must still work and not clobber the .bad copy.
    store.save()
    assert json.load(open(cfg_path()))["theme"] == "system"
    assert os.path.exists(cfg_path() + ".bad")


@pytest.mark.parametrize("junk", ["", "\x00\x01\x02 not json", '{"theme": "dark"} trailing garbage'])
def test_other_kinds_of_corruption_also_fall_back_safely(junk):
    write(cfg_path(), junk)
    store = config.ConfigStore()
    assert store.theme == "system"
    assert os.path.exists(cfg_path() + ".bad")


def test_edit_presets_and_save_round_trip():
    store = config.ConfigStore()
    store.preset_1["brightness"] = 42
    assert store.save()
    assert config.ConfigStore().preset_1["brightness"] == 42


def test_concurrent_saves_from_multiple_threads_never_corrupt_the_file():
    store = config.ConfigStore()
    errors = []

    def hammer(i):
        try:
            for n in range(40):
                store.theme = ("light", "dark", "system")[(i + n) % 3]
                if not store.save():
                    errors.append("save failed")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert json.load(open(cfg_path()))["theme"] in config.THEMES
    assert not glob.glob(cfg_path() + "*.tmp")
