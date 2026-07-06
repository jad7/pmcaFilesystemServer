#!/usr/bin/env python3
"""Smoke-test the camera sync API on a live Sony camera."""

import argparse
import sys
import time

from camera_client import CameraClient
from camera_client import parse_header_fields
from camera_client import parse_key_values


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Probe the live camera sync API.")
    parser.add_argument("--base-url", required=True, help="Camera base URL, e.g. http://192.168.12.220:8080")
    parser.add_argument("--kind", choices=["all", "image", "raw", "video", "other"], help="Optional cursor kind filter")
    parser.add_argument("--prefix", help="Optional absolute prefix under the storage root")
    parser.add_argument("--modified-after-ms", type=int, help="Lower bound in epoch milliseconds")
    parser.add_argument("--hours", type=float, default=24.0, help="Default modified-after window in hours when explicit ms is not set")
    parser.add_argument("--force", dest="force", action="store_true", default=True, help="Replace any existing live cursor")
    parser.add_argument("--no-force", dest="force", action="store_false", help="Fail when a cursor is already active")
    parser.add_argument("--limit", type=int, default=100, help="Cursor page size")
    parser.add_argument("--max-pages", type=int, default=1, help="How many pages to print after the cursor is ready; 0 means unlimited")
    parser.add_argument("--poll-seconds", type=float, default=0.5, help="Polling interval while the cursor is scanning")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="How long to wait for scanning to finish")
    parser.add_argument("--no-close", action="store_true", help="Leave the cursor open after the probe")
    parser.add_argument("--all-files", action="store_true", help="Send no modified-after filter")
    return parser


def default_modified_after_ms(hours):
    return int((time.time() - (hours * 3600.0)) * 1000.0)


def print_block(title, status, body):
    print("== %s ==" % title)
    print("HTTP %s" % status)
    if body:
        print(body.rstrip("\n"))
    print("")


def main():
    args = build_argument_parser().parse_args()
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

    pages_printed = 0
    while args.max_pages == 0 or pages_printed < args.max_pages:
        page_status, page_body = client.cursor_files(limit=args.limit)
        print_block("cursor/files", page_status, page_body)
        header = parse_header_fields(page_body)
        pages_printed += 1
        if header.get("has_more") != "1":
            break

    if not args.no_close:
        close_status, close_body = client.cursor_close()
        print_block("cursor/close", close_status, close_body)


if __name__ == "__main__":
    sys.exit(main())
