"""
local_mouse.py — Local mouse reader thread for /dev/input/mice.

Reads raw 3-byte ImPS/2 packets from the Linux mice multiplexer device.
Each packet contains button state and signed X/Y deltas. The Y axis is
inverted relative to HID convention (positive = up in ImPS/2, positive =
down in HID), so we negate dy before accumulating.

The thread automatically retries if the device is disconnected.
"""

import struct
import time
import threading
import logging

logger = logging.getLogger(__name__)


class LocalMouseReader(threading.Thread):
    """Daemon thread that reads /dev/input/mice and feeds InputMerger."""

    def __init__(self, merger, device: str = '/dev/input/mice', log_level: int = logging.INFO):
        super().__init__(name="LocalMouseReader", daemon=True)
        self._merger = merger
        self._device = device
        self._running = False

        logger.setLevel(log_level)

    def run(self) -> None:
        """Thread entry point — open device and read loop."""
        self._running = True
        logger.info("Local mouse reader starting on %s", self._device)

        while self._running:
            try:
                self._read_loop()
            except IOError as e:
                if self._running:
                    logger.warning("Device %s error: %s — retrying in 1s", self._device, e)
                    time.sleep(1.0)
            except Exception as e:
                if self._running:
                    logger.error("Unexpected error reading %s: %s", self._device, e)
                    time.sleep(1.0)

        logger.info("Local mouse reader stopped")

    def _read_loop(self) -> None:
        """Open the device and read 3-byte packets continuously."""
        logger.info("Opening %s", self._device)

        with open(self._device, 'rb') as f:
            logger.info("Device %s opened successfully", self._device)

            while self._running:
                data = f.read(3)
                if not data or len(data) < 3:
                    raise IOError("Short read from device (got %d bytes)" % (len(data) if data else 0))

                # ImPS/2 3-byte packet format:
                #   Byte 0: Button state — bit0=left, bit1=right, bit2=middle
                #   Byte 1: X delta (signed 8-bit)
                #   Byte 2: Y delta (signed 8-bit)
                buttons = data[0] & 0x07  # Mask to 3 buttons

                # Unpack signed deltas
                dx, dy_raw = struct.unpack('bb', data[1:3])

                # IMPORTANT: /dev/input/mice Y axis is inverted vs HID convention.
                # In ImPS/2: positive Y = cursor moves UP
                # In HID:    positive Y = cursor moves DOWN
                # So we negate.
                dy = -dy_raw

                self._merger.accumulate(dx, dy, buttons, source='local')

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._running = False
        logger.debug("Local mouse reader stop requested")
