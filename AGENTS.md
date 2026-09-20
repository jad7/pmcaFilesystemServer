# Project Notes for Agents

## Target Device

- This application runs on Sony Alpha cameras through the PlayMemories/OpenMemories environment.
- The known target is Sony Alpha 6000.
- Treat the runtime as an old Android/PMCA environment, not as a desktop Java 8 runtime.
- Keep implementation compatible with the current project baseline: `minSdkVersion 10`, `compileSdkVersion 23`, Android Gradle Plugin 3.3.2.
- Prefer `java.io.File`, basic `java.util`, and simple Java syntax.
- Avoid relying on Java 8 runtime APIs such as `java.nio.file`, streams, `java.time`, or APIs that require desugaring.

## Current Server

- `HttpServer` extends NanoHTTPD `SimpleWebServer`.
- The existing app serves a human HTML root page and then delegates other paths to the file server.
- `CameraFileIndex` now owns the singleton cursor session for the machine API.
- `FilesystemScanner` remains only as a compatibility helper for the HTML root page.
- The HTML view still scans separately for videos, JPEGs, and RAWs, but that path is manual only.

## Product Goal

Build a better camera-side HTTP server for automatic photo synchronization.

Primary flow:

1. User returns from a photo session.
2. User turns on the camera and starts this app.
3. A local photo server discovers or is configured with the camera URL.
4. The local server asks the camera for cheap metadata pages.
5. The local server compares camera metadata with its own library state.
6. The local server downloads only missing files.
7. The local server imports downloaded files into Immich or another library.

The camera should stay dumb and lightweight. The local server owns sync state, deduplication, retry policy, and Immich integration.

## API Design Constraints

- SD cards may contain more than 10,000 files.
- Most files are already synchronized.
- Do not return the full file inventory as one large response.
- Do not require holding a long-lived full inventory on disk.
- Prefer one bounded cursor session, simple metadata, and resumable download.
- Keep cursor state singleton and explicit.
- Cursor payloads may be simple on the wire if the contract stays stable.
- Avoid expensive checksums during listing. Path, mtime, size, and extension are cheap. Content hashes should be optional and requested only for a small selected set if ever needed.
- Prefer a text line protocol over JSON. It is cheaper to generate on the camera and cheaper to parse incrementally on the Python side.

## Recommended Mini Protocol

Use a versioned machine API under `/api/v1`.

Core endpoints:

- `GET /api/v1/hello.txt`
- `POST /api/v1/cursor/create.txt`
- `GET /api/v1/cursor/status.txt`
- `GET /api/v1/cursor/files.txt`
- `POST /api/v1/cursor/close.txt`
- `GET /api/v1/ui-status.txt`
- `GET /api/v1/file.txt`
- `GET /api/v1/download`

`GET /api/v1/hello.txt` returns device and protocol capabilities as `text/plain`:

```text
protocol,pmca-sync,1
device,Sony,ILCE-6000
limits,100,200
capabilities,text,cursor,status,singleton
```

`POST /api/v1/cursor/create.txt` creates the single active cursor session.

Supported query parameters:

- `modified_after`: epoch milliseconds lower bound.
- `prefix`: path prefix under the camera storage root.
- `kind`: `all`, `image`, `raw`, `video`, or `other`.
- `force`: if truthy, close the active session first.

If `kind` is omitted, the default cursor contents are media only:
`image`, `raw`, and `video`. `other` is excluded unless explicitly requested.
If `kind=all`, the cursor should include `other` as well.

`GET /api/v1/cursor/status.txt` reports current cursor state.

`GET /api/v1/cursor/files.txt` returns bounded metadata pages as `text/plain`.

Supported query parameters:

- `limit`: requested page size, capped by the server.

Response shape:

```text
# pmca-sync cursor v=1 cursor=1 status=ready limit=100 count=1 has_more=1 matched=1 scanned=42 emitted=1 remaining=0 format=tsv fields=path,mtime,size,kind encoding=backslash
/storage/sdcard0/DCIM/100MSDCF/DSC01234.ARW	1719850000123	24891234	raw
```

Only `path` is the identity. Do not include a separate `id`; it is redundant. Do not include `name`; the client can derive it from `path`. Use tab-separated records with backslash escaping for `\\`, tab, CR, and LF so the path stays readable and still parses safely.

`size` should stay in the base record because `File.length()` is cheap and lets the Python client validate downloads. It is not a content hash.

`kind` is optional but useful and cheap when derived from extension. It should never require reading file contents.

`GET /api/v1/download?path=...` streams one file. Range support is a later enhancement if real camera testing needs it.

`GET /api/v1/file.txt?path=...` returns one metadata line for one file. This is useful for revalidation before download.

`POST /api/v1/cursor/close.txt` closes the active session and frees its memory.

`GET /api/v1/ui-status.txt` returns the current human-readable status shown on
the camera screen. The Python sync client may also post a `message=...` field
here to surface sync progress back on the camera.

`GET /api/v1/status.txt` may remain as an alias to cursor status for older clients.

The `matched` counter is current while scanning and final once the cursor is
ready, closed, expired, or errored.

## Cursor Strategy

The first implementation should use one active bounded cursor session.

Recommended behavior:

- `create` starts one background scan for the chosen filter set.
- The cursor keeps compact arrays of `path`, `mtime`, `size`, and `kind`.
- `files` pages from that session until exhausted.
- `close` cancels and releases the session.
- `force=1` replaces a stale session.

Do not add sorting or offset pagination to the MVP. The local server should own
sync ordering, deduplication, and retry logic.

## Local Sync Tooling

- `tools/sony_a6000_sync.py` is the long-running local Immich sync client.
- `make sony-a6000-sync-bundle` builds `build/sony-a6000-sync.pyz` so the
  Linux host can copy one runnable file instead of a directory tree.
- The runtime config lives in `config/sony_a6000_sync.json`, copied from
  `config/sony_a6000_sync.example.json`.
- Keep the Immich API key in that JSON config, not in source control.
  `config/sony_a6000_sync.json` is gitignored for that reason.
- Do not require a `prefix` or `camera_strip_prefix` in the local config.
  The Python sync client should consume the absolute camera paths it receives
  and derive safe local paths itself.
- Store the local per-file queue in SQLite and process small sequential batches.
  The default batch size is 20 files.
- Upload each flat batch directly to the Immich library without album creation.
  Do not group staging by year.
- Delete a staged file only after its Immich upload succeeds and SQLite records
  the imported status. Retry cleanup after restart; cleanup failure must not
  trigger a second import.
- The monitor loop should stay low-resource: poll `hello.txt`, sleep when the
  camera is absent, and only start a sync cycle when the camera is reachable.
- After a successful sync cycle, wait for the camera to disappear before
  allowing the next cycle.
- Migrate the old JSON timestamp into SQLite on the first new-client startup;
  thereafter SQLite is the only writable sync state.
- Import downloaded batches before downloading the next batch. Recover local
  downloaded files and cleanup before waiting for the camera.
- Fetch and validate all metadata pages on the Linux host, then close the cursor
  before downloading. Downloads may outlast the camera's ten-minute cursor TTL.
- Do not retry cursor page requests: each successful request advances the server
  offset even if the client loses the response.
- Download requests may include `index` and `total` for the camera display.
  `TransferInputStream` reports locally throttled bytes/s without extra HTTP
  progress polling. Client logs report received bytes and every completed file.
- Keep network lifecycle messages separate from transfer status. A temporary
  activity pause must not disable Wi-Fi; start/stop resources with onStart/onStop.
- Verify client transfer behavior with `make test-sync` and Java stream progress
  with `make test-transfer`.

## Scanner Direction

Keep `FilesystemScanner` as a compatibility helper for the HTML page only.

- one traversal implementation is enough for the HTML page
- no Java 8-only filesystem APIs
- prefer `java.io.File`, `ArrayDeque`, and simple lists

## Python Client Responsibilities

The Python/local server should:

- store sync state locally
- call `/api/v1/hello.txt`
- create a cursor for the relevant sync window
- page through cursor files until complete
- compare by path, size, and mtime
- download missing or changed files
- retry failed downloads
- import downloaded files into Immich
- keep its own database of imported camera files

The camera should not know about Immich.
