#!/usr/bin/env python3
"""Export a deterministic, read-only list of Immich assets."""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_PAGE_SIZE = 1000
DEFAULT_TIMEOUT_SECONDS = 30
CAMERA_FILE_RE = re.compile(r"^(?P<id>[0-9]+)__(?P<encoded>.+)$")


@dataclass
class ImmichAsset(object):
    asset_id: str
    original_file_name: str
    original_path: str
    file_created_at: str
    local_date_time: str
    file_modified_at: str
    exif_date_time_original: str

    @property
    def camera_db_id(self):
        match = CAMERA_FILE_RE.match(self.original_file_name)
        return match.group("id") if match else ""

    @property
    def camera_path(self):
        match = CAMERA_FILE_RE.match(self.original_file_name)
        if not match:
            return ""
        return "/" + match.group("encoded").replace("__", "/")

    @property
    def camera_file_name(self):
        if self.camera_path:
            return Path(self.camera_path).name
        return self.original_file_name


def parse_month(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise SystemExit("invalid month %r; expected YYYY-MM" % value)
    start = parsed.replace(tzinfo=timezone.utc)
    if parsed.month == 12:
        end = parsed.replace(year=parsed.year + 1, month=1, tzinfo=timezone.utc)
    else:
        end = parsed.replace(month=parsed.month + 1, tzinfo=timezone.utc)
    return start, end


def load_config(path):
    config_path = Path(path)
    if not config_path.exists():
        raise SystemExit("missing config: %s" % config_path)
    with config_path.open("r") as handle:
        config = json.load(handle)
    try:
        base_url = str(config["immich_api_url"]).rstrip("/")
        api_key = str(config["immich_api_key"])
    except KeyError as exc:
        raise SystemExit("config is missing %s" % exc.args[0])
    if not api_key or api_key == "replace-me":
        raise SystemExit("config contains no usable immich_api_key")
    return base_url, api_key


def request_json(url, api_key, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError("Immich HTTP %s: %s" % (exc.code, body[:500]))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Immich request failed: %s: %s" % (exc.__class__.__name__, exc))
    try:
        return json.loads(body)
    except ValueError as exc:
        raise RuntimeError("Immich returned invalid JSON: %s" % exc)


def asset_from_json(value):
    exif = value.get("exifInfo") or {}
    return ImmichAsset(
        asset_id=str(value.get("id", "")),
        original_file_name=str(value.get("originalFileName", "")),
        original_path=str(value.get("originalPath", "")),
        file_created_at=str(value.get("fileCreatedAt", "")),
        local_date_time=str(value.get("localDateTime", "")),
        file_modified_at=str(value.get("fileModifiedAt", "")),
        exif_date_time_original=str(exif.get("dateTimeOriginal", "")),
    )


def fetch_assets(base_url, api_key, month, make, model, page_size, timeout):
    start, end = parse_month(month)
    page = 1
    assets = []
    seen_pages = set()
    while True:
        if page in seen_pages:
            raise RuntimeError("Immich returned a repeated page: %s" % page)
        seen_pages.add(page)
        payload = {
            "make": make,
            "model": model,
            "takenAfter": start.isoformat().replace("+00:00", "Z"),
            "takenBefore": end.isoformat().replace("+00:00", "Z"),
            "order": "asc",
            "page": page,
            "size": page_size,
            "withExif": True,
        }
        response = request_json(base_url + "/search/metadata", api_key, payload, timeout)
        asset_page = response.get("assets") or {}
        items = asset_page.get("items") or []
        assets.extend(asset_from_json(item) for item in items)
        next_page = asset_page.get("nextPage")
        if not next_page:
            break
        try:
            page = int(next_page)
        except ValueError:
            raise RuntimeError("invalid Immich nextPage: %r" % next_page)
    return assets


def date_sort_key(asset):
    value = asset.file_created_at or asset.local_date_time or asset.file_modified_at
    if not value:
        return (datetime.max.replace(tzinfo=timezone.utc), asset.original_file_name, asset.asset_id)
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc), asset.original_file_name, asset.asset_id)


def tsv_value(value):
    return str(value or "").replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def write_tsv(path, assets, month, make, model):
    output = sys.stdout if str(path) == "-" else Path(path).open("w", encoding="utf-8")
    try:
        output.write("# immich-ordered-files v=1 month=%s make=%s model=%s count=%d\n" % (
            month, make, model, len(assets)))
        output.write("index\tasset_id\tdb_id\tcamera_path\tcamera_name\toriginal_file_name\toriginal_path\tfile_created_at\tlocal_date_time\tfile_modified_at\texif_date_time_original\n")
        for index, asset in enumerate(assets, 1):
            output.write("\t".join([
                str(index),
                tsv_value(asset.asset_id),
                tsv_value(asset.camera_db_id),
                tsv_value(asset.camera_path),
                tsv_value(asset.camera_file_name),
                tsv_value(asset.original_file_name),
                tsv_value(asset.original_path),
                tsv_value(asset.file_created_at),
                tsv_value(asset.local_date_time),
                tsv_value(asset.file_modified_at),
                tsv_value(asset.exif_date_time_original),
            ]) + "\n")
    finally:
        if output is not sys.stdout:
            output.close()


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Export ordered Immich assets for a camera and month.")
    parser.add_argument("--config", default="config/sony_a6000_sync.json", help="Path to the sync JSON config")
    parser.add_argument("--month", default="2026-09", help="Month to query, in YYYY-MM format")
    parser.add_argument("--make", default="SONY")
    parser.add_argument("--model", default="ILCE-6000")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--output", default="-", help="TSV output path, or - for stdout")
    return parser


def main():
    args = build_argument_parser().parse_args()
    if args.page_size < 1:
        raise SystemExit("--page-size must be positive")
    if args.timeout < 1:
        raise SystemExit("--timeout must be positive")
    base_url, api_key = load_config(args.config)
    assets = fetch_assets(
        base_url,
        api_key,
        args.month,
        args.make,
        args.model,
        args.page_size,
        args.timeout,
    )
    assets.sort(key=date_sort_key)
    write_tsv(args.output, assets, args.month, args.make, args.model)
    if args.output != "-":
        print("wrote %d assets to %s" % (len(assets), args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
