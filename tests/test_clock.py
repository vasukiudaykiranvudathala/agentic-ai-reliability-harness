"""Virtual clock: simulated delay advances logical time without real sleep."""
import time

from arh.clock import VirtualClock


def test_virtual_clock_does_not_block():
    clock = VirtualClock()
    wall_start = time.monotonic()
    clock.sleep(5000)  # 5 simulated seconds
    clock.advance(2500)
    wall_elapsed = time.monotonic() - wall_start
    assert clock.now_ms() == 7500
    assert wall_elapsed < 0.5  # nowhere near 7.5 real seconds
