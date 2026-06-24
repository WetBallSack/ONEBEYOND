#!/bin/bash
set -euo pipefail
echo '=== Building HID Bridge standalone binary ==='

VENV_DIR="/tmp/hid_bridge_build_venv"
echo "Creating build virtual environment in ${VENV_DIR}..."
python3 -m venv --system-site-packages "$VENV_DIR"

echo "Installing PyInstaller and dependencies in virtual environment..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet pyinstaller
"$VENV_DIR/bin/pip" install --quiet dbus-python
"$VENV_DIR/bin/pip" install --quiet evdev

echo "Running PyInstaller..."
"$VENV_DIR/bin/pyinstaller" --onefile \
  --add-data 'sdp_record.xml:.' \
  --add-data 'config.ini:.' \
  --collect-all dbus \
  --hidden-import dbus.mainloop.glib \
  --hidden-import dbus.service \
  --hidden-import gi \
  --hidden-import gi.repository.GLib \
  --hidden-import evdev \
  --hidden-import evdev.ecodes \
  --name hid_emulator \
  hid_emulator.py

echo "Cleaning up build virtual environment..."
rm -rf "$VENV_DIR"

echo ''
echo 'Binary created: dist/hid_emulator'
echo 'Usage: sudo ./dist/hid_emulator --help'
