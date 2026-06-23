"""
local_mouse.py — Local mouse reader thread (evdev-based).

Reads physical mouse input directly from /dev/input/event* using the
evdev library, bypassing /dev/input/mice entirely. This fixes two bugs
in the previous implementation:

  1. /dev/input/mice requires the mousedev kernel module, which is not
     loaded on headless or live-USB Linux systems. Without it the device
     either does not exist or blocks forever on read(), silently dropping
     all local mouse input.

  2. /dev/input/mice auto-negotiates packet size (3 or 4 bytes depending
     on mouse protocol). Reading a fixed 3 bytes desyncs the stream on
     any ImExPS/2 mouse, producing garbage deltas.

evdev reads properly-typed, correctly-sized events directly from the
kernel input layer and is not affected by either issue.

Dependency: pip install evdev
"""

import select
import time
import threading
import logging

logger = logging.getLogger(__name__)

try:
    import evdev
    from evdev import ecodes
    _EVDEV_AVAILABLE = True
except ImportError:
    _EVDEV_AVAILABLE = False


class LocalMouseReader(threading.Thread):
    """Daemon thread that reads all local mice via evdev and feeds InputMerger."""

    def __init__(self, merger, log_level: int = logging.INFO):
        super().__init__(name="LocalMouseReader", daemon=True)
        self._merger = merger
        self._running = False
        logger.setLevel(log_level)

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._running = True
        logger.info("Local mouse reader starting (evdev)")

        if not _EVDEV_AVAILABLE:
            logger.error(
                "evdev library is not installed — local mouse input disabled. "
                "Fix: pip install evdev"
            )
            return

        while self._running:
            try:
                self._read_loop()
            except Exception as e:
                if self._running:
                    logger.warning("Mouse reader error: %s — retrying in 1s", e)
                    time.sleep(1.0)

        logger.info("Local mouse reader stopped")

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------

    def _find_mice(self) -> list:
        """Scan /dev/input/event* and return all devices that have X/Y axes."""
        mice = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                rel_axes = caps.get(ecodes.EV_REL, [])
                if ecodes.REL_X in rel_axes and ecodes.REL_Y in rel_axes:
                    mice.append(dev)
                    logger.info("Found mouse: %s (%s)", dev.name, path)
                else:
                    dev.close()
            except Exception as e:
                logger.debug("Skipping %s: %s", path, e)
        return mice

    # ------------------------------------------------------------------
    # Main read loop
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Open all discovered mice and multiplex their events via select()."""
        mice = self._find_mice()

        if not mice:
            logger.warning("No mice found in /dev/input/event* — retrying in 2s")
            time.sleep(2.0)
            return

        logger.info("Monitoring %d mouse device(s)", len(mice))

        # Map file-descriptor → device so we can remove dead devices at runtime
        fd_to_dev = {dev.fd: dev for dev in mice}

        # Per-device persistent button state (so held buttons survive across reads)
        buttons_by_fd = {dev.fd: 0 for dev in mice}

        try:
            while self._running and fd_to_dev:
                try:
                    readable, _, _ = select.select(list(fd_to_dev.keys()), [], [], 1.0)
                except (ValueError, OSError):
                    # A file descriptor became invalid; let the outer loop retry
                    break

                for fd in readable:
                    dev = fd_to_dev.get(fd)
                    if dev is None:
                        continue

                    try:
                        events = dev.read()
                    except OSError as e:
                        logger.warning("Device %s lost: %s — removing", dev.path, e)
                        try:
                            dev.close()
                        except Exception:
                            pass
                        fd_to_dev.pop(fd, None)
                        buttons_by_fd.pop(fd, None)
                        continue

                    dx = 0
                    dy = 0
                    btn = buttons_by_fd[fd]
                    changed = False

                    for event in events:
                        if event.type == ecodes.EV_REL:
                            if event.code == ecodes.REL_X:
                                dx += event.value
                                changed = True
                            elif event.code == ecodes.REL_Y:
                                dy += event.value
                                changed = True

                        elif event.type == ecodes.EV_KEY:
                            pressed = bool(event.value)  # 1=down, 0=up, 2=repeat
                            if event.code == ecodes.BTN_LEFT:
                                btn = (btn | 0x01) if pressed else (btn & ~0x01)
                                changed = True
                            elif event.code == ecodes.BTN_RIGHT:
                                btn = (btn | 0x02) if pressed else (btn & ~0x02)
                                changed = True
                            elif event.code == ecodes.BTN_MIDDLE:
                                btn = (btn | 0x04) if pressed else (btn & ~0x04)
                                changed = True

                    if changed:
                        buttons_by_fd[fd] = btn
                        self._merger.accumulate(dx, dy, btn & 0x07, source='local')

        finally:
            for dev in fd_to_dev.values():
                try:
                    dev.close()
                except Exception:
                    pass

        # If we fell out because all devices disconnected, retry discovery
        if self._running:
            logger.info("All mouse devices lost — rescanning in 2s")
            time.sleep(2.0)

    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._running = False
        logger.debug("Local mouse reader stop requested")
