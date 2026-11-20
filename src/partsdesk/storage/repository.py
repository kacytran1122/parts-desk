"""Row mapping and queries. Every function runs inside a caller-owned transaction."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace

from partsdesk.domain import (CallStatus, Capture, CaptureKind, CaptureSource, CaptureStatus, Check, Event,
                              Finding, Identifiers, Inspection, Result, RuleRevision, Stage, Visibility,
                              frozen_map)


class NotFound(LookupError):
    pass


@dataclass(frozen=True)
class PhotoRequest:
    inspection_id: str
    kind: CaptureKind
    armed_at: float
    expires_at: float
    token: str


def _optional(enum: type, value: str | None):  # noqa: ANN202 - small enum helper
    return enum(value) if value is not None else None


def _inspection(row: sqlite3.Row) -> Inspection:
    return Inspection(row["id"], row["training_case"], Stage(row["stage"]), row["operator"], row["created_at"],
                      row["updated_at"], row["active_capture_id"], CallStatus(row["call_status"]),
                      row["call_id"], row["room_id"], row["last_seq"])


def _capture(row: sqlite3.Row) -> Capture:
    return Capture(
        id=row["id"], inspection_id=row["inspection_id"], kind=CaptureKind(row["kind"]),
        source=CaptureSource(row["source"]), status=CaptureStatus(row["status"]), captured_at=row["captured_at"],
        raw=Identifiers.from_dict(json.loads(row["raw"])),
        confirmed=Identifiers.from_dict(json.loads(row["confirmed"]) if row["confirmed"] else None),
        confirmed_at=row["confirmed_at"], visibility=_optional(Visibility, row["visibility"]),
        reason=row["reason"], observations=tuple(json.loads(row["observations"])),
        original_sha256=row["original_sha256"], crop_sha256=row["crop_sha256"],
        original_ref=row["original_ref"], crop_ref=row["crop_ref"],
    )


def insert_inspection(db: sqlite3.Connection, item: Inspection, rule: RuleRevision) -> None:
    db.execute("INSERT INTO inspections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (item.id, item.training_case, item.stage, item.operator, item.created_at, item.updated_at,
                item.active_capture_id, item.call_status, item.call_id, item.room_id, item.last_seq,
                rule.id, rule.version))


def get_inspection(db: sqlite3.Connection, inspection_id: str) -> Inspection:
    row = db.execute("SELECT * FROM inspections WHERE id=?", (inspection_id,)).fetchone()
    if row is None:
        raise NotFound("Inspection was not found.")
    return _inspection(row)


def update_inspection(db: sqlite3.Connection, item: Inspection) -> None:
    db.execute("UPDATE inspections SET stage=?, updated_at=?, active_capture_id=?, call_status=?, call_id=?, "
               "room_id=?, last_seq=? WHERE id=?",
               (item.stage, item.updated_at, item.active_capture_id, item.call_status, item.call_id,
                item.room_id, item.last_seq, item.id))


def append_event(db: sqlite3.Connection, item: Inspection, kind: str, detail: str, at: float,
                 from_stage: Stage | None = None, to_stage: Stage | None = None) -> Inspection:
    """Record the next monotonic event and return the inspection with its new sequence."""
    updated = replace(item, last_seq=item.last_seq + 1, updated_at=at)
    db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
               (item.id, updated.last_seq, at, kind, from_stage, to_stage, detail))
    update_inspection(db, updated)
    return updated


def list_events(db: sqlite3.Connection, inspection_id: str, after: int = 0) -> tuple[Event, ...]:
    rows = db.execute("SELECT * FROM events WHERE inspection_id=? AND seq>? ORDER BY seq", (inspection_id, after))
    return tuple(Event(row["inspection_id"], row["seq"], row["at"], row["type"],
                       _optional(Stage, row["from_stage"]), _optional(Stage, row["to_stage"]), row["detail"])
                 for row in rows)


def _capture_params(item: Capture) -> tuple:
    return (item.inspection_id, item.kind, item.source, item.status, item.captured_at,
            json.dumps(item.raw.to_dict()), json.dumps(item.confirmed.to_dict()) if item.confirmed else None,
            item.confirmed_at, item.visibility, item.reason, json.dumps(list(item.observations)),
            item.original_sha256, item.crop_sha256, item.original_ref, item.crop_ref)


def insert_capture(db: sqlite3.Connection, item: Capture) -> None:
    db.execute("INSERT INTO captures(inspection_id, kind, source, status, captured_at, raw, confirmed, "
               "confirmed_at, visibility, reason, observations, original_sha256, crop_sha256, original_ref, "
               "crop_ref, id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*_capture_params(item), item.id))


def update_capture(db: sqlite3.Connection, item: Capture) -> None:
    db.execute("UPDATE captures SET inspection_id=?, kind=?, source=?, status=?, captured_at=?, raw=?, "
               "confirmed=?, confirmed_at=?, visibility=?, reason=?, observations=?, original_sha256=?, "
               "crop_sha256=?, original_ref=?, crop_ref=? WHERE id=?", (*_capture_params(item), item.id))


def get_capture(db: sqlite3.Connection, inspection_id: str, capture_id: str) -> Capture:
    row = db.execute("SELECT * FROM captures WHERE inspection_id=? AND id=?", (inspection_id, capture_id)).fetchone()
    if row is None:
        raise NotFound("Capture was not found.")
    return _capture(row)


def list_captures(db: sqlite3.Connection, inspection_id: str) -> tuple[Capture, ...]:
    rows = db.execute("SELECT * FROM captures WHERE inspection_id=? ORDER BY rowid", (inspection_id,))
    return tuple(_capture(row) for row in rows)


def get_rule(db: sqlite3.Connection, inspection_id: str) -> RuleRevision:
    row = db.execute("SELECT r.* FROM rule_revisions r JOIN inspections i ON r.id=i.rule_id AND "
                     "r.version=i.rule_version WHERE i.id=?", (inspection_id,)).fetchone()
    if row is None:
        raise NotFound("Rule revision was not found.")
    return RuleRevision(row["id"], row["version"], row["notice_id"], row["source_url"], row["scope"],
                        frozen_map(json.loads(row["predicates"])))


def replace_findings(db: sqlite3.Connection, inspection_id: str, findings: tuple[Finding, ...], at: float) -> None:
    db.execute("DELETE FROM findings WHERE inspection_id=?", (inspection_id,))
    db.executemany("INSERT INTO findings VALUES(?,?,?,?,?,?,?)", [
        (inspection_id, item.check, item.result, json.dumps(list(item.capture_ids)),
         json.dumps(dict(item.values), sort_keys=True), item.rule, at) for item in findings])


def list_findings(db: sqlite3.Connection, inspection_id: str) -> tuple[Finding, ...]:
    rows = db.execute("SELECT * FROM findings WHERE inspection_id=? ORDER BY check_name", (inspection_id,))
    order = list(Check)
    items = [Finding(Check(row["check_name"]), Result(row["result"]), tuple(json.loads(row["capture_ids"])),
                     frozen_map(json.loads(row["evaluated"])), row["rule"]) for row in rows]
    return tuple(sorted(items, key=lambda item: order.index(item.check)))


def arm_photo_request(db: sqlite3.Connection, request: PhotoRequest) -> None:
    db.execute("INSERT OR REPLACE INTO photo_requests VALUES(1,?,?,?,?,?)",
               (request.inspection_id, request.kind, request.armed_at, request.expires_at, request.token))


def active_photo_request(db: sqlite3.Connection) -> PhotoRequest | None:
    row = db.execute("SELECT * FROM photo_requests WHERE slot=1").fetchone()
    if row is None:
        return None
    return PhotoRequest(row["inspection_id"], CaptureKind(row["kind"]), row["armed_at"], row["expires_at"],
                        row["token"])


def clear_photo_request(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM photo_requests")


def seen_inbound(db: sqlite3.Connection, event_id: str) -> bool:
    return db.execute("SELECT 1 FROM inbound_events WHERE event_id=?", (event_id,)).fetchone() is not None


def record_inbound(db: sqlite3.Connection, event_id: str, at: float) -> None:
    db.execute("INSERT INTO inbound_events VALUES(?,?)", (event_id, at))
