"""Serialized prompt playback into one audio source. Prompts never overlap.

While idle the player feeds short silence so the published track stays live and unmuted.
`clear()` drops queued prompts and cancels the one playing (retake or hangup).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from partsdesk.speech.pcm import FRAME_BYTES, Resampler, read_wav, split_frames, to_48k
from partsdesk.speech.synth import synthesize as default_synthesize

Sink = Callable[[bytes], Awaitable[None]]
Synthesizer = Callable[[str], Awaitable[bytes]]
MAX_QUEUED_PROMPTS = 4
SILENCE = b"\x00" * FRAME_BYTES


class PromptPlayer:
    def __init__(self, sink: Sink, synthesize: Synthesizer = default_synthesize, resample: Resampler | None = None,
                 on_spoken: Callable[[str], None] | None = None,
                 on_failure: Callable[[str, Exception], None] | None = None,
                 on_clear: Callable[[], None] | None = None,
                 idle_silence_frames: int = 5) -> None:
        self._sink, self._synthesize, self._resample = sink, synthesize, resample
        self._on_spoken = on_spoken or (lambda _text: None)
        self._on_failure = on_failure or (lambda _text, _error: None)
        self._on_clear = on_clear or (lambda: None)
        self._idle_frames = idle_silence_frames
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUED_PROMPTS)
        self._current: asyncio.Task[None] | None = None

    def say(self, text: str) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # keep the newest guidance
        self._queue.put_nowait(text)

    def clear(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._current and not self._current.done():
            self._current.cancel()
        self._on_clear()  # drop audio already buffered downstream (LiveKit AudioSource queue)

    async def run(self) -> None:
        idle_wait = self._idle_frames * 0.01 if self._idle_frames else None
        while True:
            try:
                text = await asyncio.wait_for(self._queue.get(), idle_wait)
            except TimeoutError:
                for _ in range(self._idle_frames):
                    await self._sink(SILENCE)
                continue
            self._current = asyncio.create_task(self._play(text))
            try:
                await self._current
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
            finally:
                self._current = None

    async def _play(self, text: str) -> None:
        try:
            rate, pcm = read_wav(await self._synthesize(text))
            frames = split_frames(to_48k(rate, pcm, self._resample))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # synthesis failure must never stop the call
            self._on_failure(text, error)
            return
        for frame in frames:
            await self._sink(frame)
        self._on_spoken(text)
