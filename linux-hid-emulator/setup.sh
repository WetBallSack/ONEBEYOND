#!/bin/bash
# setup.sh — Bare-metal deployment for HID Bridge Linux emulator.
# Must be run as root or with sudo.

set -euo pipefail

echo "=== HID Bridge - Linux Setup ==="
echo ""

# Verify running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (or with sudo)."
    exit 1
fi

# Install dependencies
echo "[1/4] Installing system dependencies..."
apt-get update -qq
apt-get install -y python3 python3-dbus python3-gi python3-venv python3-dev \
    libdbus-1-dev libglib2.0-dev bluez bluez-tools
echo "      Done."

# Add current user to input group (for /dev/input/mice access)
echo "[2/4] Configuring user permissions..."
TARGET_USER="${SUDO_USER:-$USER}"
if id -nG "$TARGET_USER" | grep -qw input; then
    echo "      User '$TARGET_USER' is already in the input group."
else
    usermod -aG input "$TARGET_USER"
    echo "      Added '$TARGET_USER' to input group."
fi

# Configure BlueZ:
#   -P input : Release HID L2CAP PSMs so our emulator can bind to them.
#   --compat  : Enable legacy SDP server so sdptool can register records.
# Both flags are required for this emulator to work.
echo "[3/5] Configuring BlueZ service flags..."
BT_SERVICE="/lib/systemd/system/bluetooth.service"

if [ ! -f "$BT_SERVICE" ]; then
    # Try alternate location
    BT_SERVICE="/usr/lib/systemd/system/bluetooth.service"
fi

if [ ! -f "$BT_SERVICE" ]; then
    echo "      WARNING: Could not find bluetooth.service file."
    echo "      Manually add '-P input --compat' to the ExecStart line in your bluetooth.service."
else
    # Idempotent: check each flag independently, add only what is missing
    NEEDS_UPDATE=false

    if ! grep -q -- '-P input' "$BT_SERVICE"; then
        sed -i 's|^ExecStart=.*bluetoothd.*|& -P input|' "$BT_SERVICE"
        echo "      Added '-P input' (disables BlueZ input plugin)."
        NEEDS_UPDATE=true
    else
        echo "      '-P input' already present."
    fi

    if ! grep -q -- '--compat' "$BT_SERVICE"; then
        sed -i 's|^ExecStart=.*bluetoothd.*|& --compat|' "$BT_SERVICE"
        echo "      Added '--compat' (enables legacy SDP server for sdptool)."
        NEEDS_UPDATE=true
    else
        echo "      '--compat' already present."
    fi

    if [ "$NEEDS_UPDATE" = true ]; then
        echo "      BlueZ configuration updated."
    fi
fi

# Write /etc/bluetooth/main.conf to set device class and name.
# hciconfig is deprecated on modern Ubuntu — this is the correct method.
# Class 0x002580:
#   Major Service = 0x000 (none)
#   Major Device  = 0x05  (Peripheral)
#   Minor Device  = 0x80  (Pointing device / Mouse)
echo "[4/5] Writing /etc/bluetooth/main.conf (device class + name)..."
BT_CONF="/etc/bluetooth/main.conf"

# Back up the existing config if it hasn't been backed up yet
if [ -f "$BT_CONF" ] && [ ! -f "${BT_CONF}.bak" ]; then
    cp "$BT_CONF" "${BT_CONF}.bak"
    echo "      Backed up original config to ${BT_CONF}.bak"
fi

# Write a clean minimal config
cat > "$BT_CONF" << 'EOF'
[General]
# HID Bridge Mouse emulator - set by setup.sh
Name = HID-Bridge Mouse
Class = 0x002580
DiscoverableTimeout = 0
AlwaysPairable = true
EOF
echo "      Written: Name=HID-Bridge Mouse, Class=0x002580 (Peripheral/Mouse)"

# Reload and restart Bluetooth
echo "[5/5] Restarting Bluetooth service..."
systemctl daemon-reload
systemctl restart bluetooth
echo "      Bluetooth service restarted."

echo ""
echo "=== Setup complete ==="
echo ""
echo "NOTES:"
echo "  - You must log out and back in for the input group change to take effect."
echo "    Or run: newgrp input"
echo "  - To verify BlueZ config: systemctl cat bluetooth | grep ExecStart"
echo "  - To start the emulator:  sudo ./run.sh"
