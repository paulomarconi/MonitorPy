# MonitorPy

MonitorPy is a simple system tray Python application for Windows 10/11 that allows you to control your monitor's brightness and contrast directly from the tray.

<figure>
    <center><img src="https://raw.githubusercontent.com/paulomarconi/MonitorPy/master/MonitorPy1.png" alt="MonitorPy1" width="50%"/></center>
</figure>



## Features

- Adjust brightness and contrast for connected monitors.
- System tray icon with quick access menu.
- Select between multiple monitors.
- Reset brightness and contrast to default values.
- Lightweight and easy to use.

## Requirements

- Python 3.8+
- [monitorcontrol](https://pypi.org/project/monitorcontrol/)
- [pystray](https://pypi.org/project/pystray/)
- [pillow](https://pypi.org/project/Pillow/)
- [pyinstaller](https://pypi.org/project/pyinstaller/)

## Usage

- Download and run the standalone `.exe` file from the Releases section. To uninstall, just delete the file.
- Download `MonitorPy.py`, install the dependencies with:

    ```sh
    pip install -r requirements.txt
    ```
    and run

    ```sh
    python MonitorPy.py
    ```

- To build a standalone executable, use PyInstaller:

```sh
pyinstaller --onefile --windowed --name "MonitorPy_v1.0.0" MonitorPy.py
```

## How it works

- The app discovers all connected monitors supporting DDC/CI.
- You can select a monitor and adjust its brightness and contrast using sliders.
- The tray icon provides quick access to show controls, reset values, and exit.
  
## Troubleshooting

- If you see "No DDC/CI" next to a monitor, it means the monitor does not support DDC/CI or is not detected.
- Make sure you have the required permissions and drivers for monitor control.

## License

MIT License

## Author

paulomarconi