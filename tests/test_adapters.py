"""Config, speech synthesis and playback, Chert clients, and live call helpers."""
import asyncio
import io
import json
import shutil
import wave
from pathlib import Path

import httpx
import pytest

from partsdesk.chert.client import ChertError, ChertFaceTime, ChertMessaging
from partsdesk.config import Settings
from partsdesk.domain import CallStatus
from partsdesk.live.calls import classify, has_bridge_ready, is_terminal, outbound_body, room_name
from partsdesk.live.frames import FrameStore
from partsdesk.live.manager import CallBusy, LiveCalls
from partsdesk.speech.pcm import FRAME_BYTES, read_wav
from partsdesk.speech.player import SILENCE, PromptPlayer
from partsdesk.speech.synth import SpeechError, engine_command, synthesize

LIVE_ENV = {"PARTS_DESK_LIVE": "1", "PARTS_DESK_OPS_TOKEN": "t" * 24, "PARTS_DESK_TEST_PHONE": "+15550100",
            "CHERT_API_KEY": "k", "CHERT_TENANT": "tenant",
            "CHERT_WEBHOOK_SECRET": "s", "CHERT_PROJECT_ID": "p", "CHERT_PROJECT_KEY": "pk",
            "CHERT_FACETIME_LINE_ID": "line", "LIVEKIT_URL": "wss://lk", "LIVEKIT_API_KEY": "a",
            "LIVEKIT_API_SECRET": "b"}


def wav(samples: int = 1000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"\x01\x00" * samples)
    return buffer.getvalue()


# Config -------------------------------------------------------------------

def test_settings_default_to_safe_drill_mode() -> None:
    settings = Settings.from_env({})
    assert not settings.live and not settings.facetime_ready and settings.problems() == ()


def test_live_settings_report_readiness_and_problems_without_values() -> None:
    assert Settings.from_env(LIVE_ENV).facetime_ready and Settings.from_env(LIVE_ENV).messaging_ready
    assert Settings.from_env(LIVE_ENV).problems() == ()
    assert not Settings.from_env({**LIVE_ENV, "CHERT_TENANT": ""}).messaging_ready
    problems = Settings.from_env({"PARTS_DESK_LIVE": "1", "PARTS_DESK_TEST_PHONE": "555"}).problems()
    assert any("PARTS_DESK_TEST_PHONE" in item for item in problems)
    assert any("CHERT_WEBHOOK_SECRET" in item for item in problems)
    assert any("LIVEKIT_API_SECRET" in item for item in problems)
    assert all("555" not in item for item in problems)
    assert Settings.from_env({**LIVE_ENV, "LIVEKIT_URL": "ws://lk"}).problems()
    weak = Settings.from_env({"PARTS_DESK_OPS_TOKEN": "hunter2"}).problems()
    assert len(weak) == 1 and "hunter2" not in weak[0]


# Speech -------------------------------------------------------------------

def test_engine_prefers_espeak_with_stdin_and_falls_back_to_say(tmp_path: Path) -> None:
    out = tmp_path / "p.wav"
    espeak = engine_command(out, which=lambda name: f"/bin/{name}" if name == "espeak-ng" else None)
    assert espeak[:7] == ("/bin/espeak-ng", "-v", "en-us", "-s", "145", "--stdin", "-w")
    say = engine_command(out, which=lambda name: "/usr/bin/say" if name == "say" else None)
    assert say[-2:] == ("-f", "-")
    with pytest.raises(SpeechError):
        engine_command(out, which=lambda name: None)


@pytest.mark.skipif(not (shutil.which("espeak-ng") or shutil.which("say")), reason="no local speech engine")
def test_local_synthesis_produces_non_silent_mono_pcm() -> None:
    rate, pcm = read_wav(asyncio.run(synthesize("Serial. Zero zero seven two.")))
    assert rate > 0 and len(pcm) > FRAME_BYTES and any(pcm)


def test_prompt_text_is_never_passed_as_an_argument(tmp_path: Path) -> None:
    command = engine_command(tmp_path / "p.wav", which=lambda name: "/bin/espeak-ng" if name == "espeak-ng" else None)
    assert "--stdin" in command and not any("Serial" in part for part in command)


def run_player(prompts: list[str], synth, clear_after: int | None = None):
    frames, spoken, failed = [], [], []

    async def scenario() -> None:
        async def sink(frame: bytes) -> None:
            frames.append(frame)
            await asyncio.sleep(0)

        async def interrupt() -> None:  # a separate task, like the API handling a retake
            while len(frames) < clear_after:
                await asyncio.sleep(0)
            player.clear()

        player = PromptPlayer(sink, synthesize=synth, on_spoken=spoken.append,
                              on_failure=lambda text, error: failed.append(text), idle_silence_frames=0)
        task = asyncio.create_task(player.run())
        for prompt in prompts:
            player.say(prompt)
        if clear_after is not None:
            await interrupt()
        for _ in range(200):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    return frames, spoken, failed


def test_player_streams_10ms_frames_and_serializes_prompts() -> None:
    async def synth(text: str) -> bytes:
        return wav(1000)

    frames, spoken, failed = run_player(["one", "two"], synth)
    assert len(frames) == 6 and all(len(frame) == FRAME_BYTES for frame in frames)
    assert spoken == ["one", "two"] and failed == []


def test_player_reports_synthesis_failure_and_keeps_running() -> None:
    async def synth(text: str) -> bytes:
        if text == "bad":
            raise SpeechError("boom")
        return wav(480)

    frames, spoken, failed = run_player(["bad", "good"], synth)
    assert failed == ["bad"] and spoken == ["good"]


def test_player_clear_cancels_the_current_prompt() -> None:
    async def synth(text: str) -> bytes:
        return wav(48000)

    frames, spoken, _ = run_player(["long"], synth, clear_after=3)
    assert 3 <= len(frames) < 10  # a 1 s prompt is 100 frames; playback stops right away
    assert spoken == []


def test_player_clear_flushes_queued_output_audio() -> None:
    flushed = []
    player = PromptPlayer(lambda frame: asyncio.sleep(0), on_clear=lambda: flushed.append(True))
    player.say("queued")
    player.clear()
    assert flushed == [True]


def test_crashed_call_worker_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        calls = LiveCalls()

        async def worker(end: asyncio.Event, attach) -> None:
            raise RuntimeError("media failure")

        calls.start("i1", worker)
        await asyncio.sleep(0.01)
        assert calls.active_inspection is None

    with caplog.at_level("ERROR", logger="partsdesk.live"):
        asyncio.run(scenario())
    assert "media failure" in caplog.text


def test_player_feeds_silence_while_idle() -> None:
    frames = []

    async def scenario() -> None:
        async def sink(frame: bytes) -> None:
            frames.append(frame)

        task = asyncio.create_task(PromptPlayer(sink, idle_silence_frames=2).run())
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert frames and set(frames) == {SILENCE}


# Chert clients ------------------------------------------------------------

def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_attachment_download_sends_auth_and_enforces_type_and_size() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        kind = "image/png" if "png" in request.url.path else "text/html"
        if "generic" in request.url.path:
            kind = "application/octet-stream"
        return httpx.Response(200, headers={"content-type": kind}, content=b"\x89PNG\r\n\x1a\n" + b"0" * 20)

    async def scenario():
        async with client(handler) as http:
            messaging = ChertMessaging(http, "key", tenant="tenant")
            data = await messaging.download_attachment("att_png")
            assert await messaging.download_attachment("att_generic") == data
            with pytest.raises(ChertError):
                await messaging.download_attachment("att_html")
            with pytest.raises(ChertError):
                await messaging.download_attachment("att_png", max_bytes=4)
            with pytest.raises(ChertError):
                await messaging.download_attachment("../etc")
            return data

    assert asyncio.run(scenario()).startswith(b"\x89PNG")
    assert seen["authorization"] == "Bearer key" and seen["x-chert-tenant"] == "tenant"


def test_create_call_retries_once_with_the_same_idempotency_key() -> None:
    keys, bodies = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        bodies.append(request.content)
        if len(keys) == 1:
            return httpx.Response(503)
        return httpx.Response(201, json={"data": {"id": "call_1"}})

    async def scenario():
        async with client(handler) as http:
            return await ChertFaceTime(http, "p", "pk").create_call({"a": 1}, "key-1")

    assert asyncio.run(scenario()) == "call_1"
    assert keys == ["key-1", "key-1"] and bodies[0] == bodies[1]


def test_create_call_surfaces_rejection_without_secrets() -> None:
    async def scenario():
        async with client(lambda request: httpx.Response(422, json={})) as http:
            await ChertFaceTime(http, "p", "secret-key").create_call({}, "k")

    with pytest.raises(ChertError) as caught:
        asyncio.run(scenario())
    assert "422" in str(caught.value) and "secret-key" not in str(caught.value)


def test_status_events_and_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"data": {"events": [{"event_type": "bridge_ready"}]}})
        if request.url.path.endswith("/end"):
            return httpx.Response(202, json={})
        return httpx.Response(200, json={"data": {"status": "Ringing"}})

    async def scenario():
        async with client(handler) as http:
            facetime = ChertFaceTime(http, "p", "pk")
            await facetime.end("call_1", "end-key")
            return await facetime.status("call_1"), await facetime.events("call_1")

    status, events = asyncio.run(scenario())
    assert classify(status) is CallStatus.RINGING and has_bridge_ready(events)


# Live helpers -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected,terminal", [
    ("no_answer", CallStatus.NO_ANSWER, True), ("BUSY", CallStatus.BUSY, True),
    ("completed", CallStatus.ENDED, True), ("in_progress", CallStatus.CONNECTED, False),
])
def test_call_status_mapping(raw: str, expected: CallStatus, terminal: bool) -> None:
    assert classify(raw) is expected and is_terminal(classify(raw)) is terminal
    assert classify("mystery") is None


def test_bridge_ready_detection_accepts_list_or_nested_events() -> None:
    assert has_bridge_ready({"data": [{"type": "call.started"}]})
    assert not has_bridge_ready({"data": {"events": [{"type": "ringing"}]}})


def test_outbound_body_uses_byor_mode_and_distinct_identities() -> None:
    body = outbound_body(Settings.from_env(LIVE_ENV), "bridge-jwt")
    assert body["mode"] == "customer_managed" and body["participant_token"] == "bridge-jwt"
    assert body["remote_participant_identity"] == "parts-desk-agent" and body["destination"] == "+15550100"
    assert room_name("abc", "n1") == "parts-desk-abc-n1"


def test_frame_store_expires_stale_frames() -> None:
    now = [0.0]
    store = FrameStore(clock=lambda: now[0])
    store.put("i1", b"jpeg")
    assert store.latest("i1") == b"jpeg"
    now[0] = 3.5
    assert store.latest("i1") is None
    store.put("i1", b"jpeg2")
    store.clear("i1")
    assert store.latest("i1") is None


def test_live_calls_allows_one_call_and_routes_prompts() -> None:
    class Speaker:
        def __init__(self) -> None:
            self.said: list[str] = []
            self.cleared = False

        def say(self, text: str) -> None:
            self.said.append(text)

        def clear(self) -> None:
            self.cleared = True

    async def scenario():
        calls, speaker = LiveCalls(), Speaker()

        async def worker(end: asyncio.Event, attach) -> None:
            attach(speaker)
            await end.wait()

        calls.start("i1", worker)
        await asyncio.sleep(0)
        with pytest.raises(CallBusy):
            calls.start("i2", worker)
        assert calls.say("i1", "hello") and not calls.say("i2", "x")
        calls.clear_prompts("i1")
        assert calls.request_end("i1")
        await calls.shutdown()
        await asyncio.sleep(0)
        return calls, speaker

    calls, speaker = asyncio.run(scenario())
    assert speaker.said == ["hello"] and speaker.cleared and calls.active_inspection is None
    assert json.dumps(speaker.said)
