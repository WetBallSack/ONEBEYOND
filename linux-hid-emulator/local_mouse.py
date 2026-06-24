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

    def __init__(self, merger, log_level: int = logging.INFO):
        super().__init__(name="LocalMouseReader", daemon=True)
        self._merger = merger
        self._running = False
        logger.setLevel(log_level)

    def run(self) -> None:
        self._running = True
        logger.info("Local mouse reader starting (evdev)")

        if not _EVDEV_AVAILABLE:
            logger.error("evdev library is not installed. Fix: pip install evdev")
            return

        while self._running:
            try:
                self._read_loop()
            except Exception as e:
                if self._running:
                    logger.warning("Mouse reader error: %s — retrying in 1s", e)
                    time.sleep(1.0)

        logger.info("Local mouse reader stopped")

    def _find_mice(self) -> list:
        mice = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                rel_axes = caps.get(ecodes.EV_REL, [])
                if ecodes.REL_X in rel_axes and ecodes.REL_Y in rel_axes:
                    if 'isa0060' not in (dev.phys or ''):
                        mice.append(dev)
                        logger.info("Found mouse: %s (%s)", dev.name, path)
                    else:
                        logger.info("Skipping virtual PS/2 device: %s (%s)", dev.name, path)
                        dev.close()
                else:
                    dev.close()
            except Exception as e:
                logger.debug("Skipping %s: %s", path, e)
        return mice

    def _read_loop(self) -> None:
        mice = self._find_mice()

        if not mice:
            logger.warning("No mice found in /dev/input/event* — retrying in 2s")
            time.sleep(2.0)
            return

        logger.info("Monitoring %d mouse device(s)", len(mice))

        fd_to_dev = {dev.fd: dev for dev in mice}
        buttons_by_fd = {dev.fd: 0 for dev in mice}

        try:
            while self._running and fd_to_dev:
                try:
                    readable, _, _ = select.select(list(fd_to_dev.keys()), [], [], 1.0)
                except (ValueError, OSError):
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
                            pressed = bool(event.value)
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

        if self._running:
            logger.info("All mouse devices lost — rescanning in 2s")
            time.sleep(2.0)

    def stop(self) -> None:
        self._running = False
        logger.debug("Local mouse reader stop requested")
