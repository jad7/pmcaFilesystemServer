#!/usr/bin/env python3
"""Synchronize camera media files into a local directory."""

import argparse
import os
import sys
import tempfile
import time

from camera_client import CameraClient
from camera_client import parse_cursor_page
from camera_client import parse_key_values


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Sync camera media files into a local directory.")
    parser.add_argument("--base-url", required=True, help="Camera base URL, e.g. http://192.168.12.220:8080")
    parser.add_argument("--dest-dir", required=True, help="Local directory where files will be mirrored")
    parser.add_argument("--kind", choices=["all", "image", "raw", "video", "other"], help="Optional cursor kind filter")
    parser.add_argument("--prefix", help="Optional absolute prefix under the storage root")
    parser.add_argument("--modified-after-ms", type=int, help="Lower bound in epoch milliseconds")
    parser.add_argument("--hours", type=float, default=24.0, help="Default modified-after window in hours when explicit ms is not set")
    parser.add_argument("--force", dest="force", action="store_true", default=True, help="Replace any existing live cursor")
    parser.add_argument("--no-force", dest="force", action="store_false", help="Fail when a cursor is already active")
    parser.add_argument("--limit", type=int, default=100, help="Cursor page size")
    parser.add_argument("--poll-seconds", type=float, default=0.5, help="Polling interval while the cursor is scanning")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="How long to wait for scanning and downloads to finish")
    parser.add_argument("--no-close", action="store_true", help="Leave the cursor open after the sync")
    parser.add_argument("--all-files", action="store_true", help="Send no modified-after filter")
    parser.add_argument("--strip-prefix", default="/storage/sdcard0", help="Camera path prefix to strip before mirroring locally")
    parser.add_argument("--dry-run", action="store_true", help="Print planned downloads without writing files")
    parser.add_argument("--overwrite", action="store_true", help="Download even when the destination file looks identical")
    return parser


def default_modified_after_ms(hours):
    return int((time.time() - (hours * 3600.0)) * 1000.0)


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def normalize_camera_path(path):
    if not path.startswith("/"):
        path = "/" + path
    return os.path.normpath(path)


def local_relative_path(camera_path, strip_prefix):
    normalized_path = normalize_camera_path(camera_path)
    normalized_prefix = normalize_camera_path(strip_prefix) if strip_prefix else ""
    if normalized_prefix and normalized_path == normalized_prefix:
        relative_path = os.path.basename(normalized_path)
    elif normalized_prefix and normalized_path.startswith(normalized_prefix + "/"):
        relative_path = normalized_path[len(normalized_prefix) + 1 :]
    else:
        relative_path = normalized_path.lstrip("/")
    relative_path = os.path.normpath(relative_path)
    if relative_path.startswith("..") or os.path.isabs(relative_path):
        raise ValueError("unsafe camera path: %s" % camera_path)
    return relative_path


def should_skip_existing(destination_path, entry):
    if not os.path.exists(destination_path):
        return False
    stat_result = os.stat(destination_path)
    if stat_result.st_size != entry["size"]:
        return False
    current_mtime_ms = int(stat_result.st_mtime * 1000.0)
    return abs(current_mtime_ms - entry["mtime"]) <= 1000


def write_download(client, camera_path, destination_path, mtime_ms, dry_run):
    if dry_run:
        return 200, None
    destination_dir = os.path.dirname(destination_path)
    if destination_dir and not os.path.isdir(destination_dir):
        os.makedirs(destination_dir, exist_ok=True)
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".download-", dir=destination_dir or None)
        os.close(tmp_fd)
        code, body = client.download_to_path(camera_path, tmp_path)
        if code != 200:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return code, body
        os.replace(tmp_path, destination_path)
        os.utime(destination_path, (mtime_ms / 1000.0, mtime_ms / 1000.0))
        return 200, None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def print_block(title, status, body):
    print("== %s ==" % title)
    print("HTTP %s" % status)
    if body:
        print(body.rstrip("\n"))
    print("")


def main():
    args = build_argument_parser().parse_args()
    ensure_dir(args.dest_dir)
    client = CameraClient(args.base_url, timeout_seconds=args.timeout_seconds)

    hello_status, hello_body = client.hello()
    print_block("hello", hello_status, hello_body)

    if args.all_files:
        modified_after = None
    elif args.modified_after_ms is not None:
        modified_after = args.modified_after_ms
    else:
        modified_after = default_modified_after_ms(args.hours)

    create_status, create_body = client.cursor_create(
        modified_after=modified_after,
        prefix=args.prefix,
        kind=args.kind,
        force=args.force,
    )
    print_block("cursor/create", create_status, create_body)
    if create_status != 202:
        raise SystemExit("cursor/create failed with HTTP %s" % create_status)

    deadline = time.time() + args.timeout_seconds
    while True:
        status_code, status_body = client.cursor_status()
        status_values = parse_key_values(status_body)
        print_block("cursor/status", status_code, status_body)
        if status_values.get("status") != "scanning":
            break
        if time.time() >= deadline:
            raise SystemExit("cursor did not become ready before timeout")
        time.sleep(args.poll_seconds)

    downloaded = 0
    skipped = 0
    failed = 0
    pages = 0

    try:
        while True:
            page_status, page_body = client.cursor_files(limit=args.limit)
            print_block("cursor/files", page_status, page_body)
            if page_status != 200:
                raise SystemExit("cursor/files failed with HTTP %s" % page_status)
            header, entries = parse_cursor_page(page_body)
            pages += 1
            for entry in entries:
                relative_path = local_relative_path(entry["path"], args.strip_prefix)
                destination_path = os.path.join(args.dest_dir, relative_path)
                if not args.overwrite and should_skip_existing(destination_path, entry):
                    skipped += 1
                    continue
                status, body = write_download(client, entry["path"], destination_path, entry["mtime"], args.dry_run)
                if status != 200:
                    failed += 1
                    print("FAILED %s -> HTTP %s" % (entry["path"], status))
                    if body:
                        print(body.rstrip("\n"))
                    continue
                downloaded += 1
                print("SAVED %s" % destination_path)
            if header.get("has_more") != "1":
                break
    finally:
        if not args.no_close:
            close_status, close_body = client.cursor_close()
            print_block("cursor/close", close_status, close_body)

    print("summary,downloaded=%d skipped=%d failed=%d pages=%d" % (downloaded, skipped, failed, pages))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
