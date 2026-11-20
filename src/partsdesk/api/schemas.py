"""Request bodies. Field constraints reject anything outside the training workflow."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from partsdesk.domain import CaptureKind


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewInspection(Strict):
    training_case: Literal["judging", "rehearsal"] = "judging"


class FixtureRequest(Strict):
    variant: Literal["tag", "missing", "clean", "cropped", "mismatch", "adversarial"]

    @property
    def kind(self) -> CaptureKind:
        return CaptureKind.TAG if self.variant == "tag" else CaptureKind.CERTIFICATE


class Confirmation(Strict):
    part: str | None = Field(None, max_length=24, pattern=r"^[A-Za-z0-9-]+$")
    serial: str | None = Field(None, max_length=18, pattern=r"^[A-Za-z0-9]+$")


class PhotoKind(Strict):
    kind: CaptureKind
