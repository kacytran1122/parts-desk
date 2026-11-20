import io
import wave

import pytest

from partsdesk.speech.pcm import (FRAME_BYTES, AudioFormatError, read_wav, split_frames,
                                  to_48k)


def wav_bytes(rate=48000, channels=1, width=2, samples=1000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"\x01\x00" * samples * channels if width == 2 else b"\x01" * samples)
    return buffer.getvalue()


def test_read_wav_returns_rate_and_pcm():
    rate, pcm = read_wav(wav_bytes(samples=10))
    assert rate == 48000 and len(pcm) == 20


@pytest.mark.parametrize("kwargs", [{"channels": 2}, {"width": 1}])
def test_read_wav_rejects_non_mono_16_bit(kwargs):
    with pytest.raises(AudioFormatError):
        read_wav(wav_bytes(**kwargs))


def test_read_wav_rejects_garbage():
    with pytest.raises(AudioFormatError):
        read_wav(b"not a wav")


def test_split_frames_uses_10ms_frames_and_pads_last_with_silence():
    frames = split_frames(b"\x01\x00" * 1000)
    assert [len(frame) for frame in frames] == [FRAME_BYTES, FRAME_BYTES, FRAME_BYTES]
    assert frames[-1].endswith(b"\x00" * (FRAME_BYTES - 80))


def test_split_frames_rejects_odd_byte_count():
    with pytest.raises(AudioFormatError):
        split_frames(b"\x00\x00\x00")


def test_to_48k_passes_through_or_delegates_to_resampler():
    assert to_48k(48000, b"ab", resample=None) == b"ab"
    assert to_48k(22050, b"ab", resample=lambda rate, pcm: pcm * 2) == b"abab"
    with pytest.raises(AudioFormatError):
        to_48k(22050, b"ab", resample=None)
