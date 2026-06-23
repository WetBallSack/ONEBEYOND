#!/bin/bash
set -euo pipefail
echo '=== Building HID Bridge standalone binary ==='
# Create a temporary virtual environment to run PyInstaller
VENV_DIR="/tmp/hid_bridge_build_venv"
echo "Creating build virtual environment in ${VENV_DIR}..."
# Use --system-site-packages so PyInstaller can find python3-dbus (a system package)
python3 -m venv --system-site-packages "$VENV_DIR"

echo "Installing PyInstaller and dbus-python in virtual environment..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet pyinstaller
# Install dbus-python from PyPI (compiles from source using libdbus-1-dev)
"$VENV_DIR/bin/pip" install --quiet dbus-python

echo "Running PyInstaller..."
"$VENV_DIR/bin/pyinstaller" --onefile \
  --add-data 'sdp_record.xml:.' \
  --add-data 'config.ini:.' \
  --collect-all dbus \
  --hidden-import dbus.mainloop.glib \
  --hidden-import dbus.service \
  --hidden-import gi \
  --hidden-import gi.repository.GLib \
  --name hid_emulator \
  hid_emulator.py

echo "Cleaning up build virtual environment..."
rm -rf "$VENV_DIR"

echo ''
echo 'Binary created: dist/hid_emulator'
echo 'Usage: sudo ./dist/hid_emulator --help'
