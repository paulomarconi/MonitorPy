# MonitorPy — Handoff

> Written 2026-09-29 against commit `eda9007` (tag `v1.0.6`, branch `master`).
> Updated 2026-11-19: Python environment is Miniconda `base` (see §2).
> Updated 2026-11-19: §6 items 1–5, 15, 17, 18, 20 (UPX), 23, 25 **done but uncommitted** in
> the working copy (`__version__` is now `1.0.7`, not yet tagged/released). 100 unit tests pass.
> This is meant for **any AI coding agent (Claude Code, Copilot, Codex, Cursor, …) or human**
> picking up the project on another machine. It covers the project, how to get it running,
> the conventions to keep, and a prioritized list of possible improvements.
> Line numbers may drift; search for the quoted symbol if a reference is off.

---

## 1. What this is

MonitorPy is a small **Windows 10/11 system-tray app** (Python + Tkinter + pystray) that
controls **brightness and contrast of external monitors over DDC/CI**. Features: monitor
list, two sliders, two presets ("Day"/"Night"), global hotkeys (`Ctrl+F10/F11` brightness,
`Alt+F10/F11` contrast), light/dark/system theme (sv-ttk), autostart via the HKCU Run key,
single-instance guard, DPI awareness, hot-plug re-detection. Shipped as a one-file
PyInstaller `MonitorPy.exe` on GitHub Releases.

- Repo: https://github.com/paulomarconi/MonitorPy (remote `origin`, default branch `master`)
- License: MIT. Author: paulomarconi.
- **Windows-only.** Uses `winreg`, `ctypes.windll`, `dxva2`, `RegisterHotKey`. Nothing
  imports cleanly on Linux/macOS, and tests will not run there either.

## 2. Setting up on a new machine

The whole project folder (including `build/`, `dist/`, `.vscode/`) is synchronized to other
machines through **OneDrive** (`OneDrive\Projects\MonitorPy`); no git clone needed.

### Python environment: Miniconda `base` (the owner's choice)

The owner uses **Miniconda's `base` environment**, not a venv and not the python.org Python.
From `cmd.exe`:

```bat
C:\Users\Paulo\miniconda3\Scripts\activate && conda activate base
cd %USERPROFILE%\OneDrive\Projects\MonitorPy
pip install -r requirements.txt      :: only if a dependency is missing
python -m pytest -m "not hardware"   :: unit tests, no real monitors needed
python MonitorPy.py                  :: run the app (look in the tray)
pyinstaller MonitorPy.spec           :: optional: build dist\MonitorPy.exe
```

As of 2026-11-19 on the original PC, `base` has Python **3.13.12** (same minor version as CI),
Tk 8.6, and every dependency already installed (`pystray`, `pillow`, `monitorcontrol` 4.2.0,
`sv_ttk`, `darkdetect`, `pyinstaller`, `pytest`).

Notes for agents (non-interactive shells):
- `activate` is a `cmd.exe` script and `conda activate` needs `conda init` for other shells.
  From the Bash/PowerShell tools, calling the interpreter directly is fine **for tests**:
  `C:\Users\Paulo\miniconda3\python.exe -m pytest -m "not hardware"`.
- **Always build the exe inside the activated environment**, e.g. from PowerShell:
  `cmd /c "C:\Users\Paulo\miniconda3\Scripts\activate && conda activate base && pyinstaller MonitorPy.spec --noconfirm"`.
  Activation puts `miniconda3\Library\bin` on `PATH`; without it PyInstaller can't find
  `tcl86t.dll`, `tk86t.dll`, `ffi.dll`, `libexpat.dll` (build log: "Library not found"), the
  build still "succeeds", and the exe dies at startup with *"DLL load failed while importing
  _tkinter"* (happened 2026-11-19). A good build is ~19.6 MB; the broken one was ~17 MB.
- On another machine, Miniconda may be installed under a different user folder; check
  `%USERPROFILE%\miniconda3` and ask the owner if `conda` isn't found. Don't fall back to the
  python.org Python (`C:\Python314` here: Python 3.14, no project dependencies installed).
- Don't create a venv or a new conda env unless the owner asks. (A dedicated env, e.g.
  `conda create -n monitorpy python=3.13`, was discussed as an option to isolate the project
  from other `base` packages, but `base` is the current choice.)
- Conda environments live in `miniconda3`, outside OneDrive, so nothing environment-related
  gets synced between machines; set it up once per machine.
- Releases are unaffected: the published exe is built by GitHub CI with python.org Python 3.13.
  A local `dist\` build from conda is only for local testing.

Other notes:
- **Don't commit, tag, or push** unless the owner explicitly asks; syncing happens via OneDrive.
- **Close MonitorPy before running the tests.** If `MonitorPy.exe` (or `MonitorPy.py`) is
  running, it holds `Ctrl/Alt+F10/F11` and 3 hotkey tests fail with
  `WinError 1409: Hot key is already registered` (seen 2026-11-19: 83 passed, 3 failed).
- `.gitignore` is an **allow-list** (`/*` then `!file` exceptions). Any new top-level file
  (e.g. `CLAUDE.md`, `pyproject.toml`, a new workflow) must be added there with a `!` line
  or git will silently ignore it. `handoff.md` has already been added. (`.gitignore` itself
  is not tracked by git, but OneDrive syncs it.)
- Hardware tests: `pytest -m hardware` touches real connected monitors (reads, and some
  write-then-restore). Only run them on a machine with a DDC/CI-capable external monitor.
- Tests redirect `%APPDATA%` and rename the Run-key value (`tests/conftest.py`), so they
  never touch the real config or autostart entry. Keep it that way for new tests.

## 3. Code map

| File | Role |
|---|---|
| `MonitorPy.py` | Entry point only (kept top-level for `python MonitorPy.py` and PyInstaller). |
| `monitorpy/app.py` | `main()`: DPI awareness → import controller (friendly error if a package is missing) → single-instance mutex → repair stale autostart → `MonitorController().run()`. |
| `monitorpy/controller.py` | **~785 lines.** Tray menu, Tk window, edit-presets window, all UI state, threading hand-offs. The big one. |
| `monitorpy/ddc.py` | `DdcWorker` (single background thread for every DDC/CI call, coalesces writes per `(generation, idx, feature)` key) and `probe_monitors()`. |
| `monitorpy/monitors.py` | EDID manufacturer lookup + `get_display_signature()` for hot-plug detection (no DDC traffic, cheap). |
| `monitorpy/hotkeys.py` | `HotkeyListener`: own thread with a Win32 message loop; `HOTKEYS` table. |
| `monitorpy/config.py` | `ConfigStore` + pure helpers; atomic JSON write; migrates `presets.json` → `config.json`; preserves unknown keys. |
| `monitorpy/autostart.py` | HKCU `Run` key; cleans up legacy Startup-folder `.lnk`/`.vbs`; `repair_stale_entry()` re-points autostart if the exe was moved. |
| `monitorpy/icon.py` | Loads `MonitorPy.ico` or draws the icon with Pillow. |
| `monitorpy/platform_win.py` | DPI awareness, named-mutex single instance. |
| `monitorpy/logutil.py` | Rotating log in `%APPDATA%\MonitorPy\monitorpy.log` (attached on import). |
| `tests/` | pytest suite with a `FakeMonitor`; `hardware` marker for real-monitor tests. |
| `.github/workflows/release.yml` | On tag push `v*`: check tag == `v{__version__}`, install, `pytest -m "not hardware"`, PyInstaller, create GitHub Release with the exe. |
| `.github/workflows/ci.yml` | On push / PR to `master` (and manual): install, `pytest -m "not hardware"`. |

### Threading model (read before touching controller.py)
- **Tk main thread** owns all widgets and controller state.
- **pystray thread** runs the tray; every menu callback must run on the Tk thread. Decorate
  such methods with `@on_tk_thread` (`controller.py`), which re-schedules the call with
  `root.after(0, ...)` when it arrives from another thread. pystray counts an action's
  positional parameters to decide what to pass; the decorator's wrapper counts as zero, so
  it's called with no arguments. Never `join()` a thread that calls a decorated method from
  inside a Tk callback: cross-thread `root.after()` blocks until the main thread services it,
  so that deadlocks (a test hit exactly this).
- **ddc-worker thread** does all DDC/CI I/O. Results come back through a queue that the Tk
  thread polls with `_pump()` only while work is outstanding.
- **hotkeys thread** runs `GetMessageW`; dispatches to Tk via `root.after`.
- `_generation` is bumped on every discovery; write results from an older generation are
  ignored because monitor indexes may have changed.

## 4. Conventions to keep
- Keep pure logic Tk-free and unit-testable (as `config.py`, `ddc.py`, `autostart.py` are).
- Any DDC/CI call goes through `DdcWorker` — never on the Tk thread.
- Log with `from monitorpy.logutil import log`; user-facing errors via `messagebox`.
- Comments explain *why* (Win32 quirks, threading), matching the existing style.
- Run `pytest -m "not hardware"` before committing. Commit/tag only when the owner asks.

## 5. Release process (as it actually works)
1. Bump `monitorpy/__init__.py` `__version__` (currently `1.0.7`, unreleased).
2. Commit, then `git tag vX.Y.Z && git push origin master vX.Y.Z`.
3. `release.yml` fails fast if the tag ≠ `v{__version__}`; otherwise it tests, builds and
   publishes the release with `dist/MonitorPy.exe` automatically. Don't run
   `gh release create` by hand (the README now says so too).

---

## 6. Potential improvements (prioritized)

Legend: **P1** = bug / should fix soon, **P2** = worthwhile, **P3** = nice to have.
Effort: S (< 1 h), M (a few hours), L (a day+).

### P1 — Bugs and release hygiene

1. ~~**Version string is stale (S).**~~ **Done 2026-11-19.** `__version__` → `1.0.7`; `release.yml` now fails if the tag ≠ `v{__version__}`.
2. ~~**Tests only run on tag push (S).**~~ **Done 2026-11-19.** Added `.github/workflows/ci.yml` (push/PR to `master` + manual; windows-latest, Python 3.13).
3. ~~**README release instructions contradict the workflow (S).**~~ **Done 2026-11-19.** README documents the tag-push flow.
4. ~~**Presets ignore unsupported features (S).**~~ **Done 2026-11-19.** `_set_feature` skips features the selected monitor didn't answer at discovery (no write, no log spam); the slider is greyed out and shows "n/a". `probe_monitors()` now starts `supported` as all-False, so a monitor whose DDC/CI session can't open disables both sliders.
5. ~~**Autostart path goes stale if the exe is moved (S).**~~ **Done 2026-11-19.** `autostart.repair_stale_entry()` (called from `app.main`) rewrites the Run key only when running as the frozen exe **and** the stored target no longer exists, so running from source or a second copy of the exe never takes over a working entry.

### P2 — Worthwhile features / robustness

6. **"All monitors" mode (M).** Hotkeys and presets only affect the *selected* monitor. Add
   an "Apply to all monitors" toggle (tray + window) — the most common request for this kind
   of tool. `DdcWorker` already keys writes per monitor index, so it's mostly controller work.
7. **Configurable hotkeys and step size (M).** `HOTKEYS` is hardcoded in `hotkeys.py`; step
   is a `getattr(self, 'brightness_step', 5)` never exposed. Store them in `config.json`
   (the config already preserves unknown keys, so this is forward/backward compatible), and
   add a small settings dialog.
8. **Automatic hot-plug detection (M).** Currently re-detects only on window open or via
   "Refresh monitors". Listen for `WM_DISPLAYCHANGE` / `WM_DEVICECHANGE` (hidden message
   window, or reuse the hotkey thread's loop) and debounce into `refresh_monitors()`.
   Also useful after sleep/resume, when some monitors drop DDC/CI.
9. **Values changed via the monitor's OSD aren't reflected (S–M).** Brightness/contrast are
   read only at discovery. Re-read the selected monitor's values (on the worker) when the
   window is shown.
10. **Per-monitor presets / more than two presets (M).** Presets are global and fixed to
    "Day"/"Night" (`config.py` `preset_1`/`preset_2`). Generalize to a named list, optionally
    per monitor (keyed by manufacturer+model, as `_finish_discovery` already does for
    selection). Needs a `CONFIG_VERSION` bump + migration.
11. **Scheduled presets (M).** Auto-apply Day/Night at set times or at sunrise/sunset —
    natural next step given the preset names.
12. **Hotkey feedback (S–M).** Pressing a hotkey while the window is hidden gives no visual
    feedback. Show a brief OSD-style overlay or tray tooltip with the new value.
13. **Window placement on multi-monitor setups (S–M).** `position_near_tray` uses
    `SPI_GETWORKAREA` and `winfo_screenwidth()` — primary monitor only. If the taskbar is on
    another display the popup appears in the wrong place. Use the cursor's monitor
    (`MonitorFromPoint` + `GetMonitorInfo`) or `Shell_TrayWnd` rect.

### P2 — Code quality / maintainability

14. **Split `controller.py` (M–L).** ~780 lines mixing tray, main window, preset editor and
    state. Suggested: `ui/window.py`, `ui/presets_dialog.py`, `tray.py`, leaving the
    controller as state + orchestration. Do it after CI exists (item 2) and behind the
    existing tests.
15. ~~**Dead / leftover code (S).**~~ **Done 2026-11-19.** Removed `create_tray_icon_image` (tests now cover `draw_monitor_icon`/`load_tray_icon_image`), the `last_*` getattrs and the `getattr(..._step)` pattern (now `__init__` attributes); `@on_tk_thread` replaces 7 copies of the thread-bounce code; the silent excepts in `open_edit_presets_window` now log. The remaining bare excepts are deliberate best-effort window tweaks.
16. **Dependency hygiene (S).** `requirements.txt` mixes runtime (`pystray`, `pillow`,
    `monitorcontrol`, `sv-ttk`, `darkdetect`) with build/dev (`pyinstaller`, `pytest`), all
    unpinned. Split into `requirements.txt` + `requirements-dev.txt` (or a `pyproject.toml`)
    and pin versions for reproducible release builds. `monitorcontrol` in particular has had
    API changes between versions.
17. ~~**README says Python 3.8+ (S).**~~ **Done 2026-11-19.** README now says Python 3.13 (what CI tests).
18. ~~**Import-time `sys.exit` (S).**~~ **Done 2026-11-19.** `app.main()` imports the controller inside a try and shows a "Missing Dependency" message for any missing package; importing modules has no side effects.
19. **Add linting (S).** `ruff` in CI would catch unused code like item 15.

### P3 — Packaging / polish

20. **UPX + unsigned exe → antivirus false positives (S–M).** *UPX part done 2026-11-19 (`upx=False` in the spec).* Still open: code signing (e.g. SignPath free OSS tier, Azure Trusted Signing).
21. **Windows version resource (S).** Add a `version=` file to the spec so Explorer's
    Properties → Details shows product name and version.
22. **Installer / winget (M).** Optional Inno Setup installer and a winget manifest for
    easier install/update; or at least an in-app "check for updates" via the GitHub
    Releases API.
23. ~~**Naming consistency (S).**~~ **Done 2026-11-19.** Tray icon name and tooltip are now "MonitorPy".
24. **Theming gaps (S).** The monitor list is a classic `tk.Listbox`, which sv-ttk doesn't
    style — it looks off in dark mode. Style it manually on theme change or replace with a
    `ttk.Treeview`. Also, "system" theme is only resolved at apply time; follow OS theme
    changes live (`darkdetect.listener`).
25. ~~**Accessibility / keyboard (S).**~~ **Done 2026-11-19.** `Esc` hides the control window and closes the presets window.
26. **Outside-click detection polls every 100 ms (S).** Works, but `<FocusOut>` on the root
    (with a check for the preset editor) would avoid polling. Low priority — current approach
    is robust against focus-stealing quirks, so test carefully if changed.

### New items found 2026-11-19
27. **Hotkey tests fail while MonitorPy runs (S).** The 2 tests in `tests/test_hotkeys.py`
    and `test_global_hotkeys_drive_brightness_and_contrast_while_hidden` fail with
    `WinError 1409` if another program holds `Ctrl/Alt+F10/F11`. They should `pytest.skip`
    in that case, like the `hardware` tests. The end-to-end one also sends real keystrokes
    and failed once (passed on rerun) while the desktop was in use.
28. ~~**Two MonitorPy.exe instances were running at once.**~~ **Not a bug (2026-11-19).**
    The PyInstaller one-file exe always runs as two processes: a bootloader that unpacks
    the app and a child (its `ParentProcessId` is the bootloader) that runs it. One copy of
    the app = two `MonitorPy.exe` processes.

### Suggested order (remaining)
Release 1.0.7 when ready (§5) → 27 → features 6, 8, 9, 12 → 16 → 14 (refactor) → the
"maybe" items (7, 10, 11, 13, 19, 24) as the owner prefers. 22 and 26 were judged not worth
doing for now.

---

## 7. Notes for AI agents
- Read §3 "Threading model" before editing `controller.py`; most past bugs here were
  cross-thread Tk access.
- There is no `CLAUDE.md` / `AGENTS.md` yet. If you create one, add it to `.gitignore`'s
  allow-list. Consider pointing it at this file.
- Don't run `pytest -m hardware` unless the owner confirms a DDC/CI monitor is attached.
- Don't push, tag, or create releases unless explicitly asked — a pushed `v*` tag publishes
  a public release automatically.
- When an item from §6 is done, strike it through or remove it here so this file stays
  accurate across machines.
