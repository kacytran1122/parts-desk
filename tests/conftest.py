from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from partsdesk.rules import GE_FOOTER_RULE
from partsdesk.service import InspectionService
from partsdesk.storage.evidence import EvidenceStore

FIXTURES = Path(__file__).resolve().parents[1] / "src/partsdesk/static/fixtures"


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fixture_bytes() -> Callable[[str], bytes]:
    return lambda name: (FIXTURES / f"{name}.png").read_bytes()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> InspectionService:
    return InspectionService(
        db_path=tmp_path / "parts-desk.sqlite3",
        evidence=EvidenceStore(tmp_path / "evidence"),
        rule=GE_FOOTER_RULE,
        clock=clock,
    )
