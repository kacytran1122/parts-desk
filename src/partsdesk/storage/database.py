"""SQLite schema and short IMMEDIATE transactions. One connection per unit of work."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from partsdesk.domain import RuleRevision

BUSY_TIMEOUT_SECONDS = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_revisions(
  id TEXT NOT NULL, version INTEGER NOT NULL, notice_id TEXT NOT NULL, source_url TEXT NOT NULL,
  scope TEXT NOT NULL, predicates TEXT NOT NULL, PRIMARY KEY(id, version));
CREATE TABLE IF NOT EXISTS inspections(
  id TEXT PRIMARY KEY, training_case TEXT NOT NULL, stage TEXT NOT NULL, operator TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, active_capture_id TEXT,
  call_status TEXT NOT NULL, call_id TEXT, room_id TEXT, last_seq INTEGER NOT NULL DEFAULT 0,
  rule_id TEXT NOT NULL, rule_version INTEGER NOT NULL,
  FOREIGN KEY(rule_id, rule_version) REFERENCES rule_revisions(id, version));
CREATE TABLE IF NOT EXISTS captures(
  id TEXT PRIMARY KEY, inspection_id TEXT NOT NULL REFERENCES inspections(id), kind TEXT NOT NULL,
  source TEXT NOT NULL, status TEXT NOT NULL, captured_at REAL NOT NULL, raw TEXT NOT NULL,
  confirmed TEXT, confirmed_at REAL, visibility TEXT, reason TEXT NOT NULL, observations TEXT NOT NULL,
  original_sha256 TEXT NOT NULL, crop_sha256 TEXT, original_ref TEXT, crop_ref TEXT);
CREATE INDEX IF NOT EXISTS captures_by_inspection ON captures(inspection_id, kind);
CREATE TABLE IF NOT EXISTS findings(
  inspection_id TEXT NOT NULL REFERENCES inspections(id), check_name TEXT NOT NULL, result TEXT NOT NULL,
  capture_ids TEXT NOT NULL, evaluated TEXT NOT NULL, rule TEXT, evaluated_at REAL NOT NULL,
  PRIMARY KEY(inspection_id, check_name));
CREATE TABLE IF NOT EXISTS events(
  inspection_id TEXT NOT NULL REFERENCES inspections(id), seq INTEGER NOT NULL, at REAL NOT NULL,
  type TEXT NOT NULL, from_stage TEXT, to_stage TEXT, detail TEXT NOT NULL,
  PRIMARY KEY(inspection_id, seq));
CREATE TABLE IF NOT EXISTS photo_requests(
  slot INTEGER PRIMARY KEY CHECK(slot = 1), inspection_id TEXT NOT NULL REFERENCES inspections(id),
  kind TEXT NOT NULL, armed_at REAL NOT NULL, expires_at REAL NOT NULL, token TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbound_events(event_id TEXT PRIMARY KEY, received_at REAL NOT NULL);
"""


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize(path: Path, rule: RuleRevision) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)  # the database and WAL hold inspection identifiers
    with closing(_connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO rule_revisions VALUES(?,?,?,?,?,?)",
            (rule.id, rule.version, rule.notice_id, rule.source_url, rule.scope,
             json.dumps(dict(rule.predicates), sort_keys=True)),
        )


@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    """Commit everything in the block together, or nothing."""
    with closing(_connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
