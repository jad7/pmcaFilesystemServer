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
paths before running the Makefile targets.

```bash
adb tcpip 5555
adb connect 192.168.178.53:5555
```

See https://stackoverflow.com/a/3623727

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
capabilities,text,cursor,status
```

### `GET /api/v1/files.txt`

Returns one bounded page of file metadata.

Supported query parameters:

* `limit`: requested page size, capped by the server.
* `cursor`: opaque continuation token returned by the previous response.
* `kind`: `all`, `image`, `raw`, `video`, or `other`.
* `ext`: extension filter such as `.jpg`, `.arw`, `.mp4`.
* `prefix`: path prefix under the camera storage root.
* `modified_after`: epoch milliseconds lower bound.
* `order`: initially `mtime_desc`.

Response:

```text
# pmca-sync files v=1 limit=100 count=1 has_more=1 next=opaque-token format=tsv fields=path,mtime,size,kind encoding=backslash
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

### `GET /api/v1/status.txt`

Returns coarse camera-side state without forcing a blocking scan.

```text
status,ready
files_indexed,9823
scanning,0
```

For first inventory or full audit, the local server can page through
`/api/v1/files.txt`. Do not add a separate JSON or NDJSON inventory endpoint
unless real hardware proves it is needed.

## Pagination Plan

The first implementation should avoid a full in-memory inventory. A practical
approach is a bounded live scan:

* Traverse storage with old-compatible `java.io.File` APIs.
* Apply filters while walking.
* Keep only the best `limit + 1` candidates for the active cursor window.
* Return `limit` items.
* Use the extra candidate to compute `has_more`.

The initial stable ordering should be:

* `mtime DESC`
* `path ASC` as the tie-breaker

The cursor should encode the last emitted `(mtime, path)` tuple plus the active
query shape. Clients must treat it as opaque.

This design trades I/O per page for bounded camera memory. If this is too slow
on real hardware, the next step is a small persistent file index. Do not start
with a database unless the bounded scan is proven insufficient on the camera.

Normal sync should query recent files first:

* The local server stores the highest imported `mtime`.
* The next sync calls `/api/v1/files.txt?modified_after=<last_mtime_minus_safety_window>&order=mtime_desc`.
* The local server stops when returned pages are older than the sync window or
  all files in the page are already known.
* The local server remains idempotent because camera-side snapshots are not
  guaranteed.

## Scanner Refactoring Direction

The current `FilesystemScanner` performs separate recursive scans for videos,
JPEGs, and RAW files. The sync API needs a single generic scanner instead:

* one traversal implementation
* one file metadata model
* one filter object
* one page result object
* no Java 8-only filesystem APIs

The Python/local server should store sync state and compare returned metadata
against its own database. The camera should only expose metadata and file
content.
