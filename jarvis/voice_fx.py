"""Post-processing that makes a plain TTS voice sound like an AI butler.

Film JARVIS isn't a raw voice: it's clean and close, with the low end thinned
out, the top end crisp, a faint doubled/metallic edge and a hint of room.
All of that is done here in one FFT pass per sentence (numpy only):

  - high-pass (~140 Hz):   removes the boomy, "depressed" low end
  - presence shelf (+dB above ~2.5 kHz): crisp, forward diction
  - two short echoes (11 ms, 19 ms): the doubled, slightly synthetic edge
  - a very short room (~60 ms decay): sounds like it's in the space, not in your ear

`amount` scales everything: 0 = off, 1 = default, 1.5 = more obviously processed.
"""

from __future__ import annotations

import numpy as np

_TAIL_S = 0.25  # room for the echoes/reverb to ring out after the sentence


def jarvis_fx(audio: np.ndarray, sample_rate: int, amount: float = 1.0) -> np.ndarray:
    """int16 mono in -> int16 mono out (a little longer, to fit the reverb tail)."""
    if amount <= 0 or len(audio) == 0:
        return audio

    x = audio.astype(np.float32) / 32768.0
    tail = int(_TAIL_S * sample_rate)
    n = len(x) + tail
    nfft = 1 << (n - 1).bit_length()

    # Impulse response: dry signal + doubling echoes + a short, quiet room.
    ir = np.zeros(tail, np.float32)
    ir[0] = 1.0
    for delay_ms, gain in ((11, 0.20), (19, 0.10)):
        ir[int(sample_rate * delay_ms / 1000)] += gain * amount
    decay_s = 0.06
    t = np.arange(tail) / sample_rate
    room = np.random.default_rng(7).standard_normal(tail).astype(np.float32) * np.exp(-t / decay_s)
    room[: int(0.008 * sample_rate)] = 0  # let the first reflection arrive a moment later
    # Scale so the room's energy is ~ -15 dB under the dry voice at any sample rate.
    room *= 0.18 * amount / np.sqrt(decay_s / 2 * sample_rate)
    ir += room

    # EQ as a frequency response, applied in the same pass.
    f = np.fft.rfftfreq(nfft, 1 / sample_rate)
    f[0] = 1.0
    high_pass = 1 / np.sqrt(1 + (140 / f) ** 4)
    shelf_gain = 10 ** (4.0 * amount / 20) - 1  # +4 dB at amount 1
    presence = 1 + shelf_gain / (1 + (2500 / f) ** 2)

    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft) * high_pass * presence, nfft)[:n]

    # Keep the loudness about where it was, and never clip.
    peak_in = float(np.max(np.abs(x))) or 1.0
    peak_out = float(np.max(np.abs(y))) or 1.0
    y *= min(peak_in / peak_out, 0.97 / peak_out)
    return (y * 32767).astype(np.int16)
