#!/bin/bash
# run.sh — Launch the HID Bridge mouse emulator.
# Must be run as root (L2CAP PSM < 4096 requires root).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== HID Bridge - Starting ==="
echo ""

# Verify running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (or with sudo)."
    echo "       L2CAP sockets on PSM 17/19 require root privileges."
    exit 1
fi

# Ensure Bluetooth is powered on and discoverable
echo "[1/2] Configuring Bluetooth adapter..."
bluetoothctl power on       2>/dev/null || echo "      Warning: 'power on' failed"
bluetoothctl discoverable on 2>/dev/null || echo "      Warning: 'discoverable on' failed"
bluetoothctl pairable on    2>/dev/null || echo "      Warning: 'pairable on' failed"
echo "      Bluetooth is ready."

# Start the emulator
echo '[2/2] Launching HID emulator...'
echo ''
if [ -x "$SCRIPT_DIR/dist/hid_emulator" ]; then
    exec "$SCRIPT_DIR/dist/hid_emulator" "$@"
elif [ -x "$SCRIPT_DIR/hid_emulator" ]; then
    exec "$SCRIPT_DIR/hid_emulator" "$@"
else
    exec python3 "$SCRIPT_DIR/hid_emulator.py" "$@"
fi
