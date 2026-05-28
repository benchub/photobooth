"""Generate placeholder beep.wav and shutter.wav using stdlib `wave`.

Run once: `python tools/make_sounds.py`. Replace these with nicer audio later
by dropping new files into assets/sounds/ — the loader uses whatever it finds.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "sounds"
SAMPLE_RATE = 44100


def _write_mono_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, s))) for s in samples))


def beep(freq: float = 880.0, duration_s: float = 0.08, volume: float = 0.45) -> list[int]:
    n = int(SAMPLE_RATE * duration_s)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        # Quick attack/decay envelope so it doesn't click.
        env = min(1.0, t / 0.005) * min(1.0, (duration_s - t) / 0.02)
        s = math.sin(2 * math.pi * freq * t) * env * volume
        samples.append(int(s * 32767))
    return samples


def shutter(duration_s: float = 0.18) -> list[int]:
    """A two-click 'clack' suggestive of a DSLR shutter."""
    import random
    rnd = random.Random(123)
    n = int(SAMPLE_RATE * duration_s)
    samples = [0] * n

    def burst(start_t: float, length_t: float, volume: float) -> None:
        s_start = int(start_t * SAMPLE_RATE)
        s_end = min(n, int((start_t + length_t) * SAMPLE_RATE))
        for i in range(s_start, s_end):
            t = (i - s_start) / SAMPLE_RATE
            env = min(1.0, t / 0.002) * max(0.0, 1 - t / length_t)
            samples[i] += int((rnd.random() * 2 - 1) * volume * env * 32767)

    burst(0.00, 0.040, 0.6)
    burst(0.08, 0.060, 0.5)
    return samples


def film_tick(duration_s: float = 0.13, volume: float = 0.55) -> list[int]:
    """Sharp wooden clack of a projector frame advance — used on each second."""
    import random
    rnd = random.Random(7)
    n = int(SAMPLE_RATE * duration_s)
    samples = [0] * n
    # Two transients close together: the perf engaging then snapping past.
    for start_t, length_t, vol in [(0.0, 0.025, 0.7), (0.018, 0.07, 0.45)]:
        s_start = int(start_t * SAMPLE_RATE)
        s_end = min(n, int((start_t + length_t) * SAMPLE_RATE))
        for i in range(s_start, s_end):
            t = (i - s_start) / SAMPLE_RATE
            env = min(1.0, t / 0.001) * (1 - t / length_t) ** 2
            # Filtered noise (low-mid) + small sinusoid.
            noise = rnd.random() * 2 - 1
            tone = math.sin(2 * math.pi * 320 * t) * 0.3
            samples[i] += int((noise * 0.7 + tone) * vol * env * volume * 32767)
    return samples


def film_rumble(duration_s: float = 1.0, volume: float = 0.18) -> list[int]:
    """One-second loopable projector motor: low rumble + 24 Hz perf clicks."""
    import random
    rnd = random.Random(99)
    n = int(SAMPLE_RATE * duration_s)
    samples = [0.0] * n

    # Low-freq rumble: sum of low sines + light noise.
    for i in range(n):
        t = i / SAMPLE_RATE
        # 60 + 90 Hz fundamentals with slow tremolo for warmth.
        s = (
            math.sin(2 * math.pi * 62 * t) * 0.35
            + math.sin(2 * math.pi * 96 * t) * 0.25
            + math.sin(2 * math.pi * 140 * t) * 0.12
        )
        # Slow amplitude wobble.
        s *= 0.8 + 0.2 * math.sin(2 * math.pi * 4.5 * t)
        samples[i] = s

    # 24 fps "perf going through gate" clicks.
    perf_rate_hz = 24.0
    period = 1.0 / perf_rate_hz
    click_len = 0.012
    t = 0.0
    while t < duration_s:
        s_start = int(t * SAMPLE_RATE)
        s_end = min(n, int((t + click_len) * SAMPLE_RATE))
        for i in range(s_start, s_end):
            local_t = (i - s_start) / SAMPLE_RATE
            env = (1 - local_t / click_len) ** 3
            samples[i] += (rnd.random() * 2 - 1) * env * 0.55
        t += period

    # Light pink-ish noise floor.
    for i in range(n):
        samples[i] += (rnd.random() * 2 - 1) * 0.05

    # Normalize/cap.
    return [int(max(-1, min(1, s)) * volume * 32767) for s in samples]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, samples in (
        ("beep.wav", beep()),
        ("shutter.wav", shutter()),
        ("film_tick.wav", film_tick()),
        ("film_rumble.wav", film_rumble()),
    ):
        path = OUT_DIR / name
        if path.exists():
            print(f"skip {name} (exists)")
            continue
        _write_mono_wav(path, samples)
        print(f"wrote {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
