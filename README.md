pmcaFilesystemServer
====
[![Build Status](https://travis-ci.org/schnatterer/pmcaFilesystemServer.svg?branch=develop)](https://travis-ci.org/schnatterer/pmcaFilesystemServer)

Simple Android app for Sony Cameras ( PlayMemories Camera App Store) that provides the File System 
of the camera via HTTP.

This app uses the [OpenMemories: Framework](https://github.com/ma1co/OpenMemories-Framework) and is 
greatly inspired by the following existing open source PMCA Apps

* [ma1co/PMCADemo](https://github.com/ma1co/PMCADemo)
* [LubikR/SynologyUploader](https://github.com/LubikR/SynologyUploader)
* [Bostwickenator/STGUploader](https://github.com/Bostwickenator/STGUploader)

# Pre-release of new UI

The PR for the new user interface hase been dangling for years. Now there is a pre-release that can be installed via Sony-PMCA-RE or through adb. See [#4](https://github.com/schnatterer/pmcaFilesystemServer/pull/4).

<img src="https://user-images.githubusercontent.com/1522953/88163092-7608fe80-cc12-11ea-8db7-9d0efad416be.png" height="240px">&nbsp;&nbsp;&nbsp;&nbsp;<img src="https://user-images.githubusercontent.com/1522953/88175996-ef125100-cc26-11ea-83ce-7c54c6b1f269.png" height="240px"> 

# Installation 

* Use [Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE), 
* via [sony-pmca.appspot.com](https://sony-pmca.appspot.com/apps) or 
* through adb (using [tweak app](https://github.com/ma1co/OpenMemories-Tweak)).

# Usage

On Startup a WiFi Connection will be established. Once this succeeds a webserver is started 
and its URL is displayed. There you can download all data from the camera, like images and videos.

This works around the constraint of certain Sony cameras where videos can not be downloaded via WiFi.

<font color="red">⚠</font>  The Web Server exposes the whole file system without authentication to everyone on the same network 
as the camera. Make sure to run this in a private network, using WiFi direct or by using your 
Mobile's Hotspot.

# Development

Copy `.env.template` to `.env` and fill in your local Java 8 and Android SDK
paths before running the Makefile targets. You can also put `ADB_TARGET` and
`CAMERA_BASE_URL` there if you want default live-test values.

```bash
adb tcpip 5555
adb connect 192.168.178.53:5555
```

See https://stackoverflow.com/a/3623727

For live camera work, these are the usual adb commands:

```bash
adb connect ip:port
adb install -t app/build/outputs/apk/releaseSigned/app-releaseSigned.apk
adb shell pm list packages
adb uninstall info.schnatterer.pmcaFilesystemServer
```

The same flow is available through Makefile targets:

```bash
make adb-connect ADB_TARGET=ip:port
make adb-packages
make adb-install-debug
make adb-install-release-signed
make adb-uninstall
make adb-reinstall-debug
make adb-reinstall-release-signed
```

For a live smoke test against the camera API, use:

```bash
make camera-probe CAMERA_BASE_URL=http://192.168.12.220:8080
make camera-probe CAMERA_BASE_URL=http://192.168.12.220:8080 PROBE_ARGS='--hours 24 --kind image --max-pages 3'
make camera-sync CAMERA_BASE_URL=http://192.168.12.220:8080 SYNC_DEST_DIR=/tmp/pmca-sync
make camera-sync CAMERA_BASE_URL=http://192.168.12.220:8080 SYNC_DEST_DIR=/tmp/pmca-sync SYNC_ARGS='--hours 24 --overwrite'
```

The probe script lives in [tools/live_camera_probe.py](/Users/ikrokhmalyov/Documents/pmcaFilesystemServer/tools/live_camera_probe.py)
and uses the reusable HTTP helper in [tools/camera_client.py](/Users/ikrokhmalyov/Documents/pmcaFilesystemServer/tools/camera_client.py).
The downloader lives in [tools/camera_sync.py](/Users/ikrokhmalyov/Documents/pmcaFilesystemServer/tools/camera_sync.py) and mirrors camera files into a local directory.

For the Immich sync scenario, copy the example config and fill the Immich API
key in JSON:

```bash
cp config/sony_a6000_sync.example.json config/sony_a6000_sync.json
make sony-a6000-sync
```

The Immich sync client lives in [tools/sony_a6000_sync.py](/Users/ikrokhmalyov/Documents/pmcaFilesystemServer/tools/sony_a6000_sync.py).
Its config stays in [config/sony_a6000_sync.example.json](/Users/ikrokhmalyov/Documents/pmcaFilesystemServer/config/sony_a6000_sync.example.json) until you copy it to `config/sony_a6000_sync.json`.
By default it syncs media only, meaning images, RAW, and video; set `kind` to `all` in the JSON config if you also want other file types.
The sync client stores its per-file queue in SQLite, downloads a small batch,
uploads that batch directly into the Immich library, and removes local copies
only after a successful upload. The default batch size is 20 files; change
`batch_size` in the JSON config if needed. The config does not need a `prefix`
or `camera_strip_prefix`.
The SQLite database is stored at `database_file`; if omitted, it is placed beside
the legacy state file with a `.sqlite3` suffix.

If you want one file to copy to the Linux box, build the zipapp bundle:

```bash
make sony-a6000-sync-bundle
```

That produces `build/sony-a6000-sync.pyz`. You still keep the JSON config,
SQLite database, and legacy state file outside the bundle. The API key remains
in the ignored JSON config; SQLite becomes the writable sync state after the
first startup.

### Transfer Progress and Diagnostics

The SQLite queue, incremental Immich import, cleanup and first-start migration
are specified in [the batch sync implementation plan](docs/sqlite-batch-sync-plan.md).

The client fetches all metadata pages onto the Linux host and closes the camera
cursor before downloading. This avoids the camera's ten-minute cursor expiry
during large transfers. Page requests are not retried automatically because they
advance the camera cursor; a lost page response aborts the cycle safely. Each
batch is imported before the next batch is downloaded, so a camera disconnect
does not discard completed work.

The camera screen shows the file number, filename, bytes sent, elapsed time and
average MiB/s, refreshed locally at most once per second. Linux logs show bytes
received about every two seconds while data is arriving, and completion for every
file. A blocked read is bounded by `download_timeout_seconds`. Retries currently
restart the affected file; the log includes the attempt number. Slow UI status
requests are logged separately. Camera-side bytes mean bytes supplied to HTTP;
the client's completion message confirms the downloaded size was checked.

Wi-Fi and the HTTP server now run from activity `onStart` to `onStop`, so a
temporary `onPause` does not shut them down. Network events stay in the lower
log area instead of replacing transfer progress. Lifecycle events are written
to the camera log; compare them with disconnects to diagnose real Wi-Fi losses.

Build both updated components with `make apk-release-signed sony-a6000-sync-bundle`.
Run regression tests with `make test-sync test-transfer`. Install the signed APK on the
camera and replace the `.pyz` on the Linux host, then restart its sync service.

For creating a release, set git tag and then upload an *unsigned* APK to GitHub's release page.
Signed APKs seem to be denied by Sony-PMCA-RE.

For local installation on the camera, build the signed variant instead:

```bash
make apk-release-signed
```

The app writes a log file to the SD card: `/storage/sdcard0/pmcaFilesystemServer/LOG.TXT`.

## Icon

Was generated with 
[AndroidAssetStudio](https://romannurik.github.io/AndroidAssetStudio/icons-launcher.html#foreground.type=text&foreground.text.text=HTTP%20FS&foreground.text.font=Allerta%20Stencil&foreground.space.trim=1&foreground.space.pad=0.1&foreColor=rgba(96%2C%20125%2C%20139%2C%200)&backColor=rgb(139%2C%20195%2C%2074)&crop=0&backgroundShape=square&effects=none&name=ic_launcher) 

## Feature Ideas

* QR Code: https://stackoverflow.com/a/8800974/
* Basic Auth: https://github.com/NanoHttpd/nanohttpd/issues/496

# Planned Sync API

This fork is intended to evolve the app from a human file browser into a small
camera-side sync server. The main use case is a local photo server discovering
the camera over WiFi, asking for cheap metadata, downloading only new files, and
then importing those files into a local photo library such as Immich.

The camera-side app must stay small. Sony Alpha cameras that run PlayMemories /
OpenMemories apps should be treated as an old Android-like runtime, not as a
desktop Java 8 environment. Implementation should prefer `java.io.File`, simple
collections, bounded memory usage, and conservative Android APIs.

## Sync Scenario

1. The SD card may contain more than 10,000 files.
2. Most files are already synchronized by the local server.
3. The user takes a new photo session.
4. The user turns on the camera and starts this app.
5. The local server queries the camera for metadata pages.
6. The local server compares metadata with its own sync database.
7. The local server downloads only missing or changed files.
8. The local server imports those files into Immich.

The camera should not own sync state, retry policy, or Immich integration. The
camera only exposes a lightweight read API and streams file contents.

## API Principles

* Do not return the full 10K+ file inventory as one large response.
* Keep every response bounded by a server-side `limit`.
* Use cursor pagination instead of offset pagination.
* Prefer cheap metadata: path, size, modification time, and optional media kind.
* Do not compute content hashes during normal listing.
* Prefer a simple text line protocol over JSON.
* Keep download separate from listing.
* Keep the old HTML page available for manual browsing.

## Proposed Endpoints

All machine endpoints should live under `/api/v1`.
The machine-readable contract lives in [docs/openapi.yaml](/Users/ikrokhmalyov/Documents/pmcaFilesystemServer/docs/openapi.yaml).

### `GET /api/v1/hello.txt`

Returns protocol and device capabilities.

```text
protocol,pmca-sync,1
device,Sony,ILCE-6000
limits,100,200
capabilities,text,cursor,status,singleton
```

### `POST /api/v1/cursor/create.txt?modified_after=...&prefix=...&kind=...&force=1`

Creates the single active cursor session. `force=1` replaces an existing live
session. Supported filters are `modified_after`, `prefix`, `kind`, and `force`.
If `kind` is omitted, the default set is media only: `image`, `raw`, and
`video`. `other` is excluded unless explicitly requested.
If `kind=all`, the cursor should include `other` as well.

Response:

```text
cursor,1
status,scanning
matched,0
scanned,0
emitted,0
remaining,0
```

The camera scans once, stores compact arrays for the matched files, and pages
from that session. `matched` is current while scanning and final once the
cursor becomes ready, closed, expired, or errored.

### `GET /api/v1/cursor/status.txt`

Returns coarse cursor state without forcing a blocking scan.

```text
cursor,1
status,ready
matched,245
scanned,9823
emitted,100
remaining,145
```

### `GET /api/v1/cursor/files.txt?limit=100`

Returns one bounded page of file metadata from the active cursor session.

Response:

```text
# pmca-sync cursor v=1 cursor=1 status=ready limit=100 count=1 has_more=1 matched=245 scanned=9823 emitted=100 remaining=145 format=tsv fields=path,mtime,size,kind encoding=backslash
/storage/sdcard0/DCIM/100MSDCF/DSC01234.ARW	1719850000123	24891234	raw
```

Only `path` is the identity. There is no separate `id`, and `name` is derived
from `path` by the client. The record body is tab-separated, and fields escape
`\\`, tab, CR, and LF with backslash sequences so the path stays readable in a
browser while remaining safe to parse.

`size` stays in the base record because `File.length()` is cheap and lets the
local server validate downloads. It is not a content hash. `kind` is optional
and should be derived from extension only.

### `GET /api/v1/file.txt?path=...`

Returns metadata for one file. The local server can use this endpoint to
revalidate size and modification time before downloading.

### `GET /api/v1/download?path=...`

Streams one file. Range support is a later enhancement if interrupted downloads
need to resume.

### `POST /api/v1/cursor/close.txt`

Closes the active session and releases the matched arrays.

### `GET /api/v1/ui-status.txt`

Returns the human status line shown on the camera screen.
The local Python sync client can also `POST` a `message=...` field here to
publish sync progress back to the camera UI.

For first inventory or full audit, the local server can page through
`/api/v1/cursor/files.txt`. Do not add a separate JSON or NDJSON inventory
endpoint unless real hardware proves it is needed.

## Cursor Plan

The first implementation uses one singleton cursor session, not a persistent
database.

* `create` starts one background scan for the chosen filter set.
* The cursor keeps compact arrays of `path`, `mtime`, `size`, and `kind`.
* `files` pages from that session until exhausted.
* `close` cancels and releases the session.
* `force=1` replaces a stale session.

Do not add sorting or offset pagination to the MVP. The local server should
own sync ordering, deduplication, and retry logic.

## Scanner Direction

Keep `FilesystemScanner` as a compatibility helper for the HTML page only.

* one traversal implementation is enough for the HTML page
* no Java 8-only filesystem APIs
* prefer `java.io.File`, `ArrayDeque`, and simple lists

The Python/local server should store sync state and compare returned metadata
against its own database. The camera should only expose metadata and file
content.
