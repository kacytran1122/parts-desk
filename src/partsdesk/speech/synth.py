"""Local speech synthesis. The prompt goes to the engine on stdin as UTF-8, never as argv."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from partsdesk.speech.prompts import clean_prompt

SYNTH_TIMEOUT_SECONDS = 8.0
Which = Callable[[str], str | None]


class SpeechError(RuntimeError):
    pass


def engine_command(output: Path, which: Which = shutil.which) -> tuple[str, ...]:
    espeak = which("espeak-ng")
    if espeak:
        return (espeak, "-v", "en-us", "-s", "145", "--stdin", "-w", str(output))
    say = which("say")
    if say:
        return (say, "-v", "Samantha", "--file-format=WAVE", "--data-format=LEI16@48000",
                "-o", str(output), "-f", "-")
    raise SpeechError("Install eSpeak NG (Linux) or run on macOS with say.")


async def synthesize(text: str, timeout: float = SYNTH_TIMEOUT_SECONDS, which: Which = shutil.which) -> bytes:
    """Return a mono 16-bit WAV for the prompt."""
    prompt = clean_prompt(text).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="parts-desk-speech-") as folder:
        os.chmod(folder, 0o700)
        output = Path(folder) / "prompt.wav"
        process = await asyncio.create_subprocess_exec(
            *engine_command(output, which), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.communicate(prompt), timeout)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise SpeechError("Speech synthesis timed out.") from error
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode or not output.exists():
            raise SpeechError("Speech synthesis failed.")
        return output.read_bytes()
