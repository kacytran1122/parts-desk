"""LiveKit media worker for one Chert FaceTime call (Bring Your Own Room).

The inspection stage is never changed here. Call lifecycle and media notes go through
InspectionService so the worksheet and the event sequence stay authoritative. CPU work
(frame encoding, card rendering, database writes) runs off the media event loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import textwrap
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from livekit import api, rtc
from PIL import Image, ImageDraw, ImageFont

from partsdesk.chert.client import ChertError, ChertFaceTime
from partsdesk.config import Settings
from partsdesk.domain import CallStatus
from partsdesk.live.calls import (AGENT_IDENTITY, BRIDGE_IDENTITY, BRIDGE_VIDEO_TRACK, classify, has_bridge_ready,
                                  is_terminal, outbound_body, room_name, room_token)
from partsdesk.live.frames import FrameStore
from partsdesk.live.manager import Speaker
from partsdesk.service import InspectionService
from partsdesk.speech.pcm import FRAME_SAMPLES, SAMPLE_RATE, livekit_resample
from partsdesk.speech.player import PromptPlayer
from partsdesk.speech.prompts import GREETING

CARD_SIZE = (640, 360)
CARD_INTERVAL = 0.2
FRAME_INTERVAL = 0.5
MAX_CAPTURE_EDGE = 1280
STALL_SECONDS = 3.0
POLL_SECONDS = 1.5
MAX_CALL_SECONDS = 180
END_CONFIRM_ATTEMPTS = 5
log = logging.getLogger("partsdesk.live")


def render_card(text: str) -> rtc.VideoFrame:
    image = Image.new("RGBA", CARD_SIZE, "#112029")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, 600, 320), radius=20, fill="#1c3540", outline="#69d8b3", width=2)
    draw.text((68, 66), "PARTS DESK / TRAINING", fill="#69d8b3", font=ImageFont.load_default(size=18))
    body = "\n".join(textwrap.wrap(text, width=38)[:6])
    draw.multiline_text((68, 110), body, fill="white", font=ImageFont.load_default(size=24), spacing=8)
    return rtc.VideoFrame(CARD_SIZE[0], CARD_SIZE[1], rtc.VideoBufferType.RGBA, image.tobytes())


def encode_frame(frame: rtc.VideoFrame) -> bytes:
    rgba = frame.convert(rtc.VideoBufferType.RGBA)
    image = Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data)).convert("RGB")
    image.thumbnail((MAX_CAPTURE_EDGE, MAX_CAPTURE_EDGE))
    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()


class _CallSession:
    def __init__(self, settings: Settings, service: InspectionService, frames: FrameStore,
                 http: httpx.AsyncClient, inspection_id: str) -> None:
        self.settings, self.service, self.frames, self.inspection_id = settings, service, frames, inspection_id
        self.facetime = ChertFaceTime(http, settings.chert_project_id, settings.chert_project_key)
        nonce = uuid.uuid4().hex[:8]
        self.room_id = room_name(inspection_id, nonce)
        self.call_key = f"{inspection_id}-{nonce}"
        self.room = rtc.Room()
        self.audio = rtc.AudioSource(SAMPLE_RATE, 1)
        self.video = rtc.VideoSource(*CARD_SIZE)
        self.card = render_card(GREETING)
        self.tasks: set[asyncio.Task[Any]] = set()
        self.video_task: asyncio.Task[Any] | None = None
        self.call_id: str | None = None
        self.dial_attempted = False
        self.ready = False
        self.terminal = False
        self.last_frame = 0.0
        self.player = PromptPlayer(self._send_audio, resample=livekit_resample,
                                   on_failure=self._prompt_failed, on_clear=self.audio.clear_queue)

    # Bookkeeping ----------------------------------------------------------

    def spawn(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Live call task failed: %s", task.exception(), exc_info=task.exception())

    async def call_status(self, status: CallStatus, detail: str, **ids: str) -> None:
        await asyncio.to_thread(self.service.update_call, self.inspection_id, status, detail, **ids)

    async def note(self, kind: str, detail: str) -> None:
        await asyncio.to_thread(self.service.note, self.inspection_id, kind, detail)

    async def _send_audio(self, pcm: bytes) -> None:
        await self.audio.capture_frame(rtc.AudioFrame(pcm, SAMPLE_RATE, 1, FRAME_SAMPLES))

    def _prompt_failed(self, text: str, error: Exception) -> None:
        self.spawn(self._show_on_card(text))
        self.spawn(self.note("prompt_failed", f"Local speech failed ({type(error).__name__}). Prompt: {text}"))

    async def _show_on_card(self, text: str) -> None:
        self.card = await asyncio.to_thread(render_card, text)

    # Media ----------------------------------------------------------------

    async def publish_card(self) -> None:
        while True:
            self.video.capture_frame(self.card)
            await asyncio.sleep(CARD_INTERVAL)

    async def watch_video(self, track: rtc.Track) -> None:
        stream = rtc.VideoStream(track, capacity=1)
        loop, last, announced = asyncio.get_running_loop(), 0.0, False
        try:
            async for event in stream:
                now = loop.time()
                if not self.ready or track.muted or now - last < FRAME_INTERVAL:
                    continue
                self.frames.put(self.inspection_id, await asyncio.to_thread(encode_frame, event.frame))
                last = self.last_frame = now
                if not announced:
                    announced = True
                    self.spawn(self.note("camera_live", "Receiving caller video through Chert. Preview available."))
        finally:
            self.frames.clear(self.inspection_id)
            await stream.aclose()

    async def watch_stalls(self) -> None:
        loop, stalled = asyncio.get_running_loop(), False
        while True:
            await asyncio.sleep(1)
            idle = self.ready and self.last_frame and loop.time() - self.last_frame > STALL_SECONDS
            if idle and not stalled:
                self.frames.clear(self.inspection_id)
                await self.note("camera_stalled",
                                "Caller video stalled. Ask the technician to hold still or check the connection.")
            stalled = bool(idle)

    def bind_room_events(self, end: asyncio.Event) -> None:
        def is_bridge_video(publication: rtc.TrackPublication, participant: rtc.Participant) -> bool:
            return participant.identity == BRIDGE_IDENTITY and publication.name == BRIDGE_VIDEO_TRACK

        @self.room.on("track_subscribed")
        def subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication,
                       participant: rtc.RemoteParticipant) -> None:
            if is_bridge_video(publication, participant):
                if self.video_task and not self.video_task.done():
                    self.video_task.cancel()  # a re-subscribed track replaces the previous watcher
                self.video_task = self.spawn(self.watch_video(track))

        @self.room.on("track_unsubscribed")
        def unsubscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication,
                         participant: rtc.RemoteParticipant) -> None:
            if is_bridge_video(publication, participant):
                self.frames.clear(self.inspection_id)

        @self.room.on("track_muted")
        def muted(participant: rtc.Participant, publication: rtc.TrackPublication) -> None:
            if is_bridge_video(publication, participant):
                self.frames.clear(self.inspection_id)
                self.spawn(self.note("camera_muted", "Caller camera is off. Ask the technician to turn it on, "
                                                     "or use Expect tag iMessage."))

        @self.room.on("participant_disconnected")
        def left(participant: rtc.RemoteParticipant) -> None:
            if participant.identity == BRIDGE_IDENTITY:
                self.frames.clear(self.inspection_id)
                self.spawn(self.note("bridge_left", "The FaceTime bridge left the room."))
                end.set()

        @self.room.on("disconnected")
        def disconnected(reason: Any = None) -> None:
            self.frames.clear(self.inspection_id)
            self.spawn(self.note("agent_disconnected", "The worker lost its LiveKit connection. Ending the call."))
            end.set()

    # Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        await self.room.connect(self.settings.livekit_url, room_token(self.settings, self.room_id, AGENT_IDENTITY))
        await self.room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("guidance", self.audio),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        await self.room.local_participant.publish_track(
            rtc.LocalVideoTrack.create_video_track("exercise-card", self.video),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))

    async def dial(self) -> None:
        await self.call_status(CallStatus.DIALING, "Allocating the FaceTime call.", room_id=self.room_id)
        body = outbound_body(self.settings, room_token(self.settings, self.room_id, BRIDGE_IDENTITY))
        self.dial_attempted = True
        self.call_id = await self.facetime.create_call(body, self.call_key)
        await self.call_status(CallStatus.DIALING, "Chert allocated the call. Waiting for the iPhone to answer.",
                               call_id=self.call_id)

    async def observe(self, end: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        deadline, last = loop.time() + MAX_CALL_SECONDS, None
        while loop.time() < deadline and not end.is_set():
            status = classify(await self.facetime.status(self.call_id))
            if is_terminal(status):
                self.terminal = True
                await self.call_status(status, f"The call ended with status {status}.")
                return
            if status is not None and status is not last and status is not CallStatus.CONNECTED:
                await self.call_status(status, f"Call status: {status}.")
            last = status
            if not self.ready and has_bridge_ready(await self.facetime.events(self.call_id)):
                self.ready = True
                await self.call_status(CallStatus.CONNECTED, "The answered FaceTime bridge is ready.")
                self.player.say(GREETING)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(end.wait(), POLL_SECONDS)
        if not end.is_set():
            await self.note("call_time_limit", "The rehearsal time limit was reached. Ending the call.")

    async def hang_up(self) -> None:
        if self.terminal:
            return
        if not self.call_id:
            if self.dial_attempted:  # Chert may have allocated a call we never heard back about
                await self.note("call_end_unconfirmed", "Call allocation did not confirm. Check the Chert "
                                                        "console for an active call before dialing again.")
            return
        await self.call_status(CallStatus.ENDING, "End requested. Waiting for Chert to confirm.")
        try:
            await self.facetime.end(self.call_id, f"end-{self.call_key}")
            for _ in range(END_CONFIRM_ATTEMPTS):
                status = classify(await self.facetime.status(self.call_id))
                if is_terminal(status):
                    self.terminal = True
                    await self.call_status(status, f"Chert confirmed the call ended ({status}).")
                    return
                await asyncio.sleep(1)
        except ChertError as error:
            log.warning("Chert end request failed: %s", error)
        await self.note("call_end_unconfirmed", "Confirm termination in the Chert console before dialing again.")

    async def release(self) -> None:
        self.player.clear()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.frames.clear(self.inspection_id)
        if self.room.isconnected():
            await self.room.disconnect()
        try:
            async with api.LiveKitAPI(self.settings.livekit_url, self.settings.livekit_api_key,
                                      self.settings.livekit_api_secret) as service:
                await service.room.delete_room(api.DeleteRoomRequest(room=self.room_id))
        except Exception as error:  # noqa: BLE001 - room cleanup is best effort and must not mask the call result
            log.warning("LiveKit room cleanup failed (%s).", type(error).__name__)
        await self.note("room_released", "The local worker released the LiveKit room.")


async def run_call(*, settings: Settings, service: InspectionService, frames: FrameStore, http: httpx.AsyncClient,
                   inspection_id: str, end: asyncio.Event,
                   attach: Callable[[Speaker | None], None]) -> None:
    session = _CallSession(settings, service, frames, http, inspection_id)
    session.bind_room_events(end)
    phase = "preparing the room"
    try:
        await session.connect()
        session.spawn(session.player.run())
        session.spawn(session.publish_card())
        session.spawn(session.watch_stalls())
        attach(session.player)
        phase = "allocating the call"
        await session.dial()
        phase = "observing the call"
        await session.observe(end)
    except Exception as error:  # noqa: BLE001 - any failure must be recorded, then the call cleaned up
        log.exception("FaceTime worker failed while %s", phase)
        with contextlib.suppress(Exception):
            await session.call_status(CallStatus.FAILED, f"FaceTime stopped while {phase} "
                                                         f"({type(error).__name__}). Check the Chert console.")
    finally:
        attach(None)
        try:
            await session.hang_up()
        finally:
            await session.release()
