# Simple SQLite Batch Sync Plan

Implementation reference for the SQLite batch sync workflow.

## Goal and Scope

Download 20 files, import them into Immich, record success, delete local copies,
then continue. If the camera disconnects, keep completed downloads and resume
later. One Python process, the existing systemd service, and two SQLite tables
on the Linux host. No Android/API changes.

Batching preserves progress and makes photos appear in Immich earlier. It does
not increase camera Wi-Fi speed. A single large video still needs a full download.

| Required for this release | Not needed now |
| --- | --- |
| Per-file SQLite status | Scan history, batch history, membership tables |
| Small sequential batches | Parallel workers and job queues |
| Keep downloads on failure/restart | HTTP Range resume for partial files |
| Record import before deleting local files | Separate cleanup job or service |
| Retry leftover cleanup on startup | Separate cleanup/status/migration CLI modes |
| One-time migration of the old timestamp | Migration hashes, external markers, audit trail |
| Validate downloads and import success | Mandatory asset-ID ledger and new Immich API integration |
| One running sync process | Multiple cameras/accounts/destinations in one DB |
| Existing Immich upload invocation | Container naming, discovery and lifecycle management |

The current `run_immich_upload()` already launches Immich CLI using `docker run
--rm`. Docker is an existing way to run that command, not a new component of this
plan. Keep that invocation; do not build container orchestration around it.

## SQLite: Two Tables

```sql
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    camera_id TEXT NOT NULL,
    completed_mtime_ms INTEGER NOT NULL,
    inventory_max_mtime_ms INTEGER,
    inventory_total INTEGER NOT NULL DEFAULT 0,
    inventory_remaining INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE files (
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

CREATE INDEX files_by_status ON files(status, id);
```

- `pending`: still needs downloading.
- `downloaded`: complete local file; needs import.
- `imported`: success recorded; local copy may be deleted.
- `cleaned`: local copy deleted; retain this row to avoid downloading it again.

No batch table: a batch is a list of up to `batch_size` downloaded rows selected
by the script. Commit their import statuses together. Cleanup already has a
durable queue: rows with `status = 'imported'`.

`sync_state` keeps the completed timestamp, current inventory maximum, and stable
progress counters for the camera screen. Advance the completed timestamp only
when no pending/downloaded rows remain, then clear the inventory fields. This
prevents a successful batch from skipping older pending files. NULL means no
active inventory; an empty inventory leaves the completed timestamp unchanged.

Use ordinary SQLite transactions and `PRAGMA user_version = 1`. Keep durability
enabled; never hold a transaction across a network call. A small OS file lock
prevents manual `--once` and systemd from processing the same queue simultaneously.

## Processing Loop

1. Retry cleanup of imported files. A missing file counts as cleaned. A deletion
   error stays imported with `last_error`; never reimport because deletion failed.
2. Import downloaded files left by the previous run, even with the camera offline.
3. If the current inventory is finished, advance the completed timestamp to its
   maximum without moving backwards and clear the active inventory field.
4. With no active inventory, fetch and validate all metadata pages using the
   existing client. In one transaction insert unseen file versions and store
   the inventory maximum. Sort the completed metadata on the Linux host by
   `(mtime_ms, path)` before batching; do not add sorting work to the camera.
   Do not reset existing statuses. Close the cursor.
   A failed/incomplete listing must not update SQLite.
5. Download until 20 files are ready or the inventory ends. If the camera
   disconnects after 7 completed downloads, import those 7 first.
6. Run the existing uploader for exactly those files, without creating albums.
7. On success commit imported, delete their local copies, then commit cleaned.
   On import failure leave them downloaded for retry.
8. Continue or wait using the existing monitor polling/retry intervals.

Keep the existing date filter and configured overlap for discovery. SQLite
decides whether a returned file version is already imported. Always process
persisted pending files regardless of the date filter.

## Files and Failure Handling

Stage in a dedicated directory under `temp_root`, using names such as
`42__DSC08090.JPG`. The DB ID prevents collisions. Run the uploader against
the current batch directory only; it contains only that batch's selected
files. Leave old untracked staging directories alone.

Download into `.part`, verify size, flush and atomically rename, then mark
downloaded. On restart reuse a complete final file with the expected size;
retry an unfinished `.part` from zero. Preserve mtime. A missing/changed camera
file remains an explicit error, not an imported file, and blocks advancing the
timestamp. Metadata identity cannot detect changes with identical path/mtime/size.

Remove unconditional staging deletion from the current `finally`. Delete only
recorded imported files inside the managed directory. If the DB cannot record
success, keep files. Cleanup needs no separate job: run it at startup, after
import, and in the existing monitor loop even when the camera is absent.

Keep the synchronous CLI invocation. Before enabling cleanup, test the deployed
CLI with success, duplicate and partial failure. If it can exit zero with skipped
or failed files, check its result summary too. An ambiguous result keeps the batch
on disk. A new asset-ID database or verification API is not part of this release.

If the process crashes after Immich accepts files but before SQLite records it,
retry the downloaded batch. Verify duplicate handling once on the deployed CLI.
An existing Docker upload may outlive a killed Python process; MVP accepts a
possible repeated upload attempt and retains files until a later successful
synchronous attempt. It does not promise exactly-once execution.

## Config and First Startup

Only two new optional settings:

```json
{
  "database_file": "sony-a6000-sync.sqlite3",
  "batch_size": 20
}
```

Default the DB beside the existing `state_file` with a `.sqlite3` suffix. Resolve
an explicitly relative DB path against the config directory. Keep existing config
paths and API key. A batch is limited by count only; add byte/time limits later
if actually needed.

Stop the old service and back up its bundle/config before replacing the bundle.
First startup through the existing `--once` or `--monitor` command:

1. If SQLite is initialized, use it without rereading JSON state.
2. Otherwise read `last_imported_mtime_ms` from JSON, falling back to parsing
   `last_synced`. If no state exists, use `initial_last_synced` from config.
   Invalid state or mismatched camera ID is an error, not a reset to zero.
3. Create the tables, schema version and initial state row in one transaction.
   Set completed mtime from the old value; leave inventory maximum NULL.
4. Leave JSON untouched as a backup; subsequently write only SQLite.
5. Resume normally. JSON has no per-file history, so do not invent imported rows.
   Some overlap files may be downloaded once more and deduplicated by Immich.

No separate migration command or migration marker. Do not rebuild a corrupt or
unsupported DB silently, or let `--init-baseline` reset populated SQLite history.
Changing camera/library and reconciling old history are outside this change.

## Four Developer Tasks

### 1. SQLite and migration

Add `tools/sync_store.py` with the schema, automatic JSON migration and required
queries. Include it in the zipapp. Add the two config settings and process lock.
Keep existing command entry points.

Verify: fresh DB, integer/ISO legacy timestamps, invalid state, repeated startup,
duplicate identity, two simultaneous runs. JSON stays intact; the bundle works
with the server's Python and built-in sqlite3.

### 2. Persistent downloads

Store only a complete validated inventory. Reuse imported rows and complete local
files. Form batches of 20, use stable filenames, and remove deletion from finally.
Keep current download-size checks and progress logging.

Verify: restart after 7 downloads without redownloading them; lost page response
does not save a partial inventory; partial files retry; changed mtime/size creates
a new row; the cutoff never skips queued files.

### 3. Batch import and cleanup

Upload selected files, record imported in one transaction, delete, then mark
cleaned. On startup process downloaded imports and imported cleanup before
waiting for the camera. Reuse existing retry delays; no background jobs.

Verify: first 20 import before file 21 downloads; disconnect flushes a smaller
batch; import failure preserves files; deletion failure does not reimport;
restart between import and deletion finishes cleanup. Test real CLI duplicate
and partial-failure behavior before enabling deletion.

### 4. Bundle and acceptance check

Update example config and README. Build with `make sony-a6000-sync-bundle`.
Test 45 files: batches of 20, 20 and 5. Repeat with disconnect and process restart.
Confirm no redownload of confirmed files, deletion only after success, and the
final timestamp equals the maximum completed file mtime. Deploy with the service
stopped, preserve JSON, then restart it. No camera APK or systemd unit change.
