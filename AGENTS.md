# Project Notes for Agents

## Language

- Talk to the user in Ukrainian.
- The user reads code and documentation in English.
- Do not use Russian.

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
- `FilesystemScanner` currently performs recursive `File.listFiles()` scans from external storage.
- The current scanner scans separately for videos, JPEGs, and RAWs, which means repeated full tree walks.

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
- Do not require holding the full inventory in memory to serve one request.
- Prefer bounded pages, simple metadata, and resumable download.
- Cursor pagination must be stable enough for incremental sync.
- Cursor tokens should be opaque to clients even if the first implementation encodes simple fields.
- Avoid expensive checksums during listing. Path, mtime, size, and extension are cheap. Content hashes should be optional and requested only for a small selected set if ever needed.
- Prefer a text line protocol over JSON. It is cheaper to generate on the camera and cheaper to parse incrementally on the Python side.

## Recommended Mini Protocol

Use a versioned machine API under `/api/v1`.

Core endpoints:

- `GET /api/v1/hello.txt`
- `GET /api/v1/files.txt`
- `GET /api/v1/file.txt`
- `GET /api/v1/status.txt`
- `GET /api/v1/download`

`GET /api/v1/hello.txt` returns device and protocol capabilities as `text/plain`:

```text
protocol,pmca-sync,1
device,Sony,ILCE-6000
limits,100,200
capabilities,text,cursor,status
```

`GET /api/v1/files.txt` returns bounded metadata pages as `text/plain`.

Supported query parameters:

- `limit`: requested page size, capped by the server.
- `cursor`: opaque continuation token from the previous response.
- `kind`: `all`, `image`, `raw`, `video`, or `other`.
- `ext`: extension filter such as `.jpg`, `.arw`, `.mp4`.
- `prefix`: path prefix under the camera storage root.
- `modified_after`: client-known lower bound in epoch milliseconds.
- `order`: start with `mtime_desc`.

Response shape:

```text
# pmca-sync files v=1 limit=100 count=1 has_more=1 next=opaque-token format=tsv fields=path,mtime,size,kind encoding=backslash
/storage/sdcard0/DCIM/100MSDCF/DSC01234.ARW	1719850000123	24891234	raw
```

Only `path` is the identity. Do not include a separate `id`; it is redundant. Do not include `name`; the client can derive it from `path`. Use tab-separated records with backslash escaping for `\\`, tab, CR, and LF so the path stays readable and still parses safely.

`size` should stay in the base record because `File.length()` is cheap and lets the Python client validate downloads. It is not a content hash.

`kind` is optional but useful and cheap when derived from extension. It should never require reading file contents.

`GET /api/v1/download?path=...` streams one file. Range support is a later enhancement if real camera testing needs it.

`GET /api/v1/file.txt?path=...` returns one metadata line for one file. This is useful for revalidation before download.

`GET /api/v1/status.txt` returns coarse camera-side state without forcing a blocking scan:

```text
status,ready
files_indexed,9823
scanning,0
```

For first inventory or full audit, the client can page through `/api/v1/files.txt`. Do not add a separate JSON/NDJSON inventory endpoint unless real hardware proves it is needed.

## Pagination Strategy

First implementation should use a bounded live scan, not a persistent database.

Recommended order:

- Sort by `mtime DESC`, then `path ASC`.
- Cursor contains the last emitted tuple: `mtime`, `path`, plus the active query filters.
- The next page returns files where `(mtime, path)` is after that tuple in the same order.

For the Sony camera environment, a full sorted in-memory list of 10K+ entries may be too expensive. Prefer scanning with a bounded heap of the next page:

- Walk the filesystem once per page.
- Apply cheap filters while walking.
- Keep only the best `limit + 1` entries for the current cursor window.
- Return `limit` entries and use the extra entry to compute `has_more`.

This costs I/O per page but bounds memory. It also keeps the camera-side implementation simple and compatible with old Android APIs.

If performance is not good enough, the second step is a small persistent index stored on external storage or app storage. Do not start with SQLite unless the bounded live scan is proven too slow on the camera.

Normal sync should query recent files first:

- The Python client stores the highest imported `mtime`.
- The next sync calls `/api/v1/files.txt?modified_after=<last_mtime_minus_safety_window>&order=mtime_desc`.
- The client stops once pages are older than the local sync window or all returned files are already known.
- The client remains idempotent because camera-side snapshots are not guaranteed.

## Scanner Direction

Refactor `FilesystemScanner` toward one generic scanner:

- one traversal implementation
- one `FileEntry` metadata model
- one filter object
- one page result object
- no separate full scans for JPEG, RAW, and video

Avoid adding Java 8-only APIs. Use iterative traversal with `File[]` and `ArrayList`/`Stack` or `ArrayDeque` if available on target.

## Python Client Responsibilities

The Python/local server should:

- store sync state locally
- call `/api/v1/hello.txt`
- query recent files first with `modified_after` from the last successful sync
- compare by path, size, and mtime
- download missing or changed files
- retry failed downloads
- import downloaded files into Immich
- keep its own database of imported camera files

The camera should not know about Immich.
