# HID Bridge — Network-to-Bluetooth Mouse Input Bridge

A two-PC assistive technology system that captures software-generated mouse inputs on a Windows PC, transmits them over UDP to a Linux PC, merges them with local physical mouse inputs (Co-Pilot Mode), and emits a single unified Bluetooth HID mouse signal.

```
┌─────────────────────────┐     UDP      ┌─────────────────────────┐
│     PRIMARY PC          │   (7-byte    │      LINUX PC           │
│     (Windows)           │    packets)  │   (Live USB / BT Hub)   │
│                         │ ──────────►  │                         │
│  Assistive App          │              │  UDP Listener           │
│       │                 │              │       │                 │
│       ▼                 │              │       ▼                 │
│  LL Mouse Hook          │              │  Input Merger  ◄── /dev/input/mice
│  (captures injected,    │              │  (sum deltas,     (local USB mouse)
│   passes physical)      │              │   OR buttons)           │
│       │                 │              │       │                 │
│       ▼                 │              │       ▼                 │
│  UDP Transmitter ───────│──────────────│► BT HID Profile         │
│                         │              │  (L2CAP PSM 17/19)      │
│                         │  Bluetooth   │       │                 │
│  BT Mouse ◄────────────│──────────────│───────┘                 │
│  (received as HID)      │   (HID)     │                         │
└─────────────────────────┘              └─────────────────────────┘
```

## Project Structure

```
hid-bridge/
├── README.md
├── primary-pc-forwarder/          # Windows C++17 application
│   ├── CMakeLists.txt             # CMake 3.15+ build
│   ├── config.ini                 # Target IP, port, logging
│   └── src/
│       ├── protocol.h             # 7-byte wire packet format
│       ├── config.h / config.cpp  # INI reader
│       ├── network.h / network.cpp # Winsock2 UDP sender
│       ├── mouse_hook.h / mouse_hook.cpp  # WH_MOUSE_LL hook
│       └── main.cpp               # Entry point
│
└── linux-hid-emulator/            # Python 3.8+ service
    ├── setup.sh                   # System setup (apt, groups, BlueZ)
    ├── run.sh                     # Launcher (BT power on + emulator)
    ├── config.ini                 # UDP port, BT name, dispatch rate
    ├── protocol.py                # Packet parser
    ├── udp_listener.py            # UDP receiver thread
    ├── local_mouse.py             # /dev/input/mice reader thread
    ├── input_merger.py            # Thread-safe delta accumulator
    ├── bt_hid_profile.py          # BlueZ L2CAP + SDP profile
    ├── sdp_record.xml             # Bluetooth HID mouse SDP record
    └── hid_emulator.py            # Main entry + dispatch loop
```

---

## Part 1: Primary PC Forwarder (Windows)

### Prerequisites

- Windows 10 or 11 (x64)
- Visual Studio 2019+ with C++ Desktop workload, **or** MinGW-w64 with CMake
- CMake 3.15+

### Building

```powershell
cd primary-pc-forwarder
cmake -B build -G "Visual Studio 17 2022"
cmake --build build --config Release
```

Or with MinGW:

```bash
cd primary-pc-forwarder
cmake -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

The binary `hid_forwarder.exe` and `config.ini` are placed in the build output directory.

### Configuration

Edit `config.ini` in the same directory as `hid_forwarder.exe`:

| Key                  | Default         | Description |
|----------------------|-----------------|-------------|
| `target_ip`          | `192.168.1.100` | IP address of the Linux PC |
| `target_port`        | `5555`          | UDP port the Linux PC listens on |
| `log_level`          | `2`             | 0=silent, 1=errors, 2=info, 3=debug |
| `use_relative_deltas`| `true`          | `true` if assistive app uses `SendInput` relative mode |

### Running

```powershell
.\hid_forwarder.exe
```

Press `Ctrl+C` to stop gracefully. The forwarder will print session statistics on exit.

### How It Works

1. A `WH_MOUSE_LL` hook captures all mouse events system-wide.
2. Each event is inspected for `LLMHF_INJECTED` / `LLMHF_LOWER_IL_INJECTED` flags.
3. **Injected events** (from assistive software): relative deltas and button states are extracted, packed into a 7-byte UDP packet, and transmitted. The event is **suppressed** (return 1) to prevent duplicate local cursor movement.
4. **Physical/Bluetooth events**: passed through via `CallNextHookEx` with zero processing overhead, preventing feedback loops.

### Wire Protocol

7-byte little-endian binary packet:

| Offset | Size | Field   | Type    | Description |
|--------|------|---------|---------|-------------|
| 0      | 1    | magic   | uint8   | `0xAB` — validation sentinel |
| 1      | 2    | dx      | int16   | Signed relative X delta |
| 3      | 2    | dy      | int16   | Signed relative Y delta |
| 5      | 1    | buttons | uint8   | Bit 0: Left, Bit 1: Right, Bit 2: Middle |
| 6      | 1    | seq     | uint8   | Rolling sequence (0–255) |

---

## Part 2: Linux HID Emulator

### Prerequisites

- Debian/Ubuntu-based Linux (Live USB or installed)
- Python 3.8+
- Bluetooth adapter (USB dongle or built-in)
- Root access

### Quick Start

```bash
# 1. Run the setup script (installs deps, configures BlueZ)
sudo bash setup.sh

# 2. Log out/in (or: newgrp input) for group permissions

# 3. Launch the emulator
sudo bash run.sh
```

### What `setup.sh` Does

1. Installs packages: `python3`, `python3-dbus`, `bluez`, `bluez-tools`
2. Adds your user to the `input` group (for `/dev/input/mice` access)
3. Adds `-P input` flag to the BlueZ systemd service (disables the default input plugin so our emulator can bind to HID L2CAP ports)
4. Restarts the Bluetooth service

### Configuration

Edit `config.ini`:

| Section       | Key               | Default             | Description |
|---------------|-------------------|---------------------|-------------|
| `[network]`   | `udp_port`        | `5555`              | UDP port to listen on |
| `[bluetooth]` | `device_name`     | `HID-Bridge Mouse`  | Name visible to paired devices |
| `[performance]`| `dispatch_rate_hz`| `125`               | HID report rate (125/250/500/1000 Hz) |
| `[logging]`   | `log_level`       | `2`                 | 0=silent, 1=errors, 2=info, 3=debug |

### Pairing

1. Start the emulator (`sudo bash run.sh`). It will make the adapter discoverable.
2. On the **Windows PC**, open **Settings → Bluetooth & Devices → Add device**.
3. Select **"HID-Bridge Mouse"** from the list.
4. Pair. Windows will recognize it as a standard Bluetooth mouse.
5. After initial pairing, reconnections are automatic.

### How It Works

1. **UDP Listener Thread**: Receives 7-byte packets from the Windows forwarder, parses dx/dy/buttons, feeds them into the Input Merger.

2. **Local Mouse Reader Thread**: Reads 3-byte ImPS/2 packets from `/dev/input/mice` (aggregates all physical USB mice), parses dx/dy/buttons (with Y-axis inversion for HID convention), feeds into the Input Merger.

3. **Input Merger**: Thread-safe accumulator that:
   - **Sums** relative dx/dy deltas from both sources
   - **ORs** button state bitmasks
   - On `flush()`: clamps to [-127, 127] and preserves remainder for next tick

4. **Dispatch Loop** (125 Hz default): Calls `flush()` every 8ms, formats a 4-byte HID input report, sends it over the Bluetooth L2CAP Interrupt channel (PSM 19).

5. **Bluetooth HID Profile**: Registers an SDP record describing a 3-button relative mouse with wheel. Accepts L2CAP connections on PSM 17 (Control) and PSM 19 (Interrupt).

---

## Network Setup

Both PCs must be on the same network. Recommended configurations:

### Option A: Direct Ethernet Cable
- Connect both PCs with an Ethernet cable.
- Set static IPs (e.g., Primary: `192.168.1.1`, Linux: `192.168.1.100`).
- Lowest latency option.

### Option B: Same Wi-Fi / LAN
- Both PCs on the same router.
- Set `target_ip` in the Windows config to the Linux PC's LAN IP.
- Use `ip addr show` on Linux to find the IP.

### Firewall
- **Linux**: Ensure UDP port 5555 (or your configured port) is open:
  ```bash
  sudo ufw allow 5555/udp
  ```
- **Windows**: No inbound rules needed (only sends).

---

## Latency Optimization

| Layer | Optimization |
|-------|-------------|
| Windows Hook | Zero allocations in callback, direct `sendto()` from hook thread |
| UDP Socket (Win) | `FIONBIO` non-blocking mode, `SO_SNDBUF = 1024` bytes |
| UDP Socket (Linux) | `SO_RCVBUF = 2048` bytes, 50ms socket timeout |
| Wire Protocol | 7 bytes per packet — fits in a single UDP datagram |
| Input Merger | `threading.Lock` (lower overhead than Queue), remainder preservation |
| Dispatch Loop | Precise tick timing via `time.monotonic()`, only sends on actual input |
| Bluetooth | Direct L2CAP `SOCK_SEQPACKET` sends, no D-Bus hop in hot path |

---

## Troubleshooting

### Windows Forwarder

| Problem | Solution |
|---------|----------|
| "WSAStartup failed" | Winsock not available — restart or check Windows Sockets service |
| No packets sent | Verify `target_ip` / `target_port` in config.ini. Test with `netcat -u -l 5555` on Linux |
| Cursor still moves locally | Ensure the assistive app injects with `SendInput` (sets LLMHF_INJECTED). Raw hardware input is never suppressed |
| Log level 3 shows no injected events | Your assistive app may not set the injected flag. Check with a simple `SendInput(MOUSEEVENTF_MOVE)` test |

### Linux Emulator

| Problem | Solution |
|---------|----------|
| "Permission denied" for /dev/input/mice | Run `sudo usermod -aG input $USER` then log out/in |
| L2CAP bind fails (EADDRINUSE) | BlueZ input plugin still active. Verify: `systemctl cat bluetooth \| grep ExecStart` should show `-P input` |
| "Failed to register SDP record" | Ensure `python3-dbus` is installed. Try: `sdptool browse local` |
| Windows doesn't see the mouse | Check `bluetoothctl show` — adapter must be discoverable. Re-run `sudo bash run.sh` |
| Laggy cursor | Increase `dispatch_rate_hz` to 250 or 500. Check network latency with `ping` |

---

## License

This project is provided as-is for assistive technology and research purposes.
