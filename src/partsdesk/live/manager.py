"""One active call at a time, plus a handle to its prompt player and end request."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

log = logging.getLogger("partsdesk.live")


class Speaker(Protocol):
    def say(self, text: str) -> None: ...

    def clear(self) -> None: ...


class CallBusy(Exception):
    pass


class LiveCalls:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._inspection_id: str | None = None
        self._speaker: Speaker | None = None
        self._end = asyncio.Event()

    @property
    def active_inspection(self) -> str | None:
        return self._inspection_id if self._task and not self._task.done() else None

    def start(self, inspection_id: str,
              factory: Callable[[asyncio.Event, Callable[[Speaker | None], None]], Coroutine[Any, Any, None]]) -> None:
        if self.active_inspection:
            raise CallBusy("The exercise line already has an active call.")
        self._end = asyncio.Event()
        self._inspection_id = inspection_id
        self._task = asyncio.create_task(factory(self._end, self._attach))
        self._task.add_done_callback(self._finished)

    def _attach(self, speaker: Speaker | None) -> None:
        self._speaker = speaker

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._attach(None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("Live call worker stopped unexpectedly: %s", error, exc_info=error)

    def say(self, inspection_id: str, text: str) -> bool:
        if self.active_inspection != inspection_id or self._speaker is None:
            return False
        self._speaker.say(text)
        return True

    def clear_prompts(self, inspection_id: str) -> None:
        if self.active_inspection == inspection_id and self._speaker is not None:
            self._speaker.clear()

    def request_end(self, inspection_id: str) -> bool:
        if self.active_inspection != inspection_id:
            return False
        self._end.set()
        return True

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._end.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 15)
            except TimeoutError:
                self._task.cancel()
