"""Migrate an existing database to the v1 evidence contract.

Additive and reversible by design: it only ADDs columns and CREATEs tables,
never drops or rewrites a row's meaning. Existing `media_items.component_tag`
values are copied into the new `client_hint` column, which is what they
always actually were — the client's request. They are deliberately NOT
promoted into `observed_views`, because no one ever verified that those
photos showed what was requested. Old sessions therefore lose coverage they
were never entitled to; that is the point of the migration.

Usage (from backend/, venv active):
    python scripts/migrate_evidence_contract.py --dry-run
    python scripts/migrate_evidence_contract.py
    python scripts/migrate_evidence_contract.py --database-url sqlite:///./other.db

Always takes a timestamped backup of a SQLite file before writing.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# (table, column, DDL type, default expression or None)
ADDED_COLUMNS: list[tuple[str, str, str, str | None]] = [
    ("sessions", "evidence_revision", "INTEGER", "1"),
    ("media_items", "client_hint", "VARCHAR", None),
    ("media_items", "capture_origin", "VARCHAR", "'unknown'"),
    ("media_items", "image_sha256", "VARCHAR", None),
    ("evidence_records", "canonical_value", "VARCHAR", None),
    ("evidence_records", "display_value", "VARCHAR", None),
    ("evidence_records", "run_id", "VARCHAR", None),
    ("evidence_records", "state", "VARCHAR", "'active'"),
    ("evidence_records", "superseded_by_id", "VARCHAR", None),
    ("evidence_records", "state_reason", "VARCHAR", None),
    ("evidence_records", "basis", "JSON", "'{}'"),
    ("appraisals", "evidence_revision", "INTEGER", "0"),
    ("inference_runs", "limitations", "JSON", "'[]'"),
]

# Visual evidence written by the pre-contract pipeline was never checked for
# readable support (the audit reproduced unsupported mileage, badge-promoted
# model families and "unknown" categories stored as observations). It is
# demoted — never deleted — so it stops unlocking prices. A record is
# pre-contract when it is photo-derived and carries no InferenceRun id.
# Reverse with:  UPDATE evidence_records SET state='active', state_reason=NULL
#                WHERE state='legacy_unverified';
LEGACY_DEMOTION = (
    "UPDATE evidence_records "
    "SET state = 'legacy_unverified', "
    "    state_reason = 'pre-contract visual evidence; not admitted under v1 rules — reanalyse the photo' "
    "WHERE provenance IN ('observed_from_photo', 'inferred_candidate') "
    "  AND run_id IS NULL "
    "  AND (state IS NULL OR state = 'active')"
)

NEW_TABLES: dict[str, str] = {
    "observed_views": """
        CREATE TABLE observed_views (
            id VARCHAR NOT NULL PRIMARY KEY,
            session_id VARCHAR NOT NULL REFERENCES sessions(id),
            media_id VARCHAR NOT NULL REFERENCES media_items(id),
            run_id VARCHAR REFERENCES inference_runs(id),
            view VARCHAR NOT NULL,
            visibility VARCHAR NOT NULL,
            usable BOOLEAN DEFAULT 0,
            limitation VARCHAR,
            state VARCHAR DEFAULT 'active',
            created_at DATETIME
        )
    """,
    "inference_runs": """
        CREATE TABLE inference_runs (
            id VARCHAR NOT NULL PRIMARY KEY,
            session_id VARCHAR NOT NULL REFERENCES sessions(id),
            media_id VARCHAR NOT NULL REFERENCES media_items(id),
            model_id VARCHAR NOT NULL,
            prompt_version VARCHAR NOT NULL,
            schema_version VARCHAR NOT NULL,
            image_sha256 VARCHAR,
            raw_proposal VARCHAR,
            admitted JSON,
            rejected JSON,
            usage JSON,
            limitations JSON,
            latency_ms INTEGER,
            failure_type VARCHAR,
            created_at DATETIME
        )
    """,
    "concerns": """
        CREATE TABLE concerns (
            id VARCHAR NOT NULL PRIMARY KEY,
            session_id VARCHAR NOT NULL REFERENCES sessions(id),
            media_id VARCHAR REFERENCES media_items(id),
            component VARCHAR NOT NULL,
            severity VARCHAR DEFAULT 'info',
            description VARCHAR NOT NULL,
            status VARCHAR DEFAULT 'open',
            resolved_by_media_id VARCHAR,
            created_at DATETIME
        )
    """,
}


def sqlite_path_from_url(url: str) -> Path | None:
    prefix = "sqlite:///"
    return Path(url[len(prefix) :]) if url.startswith(prefix) else None


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def migrate(db_path: Path, dry_run: bool) -> int:
    if not db_path.exists():
        print(f"No database at {db_path}; nothing to migrate (a fresh one is created by the app).")
        return 0

    if not dry_run:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.name}.pre-contract-{stamp}")
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    conn = sqlite3.connect(db_path)
    changes = 0
    try:
        for table, column, coltype, default in ADDED_COLUMNS:
            if not table_exists(conn, table):
                print(f"  skip {table}.{column} (table absent)")
                continue
            if column in existing_columns(conn, table):
                print(f"  ok   {table}.{column} already present")
                continue
            ddl = f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"
            if default is not None:
                ddl += f" DEFAULT {default}"
            print(f"  ADD  {ddl}")
            changes += 1
            if not dry_run:
                conn.execute(ddl)

        for name, ddl in NEW_TABLES.items():
            if table_exists(conn, name):
                print(f"  ok   table {name} already present")
                continue
            print(f"  ADD  table {name}")
            changes += 1
            if not dry_run:
                conn.execute(ddl)

        # Legacy request metadata moves to the field that honestly names it.
        # component_tag is left in place so the change stays reversible.
        # Previews are computed against the TARGET schema, so a dry run on a
        # pre-contract database still shows these data-changing steps.
        if table_exists(conn, "media_items") and "component_tag" in existing_columns(conn, "media_items"):
            has_hint = "client_hint" in existing_columns(conn, "media_items")
            pending_hints = conn.execute(
                "SELECT COUNT(*) FROM media_items WHERE component_tag IS NOT NULL"
                + (" AND client_hint IS NULL" if has_hint else "")
            ).fetchone()[0]
            backfill = (
                "UPDATE media_items SET client_hint = component_tag "
                "WHERE client_hint IS NULL AND component_tag IS NOT NULL"
            )
            print(f"  RUN  {backfill}  ({pending_hints} row(s))")
            changes += 1
            if not dry_run:
                cursor = conn.execute(backfill)
                print(f"       {cursor.rowcount} legacy hint(s) preserved as client_hint")

        if table_exists(conn, "evidence_records"):
            cols = existing_columns(conn, "evidence_records")
            if "run_id" in cols:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM evidence_records "
                    "WHERE provenance IN ('observed_from_photo', 'inferred_candidate') "
                    "AND run_id IS NULL AND (state IS NULL OR state = 'active')"
                ).fetchone()[0]
            else:
                # Pre-contract table: every visual row lacks a run id by definition.
                pending = conn.execute(
                    "SELECT COUNT(*) FROM evidence_records "
                    "WHERE provenance IN ('observed_from_photo', 'inferred_candidate')"
                ).fetchone()[0]
            if pending:
                print(f"  RUN  demote {pending} pre-contract visual evidence row(s) to legacy_unverified")
                changes += 1
                if not dry_run:
                    conn.execute(LEGACY_DEMOTION)
            else:
                print("  ok   no pre-contract visual evidence left to demote")

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    print(f"\n{'Would apply' if dry_run else 'Applied'} {changes} change(s).")
    if dry_run:
        print("Dry run only — nothing written.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    url = args.database_url
    if url is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.config import get_settings

        url = get_settings().database_url

    db_path = sqlite_path_from_url(url)
    if db_path is None:
        print(f"Only SQLite URLs are supported by this script; got {url!r}")
        return 2

    print(f"Database: {db_path}")
    return migrate(db_path, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
