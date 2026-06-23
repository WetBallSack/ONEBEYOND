"""
bt_hid_profile.py — BlueZ Bluetooth HID profile manager.

Handles all Bluetooth setup:
  1. Configures the HCI adapter (device class, name, discoverability).
  2. Registers a persistent D-Bus pairing agent (auto-accepts all pairs).
  3. Registers the HID SDP record via D-Bus ProfileManager1.
  4. Creates L2CAP server sockets on PSM 17 (Control) and 19 (Interrupt).
  5. Accepts incoming connections and sends HID input reports.

Requires:
  - BlueZ with the input plugin disabled (-P input) and compat mode
    (--compat) in bluetooth.service.
  - python3-dbus package.
  - Root privileges for L2CAP PSM < 4096.
"""

import os
import sys
import struct
import socket
import subprocess
import logging
import threading

logger = logging.getLogger(__name__)

# Bluetooth constants
L2CAP_PSM_CONTROL = 17    # HID Control channel
L2CAP_PSM_INTERRUPT = 19  # HID Interrupt channel

# HID transaction header: DATA | INPUT
HID_HEADER_INPUT = 0xA1

# Bluetooth protocol number for L2CAP
BTPROTO_L2CAP = 0

# HID Profile UUID
HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"

# D-Bus paths
DBUS_PROFILE_PATH = "/org/bluez/hid_bridge_profile"
DBUS_AGENT_PATH = "/org/bluez/hid_bridge_agent"

# Agent capability — no keyboard/display, auto-accept everything
AGENT_CAPABILITY = "NoInputNoOutput"


def _find_sdp_record_path() -> str:
    """Locate the sdp_record.xml file relative to this module."""
    # PyInstaller bundles data files into a temp dir
    if getattr(sys, '_MEIPASS', None):
        path = os.path.join(sys._MEIPASS, 'sdp_record.xml')
        if os.path.isfile(path):
            return path
    module_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(module_dir, "sdp_record.xml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"SDP record not found at {path}")
    return path


def _start_dbus_agent() -> bool:
    """Register a persistent D-Bus pairing agent that auto-accepts all
    pairing requests, and run the GLib main loop in a daemon thread so
    the agent stays alive for the lifetime of this process.

    Returns True if the agent was registered successfully.
    """
    try:
        import dbus
        import dbus.service
        import dbus.mainloop.glib
        from gi.repository import GLib

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

        class AutoPairAgent(dbus.service.Object):
            """BlueZ Agent1 implementation that auto-accepts everything."""

            AGENT_INTERFACE = "org.bluez.Agent1"

            @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
            def Release(self):
                logger.debug("Agent: Release called")

            @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
            def AuthorizeService(self, device, uuid):
                logger.info("Agent: AuthorizeService %s %s — auto-accepting", device, uuid)

            @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
            def RequestPinCode(self, device):
                logger.info("Agent: RequestPinCode %s — returning '0000'", device)
                return "0000"

            @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
            def RequestPasskey(self, device):
                logger.info("Agent: RequestPasskey %s — returning 0", device)
                return dbus.UInt32(0)

            @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
            def DisplayPasskey(self, device, passkey, entered):
                logger.info("Agent: DisplayPasskey %s: %06d", device, passkey)

            @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
            def DisplayPinCode(self, device, pincode):
                logger.info("Agent: DisplayPinCode %s: %s", device, pincode)

            @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
            def RequestConfirmation(self, device, passkey):
                logger.info("Agent: RequestConfirmation %s %06d — auto-confirming", device, passkey)

            @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
            def RequestAuthorization(self, device):
                logger.info("Agent: RequestAuthorization %s — auto-authorizing", device)

            @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
            def Cancel(self):
                logger.debug("Agent: Cancel called")

        bus = dbus.SystemBus()
        agent = AutoPairAgent(bus, DBUS_AGENT_PATH)

        agent_manager = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1"
        )

        # Unregister any existing agent at this path (ignore errors)
        try:
            agent_manager.UnregisterAgent(DBUS_AGENT_PATH)
        except dbus.exceptions.DBusException:
            pass

        agent_manager.RegisterAgent(DBUS_AGENT_PATH, AGENT_CAPABILITY)
        agent_manager.RequestDefaultAgent(DBUS_AGENT_PATH)
        logger.info("D-Bus pairing agent registered at %s (auto-accept)", DBUS_AGENT_PATH)

        # Run the GLib main loop in a daemon thread so the agent stays alive
        loop = GLib.MainLoop()

        def _run_loop():
            try:
                loop.run()
            except Exception as e:
                logger.error("Agent GLib main loop crashed: %s", e)

        t = threading.Thread(target=_run_loop, name="dbus-agent-loop", daemon=True)
        t.start()
        logger.debug("D-Bus agent GLib main loop started in background thread")
        return True

    except ImportError as e:
        logger.warning("Cannot start D-Bus agent (missing module: %s). "
                        "Falling back to bt-agent.", e)
        return False
    except Exception as e:
        logger.warning("D-Bus agent registration failed: %s. "
                        "Falling back to bt-agent.", e)
        return False


def _start_bt_agent_fallback() -> bool:
    """Fallback: start bt-agent as a background subprocess.

    bt-agent (from bluez-tools) is a persistent pairing agent that stays
    alive as a daemon process.
    """
    try:
        # Kill any existing bt-agent first
        subprocess.run(['killall', 'bt-agent'], capture_output=True, timeout=5)

        proc = subprocess.Popen(
            ['bt-agent', '-c', 'NoInputNoOutput'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("bt-agent started as fallback pairing agent (PID %d)", proc.pid)
        return True
    except FileNotFoundError:
        logger.error("bt-agent not found. Install bluez-tools: sudo apt install bluez-tools")
        return False
    except Exception as e:
        logger.error("Failed to start bt-agent: %s", e)
        return False


class BluetoothHIDProfile:
    """Manages the Bluetooth HID mouse profile lifecycle."""

    def __init__(self, device_name: str = "HID-Bridge Mouse", log_level: int = logging.INFO):
        self._device_name = device_name
        self._ctrl_sock = None       # L2CAP control channel server socket
        self._intr_sock = None       # L2CAP interrupt channel server socket
        self._ctrl_client = None     # Connected control channel
        self._intr_client = None     # Connected interrupt channel
        self._client_addr = None     # Connected device address
        self._connected = False
        self._lock = threading.Lock()

        logger.setLevel(log_level)

    def setup(self) -> None:
        """Configure the Bluetooth adapter and register the HID profile.

        This must be called once before wait_for_connection(). Requires
        root privileges.
        """
        logger.info("Setting up Bluetooth HID profile...")

        # 1. Set device class: 0x002580
        #    Bits: Major Service Class = 0x00 (None)
        #    Major Device Class = 0x05 (Peripheral)
        #    Minor Device Class = 0x80 (Pointing device)
        self._run_cmd(['hciconfig', 'hci0', 'class', '0x002580'],
                      "Set device class to 0x002580 (peripheral/pointing)")

        # 2. Set device name
        self._run_cmd(['hciconfig', 'hci0', 'name', self._device_name],
                      f"Set device name to '{self._device_name}'")

        # 3. Make discoverable and pairable via bluetoothctl
        self._run_cmd(['bluetoothctl', 'power', 'on'], "Power on Bluetooth")
        self._run_cmd(['bluetoothctl', 'discoverable', 'on'], "Enable discoverable")
        self._run_cmd(['bluetoothctl', 'pairable', 'on'], "Enable pairable")

        # 4. Start a persistent pairing agent (D-Bus preferred, bt-agent fallback)
        if not _start_dbus_agent():
            _start_bt_agent_fallback()

        # 5. Register SDP record via D-Bus
        self._register_sdp_record()

        logger.info("Bluetooth HID profile setup complete")

    def _run_cmd(self, cmd: list, description: str) -> bool:
        """Run a subprocess command with logging."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.debug("%s: OK", description)
            else:
                logger.warning("%s: exit %d — %s",
                               description, result.returncode,
                               result.stderr.strip() or result.stdout.strip())
            return result.returncode == 0
        except FileNotFoundError:
            logger.error("%s: command not found: %s", description, cmd[0])
            return False
        except subprocess.TimeoutExpired:
            logger.error("%s: timed out", description)
            return False

    def _register_sdp_record(self) -> None:
        """Register the HID SDP record via D-Bus ProfileManager1."""
        try:
            import dbus

            bus = dbus.SystemBus()
            manager = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.ProfileManager1"
            )

            # Read the SDP record XML
            sdp_path = _find_sdp_record_path()
            with open(sdp_path, 'r') as f:
                sdp_xml = f.read()

            opts = {
                "ServiceRecord": sdp_xml,
                "Role": "server",
                "RequireAuthentication": dbus.Boolean(False),
                "RequireAuthorization": dbus.Boolean(False),
            }

            manager.RegisterProfile(DBUS_PROFILE_PATH, HID_UUID, opts)
            logger.info("SDP record registered via D-Bus ProfileManager1")

        except ImportError:
            logger.warning(
                "python3-dbus not available; falling back to sdptool. "
                "Install python3-dbus for proper SDP registration."
            )
            self._register_sdp_via_sdptool()

        except Exception as e:
            logger.warning("D-Bus SDP registration failed: %s — trying sdptool", e)
            self._register_sdp_via_sdptool()

    def _register_sdp_via_sdptool(self) -> None:
        """Fallback: register the SDP record using sdptool.

        Requires --compat flag in bluetooth.service ExecStart line.
        """
        result = subprocess.run(
            ['sdptool', 'add', '--handle=0x10000', 'HID'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("SDP HID record registered via sdptool (generic)")
        else:
            logger.warning(
                "sdptool HID registration failed (exit %d: %s). "
                "SDP record may not be registered — the host device may "
                "not recognize this as a HID mouse. "
                "Install python3-dbus for reliable SDP registration.",
                result.returncode,
                result.stderr.strip() or result.stdout.strip()
            )

    def wait_for_connection(self) -> bool:
        """Create L2CAP server sockets and wait for an HID host to connect.

        Blocks until a device connects on both the control and interrupt
        channels. Returns True on success, False on error.
        """
        logger.info("Waiting for Bluetooth HID connection...")

        # Clean up any previous sockets
        self._close_server_sockets()
        self._close_client_sockets()

        try:
            # Create control channel server (PSM 17)
            self._ctrl_sock = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP
            )
            self._ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._ctrl_sock.bind(('00:00:00:00:00:00', L2CAP_PSM_CONTROL))
            self._ctrl_sock.listen(1)
            logger.debug("L2CAP control socket listening on PSM %d", L2CAP_PSM_CONTROL)

            # Create interrupt channel server (PSM 19)
            self._intr_sock = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP
            )
            self._intr_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._intr_sock.bind(('00:00:00:00:00:00', L2CAP_PSM_INTERRUPT))
            self._intr_sock.listen(1)
            logger.debug("L2CAP interrupt socket listening on PSM %d", L2CAP_PSM_INTERRUPT)

        except OSError as e:
            logger.error("Failed to create L2CAP server sockets: %s", e)
            logger.error(
                "Ensure BlueZ input plugin is disabled (-P input) and "
                "you are running as root."
            )
            self._close_server_sockets()
            return False

        try:
            # Accept control channel connection
            logger.info("Waiting for control channel connection (PSM %d)...", L2CAP_PSM_CONTROL)
            self._ctrl_client, ctrl_addr = self._ctrl_sock.accept()
            logger.info("Control channel connected from %s", ctrl_addr[0])

            # Accept interrupt channel connection
            logger.info("Waiting for interrupt channel connection (PSM %d)...", L2CAP_PSM_INTERRUPT)
            self._intr_client, intr_addr = self._intr_sock.accept()
            logger.info("Interrupt channel connected from %s", intr_addr[0])

            self._client_addr = ctrl_addr[0]
            self._connected = True

            logger.info("HID device connected: %s", self._client_addr)
            return True

        except OSError as e:
            logger.error("Error accepting L2CAP connection: %s", e)
            self._close_client_sockets()
            return False

    def send_report(self, buttons: int, dx: int, dy: int, wheel: int = 0) -> bool:
        """Send an HID input report over the interrupt channel.

        Report format (5 bytes):
          0xA1 (DATA|INPUT header)
          buttons (uint8, bits 0-2: left/right/middle)
          dx (int8, relative X)
          dy (int8, relative Y)
          wheel (int8, relative wheel)

        Args:
            buttons: Button bitmask.
            dx: Relative X movement (-127..127).
            dy: Relative Y movement (-127..127).
            wheel: Relative wheel movement (-127..127).

        Returns:
            True if the report was sent successfully, False on error.
        """
        if not self._connected or self._intr_client is None:
            return False

        try:
            # Pack: header(u8), buttons(u8), dx(i8), dy(i8), wheel(i8)
            report = struct.pack('BBbbb', HID_HEADER_INPUT, buttons & 0x07, dx, dy, wheel)
            self._intr_client.send(report)
            return True
        except OSError as e:
            logger.warning("Failed to send HID report: %s", e)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Close all connections and sockets."""
        logger.info("Disconnecting Bluetooth HID profile")
        self._connected = False
        self._close_client_sockets()
        self._close_server_sockets()

    def is_connected(self) -> bool:
        """Check whether the interrupt channel is alive."""
        return self._connected and self._intr_client is not None

    def _close_client_sockets(self) -> None:
        """Close connected client sockets."""
        for name, sock in [('control', self._ctrl_client), ('interrupt', self._intr_client)]:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._ctrl_client = None
        self._intr_client = None
        self._client_addr = None
        self._connected = False

    def _close_server_sockets(self) -> None:
        """Close listening server sockets."""
        for name, sock in [('control', self._ctrl_sock), ('interrupt', self._intr_sock)]:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._ctrl_sock = None
        self._intr_sock = None
