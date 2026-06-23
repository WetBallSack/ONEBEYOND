"""
input_merger.py — Thread-safe mouse input delta accumulator.

Merges mouse deltas from multiple input sources (UDP network, local
/dev/input/mice) under a lock. The flush() method returns clamped
values suitable for an HID report (-127..127) while preserving any
remainder for the next tick, ensuring no motion is lost.

Button state is tracked per-source: each source REPLACES its own
button state on every update. The final output is the bitwise OR of
all sources, so a button held by either source stays pressed. This
avoids the problem of flush()-resetting buttons to 0 when a button
is held but no new packets arrive.
"""

import threading
import logging

logger = logging.getLogger(__name__)

# HID report clamp range for signed 8-bit relative values
_CLAMP_MIN = -127
_CLAMP_MAX = 127


class InputMerger:
    """Accumulates mouse dx/dy deltas and button state from multiple threads."""

    __slots__ = ('_dx', '_dy', '_buttons_network', '_buttons_local', '_lock')

    def __init__(self):
        self._dx: int = 0
        self._dy: int = 0
        self._buttons_network: int = 0  # Last known button state from network
        self._buttons_local: int = 0    # Last known button state from local mouse
        self._lock = threading.Lock()

    def accumulate(self, dx: int, dy: int, buttons: int,
                   source: str = 'network') -> None:
        """Add motion deltas and update per-source button state.

        Each source's button state is REPLACED (not ORed) on every call,
        so that button releases propagate correctly. The final merged
        output is the OR of all sources' latest states.

        Args:
            dx: Relative X movement to add.
            dy: Relative Y movement to add.
            buttons: Button bitmask — replaces this source's state.
            source: 'network' or 'local' — identifies the input source.
        """
        with self._lock:
            self._dx += dx
            self._dy += dy
            if source == 'network':
                self._buttons_network = buttons
            else:
                self._buttons_local = buttons

    def flush(self) -> tuple:
        """Read and reset accumulated motion, returning merged button state.

        Returns:
            (dx, dy, buttons) where dx and dy are clamped to [-127, 127].
            Any remainder beyond the clamp range is preserved for the
            next flush call. Button state is NOT reset — it persists as
            the last known state from each source.
        """
        with self._lock:
            # Clamp dx
            if self._dx > _CLAMP_MAX:
                clamped_dx = _CLAMP_MAX
                self._dx -= _CLAMP_MAX
            elif self._dx < _CLAMP_MIN:
                clamped_dx = _CLAMP_MIN
                self._dx -= _CLAMP_MIN
            else:
                clamped_dx = self._dx
                self._dx = 0

            # Clamp dy
            if self._dy > _CLAMP_MAX:
                clamped_dy = _CLAMP_MAX
                self._dy -= _CLAMP_MAX
            elif self._dy < _CLAMP_MIN:
                clamped_dy = _CLAMP_MIN
                self._dy -= _CLAMP_MIN
            else:
                clamped_dy = self._dy
                self._dy = 0

            # Merge buttons: OR of both sources' latest known states.
            # NOT reset — each source's state persists until updated.
            buttons = self._buttons_network | self._buttons_local

            return (clamped_dx, clamped_dy, buttons)
