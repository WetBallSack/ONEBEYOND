#!/usr/bin/env python3
"""
hid_emulator.py — Main entry point for the Linux HID Bridge mouse emulator.

This service:
  1. Reads configuration from config.ini (CLI args override).
  2. Starts a UDP listener thread for remote mouse packets.
  3. Starts a local /dev/input/mice reader thread.
  4. Sets up the Bluetooth HID profile (adapter config + SDP record).
  5. Waits for a Bluetooth host to connect.
  6. Runs a tight dispatch loop that flushes merged mouse deltas and
     sends HID input reports at a configurable rate (default 125 Hz).
  7. Automatically reconnects when the Bluetooth host disconnects.

Must be run as root on Linux with BlueZ input plugin disabled.
"""

import signal
import sys
import os
import time
import argparse
import configparser
import logging

from input_merger import InputMerger
from udp_listener import UDPListener
from local_mouse import LocalMouseReader
from bt_hid_profile import BluetoothHIDProfile

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER = r"""
 ╔══════════════════════════════════════════════╗
 ║         HID Bridge — Mouse Emulator          ║
 ║        Bluetooth HID Profile Service         ║
 ╚══════════════════════════════════════════════╝
"""

# ---------------------------------------------------------------------------
# Globals for signal handling
# ---------------------------------------------------------------------------

_running = True
_udp_listener = None
_local_reader = None
_bt_profile = None


def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global _running
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    logging.getLogger(__name__).info("Received %s — shutting down...", sig_name)
    _running = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_base_dir():
    """Return the base directory for config and data files.

    When running from a PyInstaller bundle, sys._MEIPASS points to the
    temporary extraction directory where bundled data files live.
    """
    if getattr(sys, '_MEIPASS', None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description='HID Bridge Mouse Emulator')
    p.add_argument('--udp-port', type=int, default=None,
                   help='UDP port to listen on (overrides config.ini)')
    p.add_argument('--device-name', type=str, default=None,
                   help='Bluetooth device name (overrides config.ini)')
    p.add_argument('--dispatch-rate', type=int, default=None,
                   help='Dispatch rate in Hz (overrides config.ini)')
    p.add_argument('--log-level', type=int, default=None, choices=[0, 1, 2, 3],
                   help='0=silent, 1=errors, 2=info, 3=debug')
    return p.parse_args()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config() -> configparser.ConfigParser:
    """Load config.ini from the base directory."""
    config = configparser.ConfigParser()
    config_path = os.path.join(_get_base_dir(), 'config.ini')

    if not os.path.isfile(config_path):
        logging.getLogger(__name__).warning(
            "Config file not found at %s — using defaults", config_path
        )
        # Set defaults
        config['network'] = {'udp_port': '5555'}
        config['bluetooth'] = {'device_name': 'HID-Bridge Mouse'}
        config['performance'] = {'dispatch_rate_hz': '125'}
        config['logging'] = {'log_level': '2'}
    else:
        config.read(config_path)

    return config


def get_log_level(level_int: int) -> int:
    """Map config log_level (0-3) to Python logging level."""
    mapping = {
        0: logging.CRITICAL,  # silent
        1: logging.ERROR,     # errors only
        2: logging.INFO,      # info
        3: logging.DEBUG,     # debug
    }
    return mapping.get(level_int, logging.INFO)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _running, _udp_listener, _local_reader, _bt_profile

    # Parse CLI args
    args = parse_args()

    # Load config
    config = load_config()

    # Resolve values: CLI overrides config.ini
    udp_port = args.udp_port if args.udp_port is not None else config.getint('network', 'udp_port', fallback=5555)
    device_name = args.device_name if args.device_name is not None else config.get('bluetooth', 'device_name', fallback='HID-Bridge Mouse')
    dispatch_rate_hz = args.dispatch_rate if args.dispatch_rate is not None else config.getint('performance', 'dispatch_rate_hz', fallback=125)
    log_level_int = args.log_level if args.log_level is not None else config.getint('logging', 'log_level', fallback=2)

    log_level = get_log_level(log_level_int)

    # Configure logging
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)-5s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    logger = logging.getLogger(__name__)

    # Print banner
    print(BANNER)
    logger.info("Configuration:")
    logger.info("  UDP port:       %d", udp_port)
    logger.info("  Device name:    %s", device_name)
    logger.info("  Dispatch rate:  %d Hz (%.1f ms)", dispatch_rate_hz, 1000.0 / dispatch_rate_hz)
    logger.info("  Log level:      %d (%s)", log_level_int, logging.getLevelName(log_level))

    # Install signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Tick interval
    tick_interval = 1.0 / dispatch_rate_hz

    # Create shared merger
    merger = InputMerger()

    # Start UDP listener thread
    _udp_listener = UDPListener(port=udp_port, merger=merger, log_level=log_level)
    _udp_listener.start()
    logger.info("UDP listener thread started")

    # Start local mouse reader thread
    _local_reader = LocalMouseReader(merger=merger, log_level=log_level)
    _local_reader.start()
    logger.info("Local mouse reader thread started")

    # Set up Bluetooth HID profile
    _bt_profile = BluetoothHIDProfile(device_name=device_name, log_level=log_level)
    _bt_profile.setup()

    # Outer reconnection loop
    while _running:
        logger.info("=" * 50)
        logger.info("Waiting for Bluetooth HID host to connect...")

        if not _bt_profile.wait_for_connection():
            if _running:
                logger.error("Failed to establish connection — retrying in 2s")
                time.sleep(2.0)
            continue

        logger.info("Bluetooth HID host connected — entering dispatch loop")

        # Track last button state to avoid redundant reports
        last_buttons = 0

        # Inner dispatch loop
        while _running and _bt_profile.is_connected():
            tick_start = time.monotonic()

            # Flush accumulated input
            dx, dy, buttons = merger.flush()

            # Send report if there's actual input (motion or button change)
            if dx != 0 or dy != 0 or buttons != last_buttons:
                if not _bt_profile.send_report(buttons, dx, dy):
                    logger.warning("Send report failed — connection lost")
                    break
                last_buttons = buttons

            # Precise sleep for remainder of tick
            elapsed = time.monotonic() - tick_start
            sleep_time = tick_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Connection lost or shutdown requested
        if _running:
            logger.info("Bluetooth HID host disconnected")
            _bt_profile.disconnect()
            last_buttons = 0

    # Cleanup
    _shutdown()


def _shutdown():
    """Clean shutdown of all components."""
    logger = logging.getLogger(__name__)
    logger.info("Shutting down...")

    if _udp_listener is not None:
        _udp_listener.stop()
    if _local_reader is not None:
        _local_reader.stop()
    if _bt_profile is not None:
        _bt_profile.disconnect()

    logger.info("HID Bridge stopped. Goodbye.")


if __name__ == '__main__':
    main()
