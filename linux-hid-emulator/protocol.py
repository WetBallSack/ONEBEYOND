"""
protocol.py — Wire protocol for HID mouse packets.

Mirrors the C++ protocol.h definitions. Packet format (7 bytes, little-endian):
  magic:uint8 | dx:int16 | dy:int16 | buttons:uint8 | seq:uint8

The magic byte (0xAB) is used for frame synchronization over UDP.
"""

import struct
import logging

logger = logging.getLogger(__name__)

# Protocol constants
PACKET_MAGIC: int = 0xAB
PACKET_FORMAT: str = '<BhhBB'  # magic(u8), dx(i16), dy(i16), buttons(u8), seq(u8)
PACKET_SIZE: int = struct.calcsize(PACKET_FORMAT)  # 7 bytes

assert PACKET_SIZE == 7, f"Expected packet size 7, got {PACKET_SIZE}"


def parse_packet(data: bytes):
    """Parse a raw packet and validate the magic byte.

    Args:
        data: Raw bytes received from the network.

    Returns:
        Tuple of (dx, dy, buttons, seq) on success, or None if the
        packet is malformed or has an invalid magic byte.
    """
    if len(data) < PACKET_SIZE:
        logger.debug("Packet too short: %d bytes (need %d)", len(data), PACKET_SIZE)
        return None

    try:
        magic, dx, dy, buttons, seq = struct.unpack_from(PACKET_FORMAT, data)
    except struct.error as e:
        logger.debug("Unpack error: %s", e)
        return None

    if magic != PACKET_MAGIC:
        logger.debug("Bad magic: 0x%02X (expected 0x%02X)", magic, PACKET_MAGIC)
        return None

    return (dx, dy, buttons, seq)


def build_packet(dx: int, dy: int, buttons: int, seq: int) -> bytes:
    """Build a wire-format packet (primarily for testing).

    Args:
        dx: Relative X movement (-32768..32767).
        dy: Relative Y movement (-32768..32767).
        buttons: Button bitmask (bits 0-2: left, right, middle).
        seq: Sequence number (0-255).

    Returns:
        7-byte packed packet.
    """
    return struct.pack(PACKET_FORMAT, PACKET_MAGIC, dx, dy, buttons & 0xFF, seq & 0xFF)
