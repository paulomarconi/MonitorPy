"""Loading and saving MonitorPy's settings (presets + theme) as JSON.

Pure functions (sanitize_preset, parse_config, write_json_atomic) have no dependency on
Tk or the rest of the app, so they are unit-testable on their own. ConfigStore wraps them
into the stateful object the controller uses; it is also independent of Tk, so it can be
tested without creating a window.
"""
import json
import os
import threading
import time

from monitorpy.logutil import log

CONFIG_VERSION = 1
THEMES = ("light", "dark", "system")
DEFAULT_THEME = "system"
PRESET_FIELDS = ("brightness", "contrast")
DEFAULT_PRESET_1 = {"brightness": 100, "contrast": 70}
DEFAULT_PRESET_2 = {"brightness": 30, "contrast": 50}


def config_dir():
    appdata = os.environ.get('APPDATA')
    return os.path.join(appdata, "MonitorPy") if appdata else os.path.join(os.path.expanduser("~"), ".MonitorPy")


def config_path():
    """Path of config.json. No side effects; the folder is created when saving."""
    return os.path.join(config_dir(), "config.json")


def legacy_config_path():
    """presets.json, the name used before the theme setting made it a general config."""
    return os.path.join(config_dir(), "presets.json")


def sanitize_preset(raw, default, name="preset"):
    """A valid {'brightness': 0-100, 'contrast': 0-100} built from loaded JSON.
    Missing or invalid fields fall back to `default` (and invalid ones are logged)."""
    result = dict(default)
    if not isinstance(raw, dict):
        log.warning("Ignoring %s: expected an object, got %r", name, raw)
        return result
    for field in PRESET_FIELDS:
        if field not in raw:
            continue
        value = raw[field]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
            result[field] = value
        else:
            log.warning("Ignoring invalid %s.%s = %r (need an integer 0-100)", name, field, value)
    return result


def parse_config(data, preset_1, preset_2, theme):
    """Merge loaded JSON over the given defaults. Returns (preset_1, preset_2, theme, extra) where
    `extra` holds unknown keys so a newer version's settings survive being re-saved by this one."""
    if not isinstance(data, dict):
        log.warning("Ignoring config: expected an object, got %s", type(data).__name__)
        return preset_1, preset_2, theme, {}
    if "preset_1" in data:
        preset_1 = sanitize_preset(data["preset_1"], preset_1, "preset_1")
    if "preset_2" in data:
        preset_2 = sanitize_preset(data["preset_2"], preset_2, "preset_2")
    if "theme" in data:
        if data["theme"] in THEMES:
            theme = data["theme"]
        else:
            log.warning("Ignoring unknown theme %r", data["theme"])
    known = {"version", "preset_1", "preset_2", "theme"}
    return preset_1, preset_2, theme, {k: v for k, v in data.items() if k not in known}


def write_json_atomic(path, obj):
    """Write JSON so that `path` is always either the old or the new complete file: write a
    temp file in the same folder, flush it to disk, then replace. A crash or full disk in the
    middle can never leave a truncated config behind. The retry only smooths over a brief,
    external lock (antivirus, backup, indexer); it is not a substitute for the caller
    serializing its own concurrent writers - ConfigStore does that with a lock."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        attempts = 5
        for attempt in range(attempts):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # target briefly locked (antivirus, backup, indexer)
                if attempt == attempts - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class ConfigStore:
    """Presets and theme, loaded on construction and saved on demand. Independent of Tk;
    save() takes an internal lock, so it is safe to call from more than one thread
    (the tray thread saves the theme, the Tk thread saves presets)."""

    def __init__(self):
        self.preset_1 = dict(DEFAULT_PRESET_1)
        self.preset_2 = dict(DEFAULT_PRESET_2)
        self.theme = DEFAULT_THEME
        self._extra = {}
        self._lock = threading.Lock()
        self.load()

    def load(self):
        path, legacy = config_path(), False
        if not os.path.exists(path):
            old = legacy_config_path()
            if not os.path.exists(old):
                return  # first run: defaults
            path, legacy = old, True
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except OSError as e:
            log.error("Could not read %s: %s", path, e)
            return
        except ValueError as e:  # malformed JSON / bad encoding
            log.error("Config %s is corrupt (%s); using defaults", path, e)
            try:
                os.replace(path, path + ".bad")  # keep it for inspection instead of silently overwriting it
                log.info("Moved the corrupt config to %s.bad", path)
            except OSError:
                pass
            return
        self.preset_1, self.preset_2, self.theme, self._extra = parse_config(
            data, self.preset_1, self.preset_2, self.theme)
        if legacy:
            # Migrate presets.json -> config.json; only drop the old file once the new one is safely written.
            if self.save():
                try:
                    os.remove(path)
                    log.info("Migrated %s to %s", path, config_path())
                except OSError as e:
                    log.warning("Could not remove old %s: %s", path, e)

    def save(self):
        """Persist presets and theme. Returns True on success. Safe to call from any thread."""
        data = dict(self._extra)
        data.update(version=CONFIG_VERSION, preset_1=self.preset_1, preset_2=self.preset_2, theme=self.theme)
        with self._lock:
            try:
                write_json_atomic(config_path(), data)
                return True
            except Exception as e:
                log.error("Failed to save config: %s", e)
                return False
