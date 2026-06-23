"""
udp_listener.py — UDP receiver thread for incoming HID mouse packets.

Binds to 0.0.0.0:<port> and continuously receives packets, parsing
them with the protocol module. Valid deltas are accumulated into the
shared InputMerger. The thread runs as a daemon and checks a stop
flag on each timeout cycle for clean shutdown.
"""

import socket
import threading
import logging

from protocol import parse_packet

logger = logging.getLogger(__name__)


class UDPListener(threading.Thread):
    """Daemon thread that receives UDP HID packets and feeds InputMerger."""

    def __init__(self, port: int, merger, log_level: int = logging.INFO):
        super().__init__(name="UDPListener", daemon=True)
        self._port = port
        self._merger = merger
        self._running = False
        self._sock = None

        logger.setLevel(log_level)

    def run(self) -> None:
        """Thread entry point — bind socket and receive loop."""
        self._running = True
        logger.info("UDP listener starting on port %d", self._port)

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
            self._sock.settimeout(0.05)  # 50ms timeout for shutdown checks
            self._sock.bind(('0.0.0.0', self._port))
        except OSError as e:
            logger.error("Failed to bind UDP socket on port %d: %s", self._port, e)
            return

        logger.info("UDP listener bound to 0.0.0.0:%d", self._port)
        packets_received = 0

        while self._running:
            try:
                data, addr = self._sock.recvfrom(64)
            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    logger.error("UDP recv error: %s", e)
                break

            result = parse_packet(data)
            if result is None:
                continue

            dx, dy, buttons, seq = result
            self._merger.accumulate(dx, dy, buttons, source='network')

            packets_received += 1
            if packets_received % 10000 == 0:
                logger.debug("UDP packets received: %d (last from %s)", packets_received, addr)

        self._cleanup()
        logger.info("UDP listener stopped (total packets: %d)", packets_received)

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._running = False
        logger.debug("UDP listener stop requested")

    def _cleanup(self) -> None:
        """Close the socket."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
