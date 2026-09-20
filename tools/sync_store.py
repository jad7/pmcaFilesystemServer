"""Small SQLite queue for the Sony camera sync client."""

import json
import fcntl
import sqlite3
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 1


class SyncLock(object):
    def __init__(self, database_path):
        self.path = Path(str(database_path) + ".lock")
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("sync is already running: %s" % exc)

    def release(self):
        if self.handle is None:
            return
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


@dataclass
class FileRecord(object):
    id: int
    path: str
    mtime_ms: int
    size_bytes: int
    kind: str
    status: str
    local_name: str
    last_error: str


class SyncStore(object):
    def __init__(self, path, camera_id, baseline_mtime_ms):
        self.path = Path(path)
        self.camera_id = camera_id
        self.baseline_mtime_ms = int(baseline_mtime_ms)
        self.existed_before_open = self.path.exists() and self.path.stat().st_size > 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = DELETE")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self):
        with self.connection:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(
                    "unsupported SQLite schema version %s" % version
                )
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    camera_id TEXT NOT NULL,
                    completed_mtime_ms INTEGER NOT NULL,
                    inventory_max_mtime_ms INTEGER,
                    inventory_total INTEGER NOT NULL DEFAULT 0,
                    inventory_remaining INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    mtime_ms INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'downloaded', 'imported', 'cleaned')),
                    local_name TEXT UNIQUE,
                    last_error TEXT,
                    UNIQUE (path, mtime_ms, size_bytes)
                );

                CREATE INDEX IF NOT EXISTS files_by_status ON files(status, id);
                PRAGMA user_version = 1;
                """
            )
            row = self.connection.execute(
                "SELECT camera_id FROM sync_state WHERE id = 1"
            ).fetchone()
            if row is None:
                if self.existed_before_open:
                    raise RuntimeError("SQLite database is missing sync_state")
                self.connection.execute(
                    """
                    INSERT INTO sync_state
                        (id, camera_id, completed_mtime_ms, inventory_max_mtime_ms,
                         inventory_total, inventory_remaining)
                    VALUES (1, ?, ?, NULL, 0, 0)
                    """,
                    (self.camera_id, self.baseline_mtime_ms),
                )
            elif row["camera_id"] != self.camera_id:
                raise RuntimeError(
                    "SQLite database belongs to camera %s, expected %s"
                    % (row["camera_id"], self.camera_id)
                )

    def close(self):
        self.connection.close()

    def state(self):
        return self.connection.execute(
            "SELECT * FROM sync_state WHERE id = 1"
        ).fetchone()

    def has_inventory(self):
        return self.state()["inventory_max_mtime_ms"] is not None

    def completed_mtime_ms(self):
        return int(self.state()["completed_mtime_ms"])

    def inventory_progress(self):
        row = self.state()
        return int(row["inventory_total"]), int(row["inventory_remaining"])

    def start_inventory(self, records):
        records = list(records)
        max_mtime_ms = max((record.mtime_ms for record in records), default=None)
        with self.connection:
            for record in records:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO files
                        (path, mtime_ms, size_bytes, kind, status)
                    VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (record.path, record.mtime_ms, record.size_bytes, record.kind),
                )
            remaining = self.connection.execute(
                """
                SELECT COUNT(*) AS count FROM files
                WHERE status IN ('pending', 'downloaded')
                """
            ).fetchone()["count"]
            self.connection.execute(
                """
                UPDATE sync_state
                SET inventory_max_mtime_ms = ?, inventory_total = ?,
                    inventory_remaining = ?
                WHERE id = 1
                """,
                (max_mtime_ms, len(records), remaining if records else 0),
            )

    def pending_files(self, limit):
        rows = self.connection.execute(
            """
            SELECT id, path, mtime_ms, size_bytes, kind, status, local_name, last_error
            FROM files
            WHERE status = 'pending'
            ORDER BY (local_name IS NULL), mtime_ms, path, id
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [self._file_record(row) for row in rows]

    def downloaded_groups(self):
        rows = self.connection.execute(
            """
            SELECT id, path, mtime_ms, size_bytes, kind, status, local_name, last_error
            FROM files
            WHERE status = 'downloaded' AND local_name IS NOT NULL
            ORDER BY local_name
            """
        ).fetchall()
        groups = {}
        for row in rows:
            record = self._file_record(row)
            group = str(Path(record.local_name).parent)
            groups.setdefault(group, []).append(record)
        return groups

    def imported_files(self):
        rows = self.connection.execute(
            """
            SELECT id, path, mtime_ms, size_bytes, kind, status, local_name, last_error
            FROM files
            WHERE status = 'imported' AND local_name IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
        return [self._file_record(row) for row in rows]

    def mark_downloaded(self, file_id, local_name):
        with self.connection:
            self.connection.execute(
                """
                UPDATE files
                SET status = 'downloaded', local_name = ?, last_error = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (local_name, int(file_id)),
            )

    def assign_local_name(self, file_id, local_name):
        with self.connection:
            self.connection.execute(
                """
                UPDATE files SET local_name = ?
                WHERE id = ? AND status = 'pending' AND local_name IS NULL
                """,
                (local_name, int(file_id)),
            )

    def mark_imported(self, file_ids):
        file_ids = [int(file_id) for file_id in file_ids]
        if not file_ids:
            return
        with self.connection:
            placeholders = ",".join("?" for _ in file_ids)
            self.connection.execute(
                """
                UPDATE files
                SET status = 'imported', last_error = NULL
                WHERE status = 'downloaded' AND id IN (%s)
                """ % placeholders,
                file_ids,
            )
            self.connection.execute(
                """
                UPDATE sync_state
                SET inventory_remaining = MAX(0, inventory_remaining - ?)
                WHERE id = 1 AND inventory_max_mtime_ms IS NOT NULL
                """,
                (len(file_ids),),
            )

    def mark_cleaned(self, file_id):
        with self.connection:
            self.connection.execute(
                """
                UPDATE files SET status = 'cleaned', last_error = NULL
                WHERE id = ? AND status = 'imported'
                """,
                (int(file_id),),
            )

    def record_error(self, file_id, message):
        with self.connection:
            self.connection.execute(
                "UPDATE files SET last_error = ? WHERE id = ?",
                (str(message)[:1000], int(file_id)),
            )

    def finish_inventory_if_ready(self):
        with self.connection:
            state = self.state()
            if state["inventory_max_mtime_ms"] is None:
                return False
            unfinished = self.connection.execute(
                "SELECT COUNT(*) AS count FROM files WHERE status IN ('pending', 'downloaded')"
            ).fetchone()["count"]
            if unfinished:
                return False
            completed = max(
                int(state["completed_mtime_ms"]),
                int(state["inventory_max_mtime_ms"]),
            )
            self.connection.execute(
                """
                UPDATE sync_state
                SET completed_mtime_ms = ?, inventory_max_mtime_ms = NULL,
                    inventory_total = 0, inventory_remaining = 0
                WHERE id = 1
                """,
                (completed,),
            )
            return True

    def counts(self):
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM files GROUP BY status"
        ).fetchall()
        result = {"pending": 0, "downloaded": 0, "imported": 0, "cleaned": 0}
        for row in rows:
            result[row["status"]] = int(row["count"])
        return result

    def _file_record(self, row):
        return FileRecord(
            id=int(row["id"]),
            path=row["path"],
            mtime_ms=int(row["mtime_ms"]),
            size_bytes=int(row["size_bytes"]),
            kind=row["kind"],
            status=row["status"],
            local_name=row["local_name"],
            last_error=row["last_error"],
        )


def legacy_baseline_mtime_ms(state_file, initial_last_synced):
    state_path = Path(state_file)
    if state_path.exists():
        with state_path.open("r") as handle:
            state = json.load(handle)
        if "last_imported_mtime_ms" in state:
            return int(state["last_imported_mtime_ms"])
        if "last_synced" in state:
            from datetime import datetime

            value = datetime.fromisoformat(state["last_synced"])
            if value.tzinfo is None:
                raise RuntimeError("legacy state last_synced has no timezone")
            return int(value.timestamp() * 1000)
        raise RuntimeError("legacy state is missing last_synced")
    if not initial_last_synced:
        raise RuntimeError("state file is missing and initial_last_synced is empty")
    from datetime import datetime

    value = datetime.fromisoformat(initial_last_synced)
    if value.tzinfo is None:
        raise RuntimeError("initial_last_synced has no timezone")
    return int(value.timestamp() * 1000)
