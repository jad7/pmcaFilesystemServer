#!/usr/bin/env python3
"""Sony a6000 -> Immich sync client."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from camera_client import CameraClient
from camera_client import parse_cursor_page
from camera_client import parse_key_values


@dataclass
class CameraFile(object):
    path: str
    mtime_ms: int
    size_bytes: int
    kind: str


@dataclass
class SyncConfig(object):
    version: int
    camera_id: str
    camera_base_url: str
    immich_api_url: str
    immich_api_key: str
    state_file: Path
    temp_root: Path
    prefix: str
    kind: str
    page_limit: int
    safety_window_seconds: int
    hello_timeout_seconds: int
    download_timeout_seconds: int
    cursor_wait_timeout_seconds: int
    disconnect_poll_seconds: int
    disconnect_failure_threshold: int
    idle_poll_seconds: int
    failure_retry_seconds: int
    camera_strip_prefix: str
    initial_last_synced: str
    immich_cli_image: str
    immich_upload_args: list


CONFIG = None
CLIENT = None


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Sync Sony a6000 media into Immich.")
    parser.add_argument("--config", default="config/sony_a6000_sync.json", help="Path to the JSON config file")
    parser.add_argument("--once", action="store_true", help="Run a single sync and exit")
    parser.add_argument("--monitor", action="store_true", help="Keep waiting for the camera and sync when it appears")
    parser.add_argument("--init-baseline", help="Initialize or reset state.last_synced and exit")
    return parser


def load_config(path):
    config_path = Path(path)
    if not config_path.exists():
        if config_path.suffix:
            example_path = config_path.with_name(
                config_path.name.replace(config_path.suffix, ".example" + config_path.suffix)
            )
        else:
            example_path = Path(str(config_path) + ".example")
        if example_path.exists():
            raise SystemExit("missing config: %s (copy %s)" % (config_path, example_path))
        raise SystemExit("missing config: %s" % config_path)

    with config_path.open("r") as handle:
        raw = json.load(handle)

    config = SyncConfig(
        version=int(raw.get("version", 1)),
        camera_id=str(raw["camera_id"]),
        camera_base_url=str(raw["camera_base_url"]),
        immich_api_url=str(raw["immich_api_url"]),
        immich_api_key=str(raw["immich_api_key"]),
        state_file=Path(raw["state_file"]),
        temp_root=Path(raw["temp_root"]),
        prefix=str(raw["prefix"]),
        kind=str(raw.get("kind", "media")).strip().lower(),
        page_limit=int(raw.get("page_limit", 100)),
        safety_window_seconds=int(raw.get("safety_window_seconds", 600)),
        hello_timeout_seconds=int(raw.get("hello_timeout_seconds", 3)),
        download_timeout_seconds=int(raw.get("download_timeout_seconds", 60)),
        cursor_wait_timeout_seconds=int(raw.get("cursor_wait_timeout_seconds", 300)),
        disconnect_poll_seconds=float(raw.get("disconnect_poll_seconds", 10)),
        disconnect_failure_threshold=int(raw.get("disconnect_failure_threshold", 3)),
        idle_poll_seconds=float(raw.get("idle_poll_seconds", 30)),
        failure_retry_seconds=float(raw.get("failure_retry_seconds", 15)),
        camera_strip_prefix=str(raw.get("camera_strip_prefix", "/storage/sdcard0")),
        initial_last_synced=str(raw.get("initial_last_synced")),
        immich_cli_image=str(raw.get("immich_cli_image", "ghcr.io/immich-app/immich-cli:latest")),
        immich_upload_args=list(raw.get("immich_upload_args", ["upload", "--recursive", "/import"])),
    )

    if config.kind not in ("media", "all"):
        raise SystemExit("invalid config.kind: %s (expected media or all)" % config.kind)

    return config


def configure_runtime(config):
    global CONFIG, CLIENT
    CONFIG = config
    CLIENT = CameraClient(config.camera_base_url, timeout_seconds=config.hello_timeout_seconds)


def print_block(title, status, body):
    print("== %s ==" % title)
    print("HTTP %s" % status)
    if body:
        print(body.rstrip("\n"))
    print("")


def ensure_parent_dir(path):
    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def parse_iso_datetime(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def format_iso_datetime(value):
    return value.astimezone().replace(microsecond=0).isoformat()


def init_state_from_baseline():
    if not CONFIG.initial_last_synced:
        raise SystemExit("state file missing and no initial_last_synced provided in config")
    baseline = parse_iso_datetime(CONFIG.initial_last_synced)
    state = {
        "version": CONFIG.version,
        "camera_id": CONFIG.camera_id,
        "last_synced": format_iso_datetime(baseline),
    }
    write_state_atomic(state)
    return state


def read_state():
    state_path = CONFIG.state_file
    if not state_path.exists():
        return init_state_from_baseline()

    with state_path.open("r") as handle:
        state = json.load(handle)

    if "version" not in state:
        state["version"] = CONFIG.version
    if "camera_id" not in state:
        state["camera_id"] = CONFIG.camera_id
    if "last_synced" not in state:
        raise SystemExit("state file is missing last_synced: %s" % state_path)
    return state


def write_state_atomic(state):
    state_path = CONFIG.state_file
    ensure_parent_dir(state_path)
    tmp_path = state_path.with_name(state_path.name + ".tmp")
    with tmp_path.open("w") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp_path), str(state_path))


def probe_camera():
    status, body = CLIENT.hello()
    if status != 200:
        return False
    fields = parse_key_values(body)
    return fields.get("protocol") == "pmca-sync"


def create_cursor(modified_after_ms):
    status, body = CLIENT.cursor_create(
        modified_after=modified_after_ms,
        prefix=CONFIG.prefix,
        kind=cursor_kind_argument(),
        force=True,
    )
    print_block("cursor/create", status, body)
    if status not in (200, 202):
        raise RuntimeError("cursor create failed with HTTP %s" % status)


def wait_cursor_ready(timeout_seconds):
    deadline = time.time() + timeout_seconds
    while True:
        status, body = CLIENT.cursor_status()
        print_block("cursor/status", status, body)
        if status != 200:
            raise RuntimeError("cursor status failed with HTTP %s" % status)
        values = parse_key_values(body)
        cursor_state = values.get("status")
        if cursor_state == "ready":
            return values
        if cursor_state == "error":
            raise RuntimeError("cursor entered error state: %s" % values.get("error", "unknown error"))
        if time.time() >= deadline:
            raise RuntimeError("cursor did not become ready before timeout")
        time.sleep(0.5)


def _fetch_next_page(limit):
    status, body = CLIENT.cursor_files(limit=limit)
    print_block("cursor/files", status, body)
    if status != 200:
        raise RuntimeError("cursor files failed with HTTP %s" % status)
    header, rows = parse_cursor_page(body)
    files = []
    for row in rows:
        files.append(CameraFile(
            path=row["path"],
            mtime_ms=row["mtime"],
            size_bytes=row["size"],
            kind=row["kind"],
        ))
    has_more = header.get("has_more") == "1"
    return files, has_more


def fetch_next_file_page(limit):
    files, _ = _fetch_next_page(limit)
    return files


def cursor_kind_argument():
    if CONFIG.kind == "all":
        return "all"
    return None


def camera_relative_path(camera_path):
    normalized_path = Path(camera_path).as_posix()
    prefix = CONFIG.camera_strip_prefix.rstrip("/")
    if prefix and normalized_path == prefix:
        relative = Path(normalized_path).name
    elif prefix and normalized_path.startswith(prefix + "/"):
        relative = normalized_path[len(prefix) + 1 :]
    else:
        relative = normalized_path.lstrip("/")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeError("unsafe camera path: %s" % camera_path)
    return relative_path


def download_file(file, batch_dir):
    destination = batch_dir / camera_relative_path(file.path)
    ensure_parent_dir(destination)
    part_path = destination.with_name(destination.name + ".part")

    response = CLIENT.open_download(file.path, timeout_seconds=CONFIG.download_timeout_seconds)
    try:
        if response.getcode() != 200:
            body = response.read().decode("utf-8", "replace")
            raise RuntimeError("download failed for %s: HTTP %s %s" % (file.path, response.getcode(), body.strip()))

        with part_path.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 64)
                if not chunk:
                    break
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        downloaded_size = part_path.stat().st_size
        if downloaded_size != file.size_bytes:
            raise RuntimeError("size mismatch for %s: expected %s got %s" % (file.path, file.size_bytes, downloaded_size))

        os.replace(str(part_path), str(destination))
        os.utime(str(destination), (file.mtime_ms / 1000.0, file.mtime_ms / 1000.0))
        return destination
    finally:
        response.close()
        if part_path.exists():
            part_path.unlink()


def close_cursor():
    status, body = CLIENT.cursor_close()
    print_block("cursor/close", status, body)
    if status != 200:
        raise RuntimeError("cursor close failed with HTTP %s" % status)


def run_immich_upload(batch_dir):
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-v",
        "%s:/import:ro" % str(batch_dir),
        "-e",
        "IMMICH_INSTANCE_URL=%s" % CONFIG.immich_api_url,
        "-e",
        "IMMICH_API_KEY=%s" % CONFIG.immich_api_key,
        CONFIG.immich_cli_image,
    ] + list(CONFIG.immich_upload_args)
    subprocess.run(command, check=True)


def current_batch_dir():
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    return CONFIG.temp_root / "incoming" / ("%s-%s" % (stamp, os.getpid()))


def sync_once():
    if not probe_camera():
        raise RuntimeError("camera is not reachable")

    state = read_state()
    last_synced = parse_iso_datetime(state["last_synced"])
    modified_after = last_synced - timedelta(seconds=CONFIG.safety_window_seconds)
    modified_after_ms = int(modified_after.timestamp() * 1000)

    create_cursor(modified_after_ms)
    wait_cursor_ready(CONFIG.cursor_wait_timeout_seconds)

    batch_dir = current_batch_dir()
    ensure_parent_dir(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)

    downloaded_files = []
    max_imported_mtime_ms = None
    max_imported_path = None
    max_imported_file = None

    try:
        while True:
            page_files, has_more = _fetch_next_page(CONFIG.page_limit)
            if not page_files:
                break

            for file in page_files:
                local_path = download_file(file, batch_dir)
                downloaded_files.append(local_path)
                if max_imported_mtime_ms is None or file.mtime_ms > max_imported_mtime_ms or (
                    file.mtime_ms == max_imported_mtime_ms and file.path > (max_imported_path or "")
                ):
                    max_imported_mtime_ms = file.mtime_ms
                    max_imported_path = file.path
                    max_imported_file = Path(file.path).name

            if not has_more:
                break

        if not downloaded_files:
            print("No files returned by cursor.")
            shutil.rmtree(str(batch_dir), ignore_errors=True)
            return 0

        run_immich_upload(batch_dir)

        now = datetime.now().astimezone()
        new_state = dict(state)
        new_state["version"] = CONFIG.version
        new_state["camera_id"] = CONFIG.camera_id
        new_state["last_synced"] = format_iso_datetime(datetime.fromtimestamp(max_imported_mtime_ms / 1000.0, tz=now.tzinfo))
        new_state["last_imported_file"] = max_imported_file
        new_state["last_imported_path"] = max_imported_path
        new_state["last_successful_sync"] = format_iso_datetime(now)
        write_state_atomic(new_state)

        shutil.rmtree(str(batch_dir))
        return len(downloaded_files)
    except Exception:
        raise
    finally:
        try:
            close_cursor()
        except Exception as exc:
            print("cursor close failed: %s" % exc, file=sys.stderr)


def wait_for_camera_disconnect():
    misses = 0
    while True:
        if probe_camera():
            misses = 0
        else:
            misses += 1
            if misses >= CONFIG.disconnect_failure_threshold:
                return
        time.sleep(CONFIG.disconnect_poll_seconds)


def monitor_loop():
    while True:
        if not probe_camera():
            time.sleep(CONFIG.idle_poll_seconds)
            continue
        try:
            sync_once()
        except Exception as exc:
            print("sync failed: %s" % exc, file=sys.stderr)
            time.sleep(CONFIG.failure_retry_seconds)
            continue
        wait_for_camera_disconnect()


def init_baseline_state(baseline_iso):
    state = {
        "version": CONFIG.version,
        "camera_id": CONFIG.camera_id,
        "last_synced": format_iso_datetime(parse_iso_datetime(baseline_iso)),
    }
    write_state_atomic(state)


def main():
    args = build_argument_parser().parse_args()
    config = load_config(args.config)
    configure_runtime(config)

    if args.init_baseline:
        init_baseline_state(args.init_baseline)
        print("Initialized state at %s" % CONFIG.state_file)
        return 0

    if args.monitor:
        monitor_loop()
        return 0

    sync_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
