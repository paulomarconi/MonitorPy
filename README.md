# MonitorPy

MonitorPy is a simple system tray Python application for Windows 10/11 that allows you to control brightness and contrast of multiple monitors directly from the tray.

<figure>
    <center><img src="https://raw.githubusercontent.com/paulomarconi/MonitorPy/master/MonitorPy1.png" alt="MonitorPy1" width="80%"/></center>
</figure>

<figure>
    <center><img src="https://raw.githubusercontent.com/paulomarconi/MonitorPy/master/MonitorPy2.png" alt="MonitorPy2" width="80%"/></center>
</figure>

## Features

- Adjust brightness and contrast for connected monitors.
- System tray icon with quick access menu.
- Select between multiple monitors. Plugged or unplugged a monitor? Use **Refresh monitors** in the tray menu (it also refreshes by itself the next time you open the window).
- Edit Presets for brightness and contrast.
- Global hotkeys (work from any application, even when the window is hidden):
  - `Ctrl+F10` / `Ctrl+F11`: brightness down / up
  - `Alt+F10` / `Alt+F11`: contrast down / up
  - If another program already uses one of these keys, MonitorPy tells you at startup.
- Autostart on Windows startup option.
- Lightweight and easy to use.

## Requirements 

- Python 3.8+

The following dependencies are used only if you run `MonitorPy.py` or build the standalone executable.

- [monitorcontrol](https://pypi.org/project/monitorcontrol/)
- [pystray](https://pypi.org/project/pystray/)
- [pillow](https://pypi.org/project/Pillow/)
- [pyinstaller](https://pypi.org/project/pyinstaller/)
- [sv-ttk](https://pypi.org/project/sv-ttk/) and [darkdetect](https://pypi.org/project/darkdetect/) for the Light/Dark themes
- [pytest](https://pypi.org/project/pytest/), only needed to run the test suite (see [Development](#development))

## Usage

- Download and run the standalone `MonitorPy.exe` file from the **Releases** section. To uninstall, just delete the file.
- Download `MonitorPy.py`, install the dependencies with:

    ```sh
    pip install -r requirements.txt
    ```
    and run

    ```sh
    python MonitorPy.py
    ```

- To build the standalone executable, use `PyInstaller`:

    ```sh
    pyinstaller MonitorPy.spec
    ```

- To push a new tag version and upload the `MonitorPy.exe` to Releases:

    ```sh
    git tag v1.0.4
    git push origin v1.0.4
    gh release create v1.0.4 dist/MonitorPy.exe --title "v1.0.4" --notes "your notes"
    ```

## How it works

- The app discovers all connected monitors supporting DDC/CI.
- You can select a monitor and adjust its brightness and contrast using sliders, and presets.
- The tray icon provides quick access to show controls, presets and edit presets values, autostar and exit.
- Supports modern Light and Dark mode themes (with System Default sync) via the tray menu.
  
## Development

The code lives in the `monitorpy/` package (`MonitorPy.py` is just the entry point); `tests/` holds a pytest suite covering it.

```sh
pip install -r requirements.txt
pytest
```

Most tests use a fake monitor and never touch real hardware or your actual `%APPDATA%`. A few, marked `hardware`, exercise whatever monitors are actually connected and skip themselves cleanly if none are found or if a check doesn't apply:

```sh
pytest -m "not hardware"   # skip the ones that need real monitors
pytest -m hardware         # only those
```

## Settings and logs

Both live in `%APPDATA%\MonitorPy\`:

- `config.json`: your Day/Night presets and theme. Older versions used `presets.json`; it is migrated automatically. If the file is ever corrupt, MonitorPy starts with defaults and keeps the broken file as `config.json.bad`.
- `monitorpy.log`: diagnostic log (rotates at 256 KB). Attach it when reporting a problem.

## Troubleshooting

- If you see "No DDC/CI" next to a monitor, it means the monitor does not support DDC/CI or is not detected.
- Make sure you have the required permissions and drivers for monitor control.

## License

MIT License

## Author

paulomarconi