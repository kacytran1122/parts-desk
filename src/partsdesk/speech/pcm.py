"""WAV decoding and 10 ms framing for a 48 kHz mono LiveKit audio source."""
from __future__ import annotations

import io
import wave
from collections.abc import Callable

SAMPLE_RATE = 48_000
FRAME_SAMPLES = 480
FRAME_BYTES = FRAME_SAMPLES * 2

Resampler = Callable[[int, bytes], bytes]


class AudioFormatError(ValueError):
    pass


def read_wav(data: bytes) -> tuple[int, bytes]:
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            if (source.getnchannels(), source.getsampwidth()) != (1, 2):
                raise AudioFormatError("Expected mono signed 16-bit PCM.")
            return source.getframerate(), source.readframes(source.getnframes())
    except (wave.Error, EOFError) as error:
        raise AudioFormatError("Synthesizer output is not a readable WAV.") from error


def to_48k(rate: int, pcm: bytes, resample: Resampler | None) -> bytes:
    if rate == SAMPLE_RATE:
        return pcm
    if resample is None:
        raise AudioFormatError(f"Audio is {rate} Hz and no resampler is available.")
    return resample(rate, pcm)


def split_frames(pcm: bytes) -> tuple[bytes, ...]:
    if len(pcm) % 2:
        raise AudioFormatError("PCM byte count must be even for 16-bit samples.")
    return tuple(pcm[start:start + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00")
                 for start in range(0, len(pcm), FRAME_BYTES))


def livekit_resample(rate: int, pcm: bytes) -> bytes:  # pragma: no cover - needs the facetime extra
    from livekit import rtc

    resampler = rtc.AudioResampler(rate, SAMPLE_RATE, num_channels=1)
    frames = list(resampler.push(bytearray(pcm))) + list(resampler.flush())
    return b"".join(frame.data.tobytes() for frame in frames)
