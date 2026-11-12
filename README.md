# Burst Control for Linux

Native Linux utility (CLI + GTK4/Libadwaita GUI) for configuring the **Turtle Beach Burst II Air** wireless gaming mouse over USB HID (`10f5:5002` wired and `10f5:5004` 2.4 GHz receiver).

Ported from Artyom Kulimov's macOS [Burst Control](https://github.com/artyomkulimov/burst-control) project.

## Features
- **5 Onboard DPI Stages:** 50–26,000 DPI in 50 DPI increments with live active-stage tracking
- **Response Settings:** Click debounce (`0–10 ms`) and USB polling rate (`125 / 250 / 500 / 1000 Hz`)
- **Tracking Settings:** Lift-off distance (`Very low` / `Low`), Angle Snapping, and Motion Sync
- **Safety & Backups:** Byte-level validation preserving unmapped sensor bytes, read-back verification, and automatic JSON backups in `~/.local/share/burst-control/`

## Installation

### 1. System Dependencies (GTK4 + Libadwaita)
- **Arch Linux:** `sudo pacman -S python-gobject gtk4 libadwaita`
- **Ubuntu / Debian:** `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-venv`
- **Fedora:** `sudo dnf install python3-gobject gtk4 libadwaita`

### 2. Install `udev` Rules, CLI, and Desktop Launcher
```bash
./install-linux.sh
```
Unplug and replug your Burst II Air cable or 2.4 GHz dongle after installing the `udev` rule.

## Usage
- **Launch GUI:** `burstcontrol --gui` (or open **Burst Control** from your app launcher)
- **Read current settings:** `burstcontrol`
- **Modify settings via CLI:** `burstcontrol --dpi 2:1600 --polling 1000 --debounce 2 --motion-sync on`
- **Offline simulation (no hardware required):** `burstcontrol --mock` or `burstcontrol --mock --gui`
