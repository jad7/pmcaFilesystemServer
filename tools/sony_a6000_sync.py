#!/usr/bin/env python3
"""Sony a6000 -> Immich sync client."""

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from camera_client import CameraClient
from camera_client import parse_cursor_page
from camera_client import parse_key_values
from sync_store import SyncStore
from sync_store import SyncLock
from sync_store import legacy_baseline_mtime_ms


@dataclass
class CameraFile(object):
    path: str
    mtime_ms: int
    size_bytes: int
    kind: str
    id: int = 0


@dataclass
class SyncConfig(object):
    version: int
    camera_id: str
    camera_base_url: str
    immich_api_url: str
    immich_api_key: str
    state_file: Path
    database_file: Path
    temp_root: Path
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
    initial_last_synced: str
    immich_cli_image: str
    immich_upload_args: list
    batch_size: int


CONFIG = None
CLIENT = None
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_INITIAL_DELAY_SECONDS = 1.0


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Sync Sony a6000 media into Immich.")
    parser.add_argument("--config", default="config/sony_a6000_sync.json", help="Path to the JSON config file")
    parser.add_argument("--once", action="store_true", help="Run a single sync and exit")
    parser.add_argument("--monitor", action="store_true", help="Keep waiting for the camera and sync when it appears")
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
        database_file=resolve_database_path(config_path, raw.get("database_file"), raw["state_file"]),
        temp_root=Path(raw["temp_root"]),
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
        initial_last_synced=raw.get("initial_last_synced"),
        immich_cli_image=str(raw.get("immich_cli_image", "ghcr.io/immich-app/immich-cli:latest")),
        immich_upload_args=list(raw.get("immich_upload_args", ["upload", "--recursive", "/import"])),
        batch_size=int(raw.get("batch_size", 20)),
    )

    if config.kind not in ("media", "all"):
        raise SystemExit("invalid config.kind: %s (expected media or all)" % config.kind)
    if config.batch_size < 1:
        raise SystemExit("invalid config.batch_size: must be positive")

    return config


def resolve_database_path(config_path, configured_path, state_path):
    if configured_path:
        path = Path(configured_path)
        return path if path.is_absolute() else config_path.parent / path
    state = Path(state_path)
    return state.with_suffix(".sqlite3")


def configure_runtime(config):
    global CONFIG, CLIENT
    CONFIG = config
    CLIENT = CameraClient(config.camera_base_url, timeout_seconds=config.hello_timeout_seconds)


def print_block(title, status, body):
    print("== %s ==" % title, flush=True)
    print("HTTP %s" % status, flush=True)
    if body:
        print(body.rstrip("\n"), flush=True)
    print("", flush=True)


def log_line(message):
    print(message, flush=True)


def set_ui_status(message):
    started = time.monotonic()
    try:
        status, body = CLIENT.ui_status(message)
        if status != 200:
            log_line("status update failed: HTTP %s: %s" % (status, body_preview(body)))
    except Exception as exc:
        log_line("status update failed: %s: %s" % (exc.__class__.__name__, exc))
    finally:
        elapsed = time.monotonic() - started
        if elapsed >= 0.5:
            log_line("slow status request: %.2fs" % elapsed)


def ensure_parent_dir(path):
    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def body_preview(body, max_lines=3, max_chars=240):
    lines = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    preview = " | ".join(lines)
    if not preview:
        return "<empty>"
    if len(preview) > max_chars:
        return preview[:max_chars] + "..."
    return preview


def call_with_retries(description, func, attempts=DEFAULT_RETRY_ATTEMPTS, initial_delay_seconds=DEFAULT_RETRY_INITIAL_DELAY_SECONDS):
    delay = initial_delay_seconds
    for attempt in range(1, attempts + 1):
        try:
            result = func()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts:
                raise RuntimeError("%s failed after %d attempts: %s: %s" % (description, attempts, exc.__class__.__name__, exc))
            log_line("%s attempt %d/%d failed: %s: %s; retrying in %.1fs" % (
                description,
                attempt,
                attempts,
                exc.__class__.__name__,
                exc,
                delay,
            ))
            time.sleep(delay)
            delay *= 2
            continue

        if isinstance(result, tuple) and len(result) == 2:
            status, body = result
            if status in RETRYABLE_HTTP_STATUSES:
                if attempt == attempts:
                    raise RuntimeError("%s failed after %d attempts with HTTP %s: %s" % (
                        description,
                        attempts,
                        status,
                        body_preview(body),
                    ))
                log_line("%s attempt %d/%d returned HTTP %s: %s; retrying in %.1fs" % (
                    description,
                    attempt,
                    attempts,
                    status,
                    body_preview(body),
                    delay,
                ))
                time.sleep(delay)
                delay *= 2
                continue
        return result
    raise RuntimeError("%s retry loop exhausted unexpectedly" % description)


def probe_camera(retries=DEFAULT_RETRY_ATTEMPTS, log_failures=True):
    try:
        if retries <= 1 and not log_failures:
            status, body = CLIENT.hello()
        else:
            status, body = call_with_retries("hello", lambda: CLIENT.hello(), attempts=retries)
    except RuntimeError as exc:
        return False, str(exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if log_failures:
            return False, "%s: %s" % (exc.__class__.__name__, exc)
        return False, None
    if status != 200:
        return False, "unexpected HTTP %s from %s: %s" % (status, CONFIG.camera_base_url, body_preview(body))
    fields = parse_key_values(body)
    protocol_value = fields.get("protocol", "")
    protocol_name = protocol_value.split(",", 1)[0].strip()
    if protocol_name != "pmca-sync":
        return False, "unexpected hello payload from %s: %s" % (CONFIG.camera_base_url, body_preview(body))
    return True, None


def create_cursor(modified_after_ms):
    status, body = call_with_retries(
        "cursor create",
        lambda: CLIENT.cursor_create(
            modified_after=modified_after_ms,
            kind=cursor_kind_argument(),
            force=True,
        ),
    )
    print_block("cursor/create", status, body)
    if status not in (200, 202):
        raise RuntimeError("cursor create failed with HTTP %s" % status)
    set_ui_status("cursor scanning")


def wait_cursor_ready(timeout_seconds):
    deadline = time.time() + timeout_seconds
    while True:
        status, body = call_with_retries("cursor status", lambda: CLIENT.cursor_status())
        print_block("cursor/status", status, body)
        if status != 200:
            raise RuntimeError("cursor status failed with HTTP %s" % status)
        values = parse_key_values(body)
        cursor_state = values.get("status")
        if cursor_state == "ready":
            set_ui_status(
                "cursor ready matched=%s scanned=%s remaining=%s" % (
                    values.get("matched", "?"),
                    values.get("scanned", "?"),
                    values.get("remaining", "?"),
                )
            )
            return values
        if cursor_state == "error":
            raise RuntimeError("cursor entered error state: %s" % values.get("error", "unknown error"))
        if time.time() >= deadline:
            raise RuntimeError("cursor did not become ready before timeout")
        time.sleep(0.5)


def _fetch_next_page(limit):
    # The camera advances its cursor on GET. Retrying a lost response skips files.
    status, body = CLIENT.cursor_files(limit=limit)
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


def fetch_inventory(expected_count):
    # Keep metadata on the Linux host, so long downloads cannot expire the cursor.
    files = []
    while True:
        page, has_more = _fetch_next_page(CONFIG.page_limit)
        files.extend(page)
        if not has_more:
            break
        if not page:
            raise RuntimeError("empty cursor page with has_more=1")
    if len(files) != expected_count:
        raise RuntimeError("incomplete inventory: expected %d files, received %d" % (
            expected_count, len(files)))
    # The camera walks directories in filesystem order. Sort on the Linux host
    # so batches are deterministic and chronological without extra camera RAM.
    files.sort(key=lambda file: (file.mtime_ms, file.path))
    log_line("inventory: %d files, %.1f MiB" % (
        len(files), sum(file.size_bytes for file in files) / 1048576.0))
    return files


def fetch_next_file_page(limit):
    files, _ = _fetch_next_page(limit)
    return files


def cursor_kind_argument():
    if CONFIG.kind == "all":
        return "all"
    return None


def staged_filename_for_camera_path(camera_path):
    normalized_path = camera_path.lstrip("/")
    relative_path = Path(normalized_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeError("unsafe camera path: %s" % camera_path)
    return normalized_path.replace("/", "__").replace("\\", "_")


def managed_root():
    return CONFIG.temp_root / "sqlite-v1"


def managed_path(local_name):
    relative = Path(local_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("unsafe local path: %s" % local_name)
    raw_path = managed_root() / relative
    if raw_path.is_symlink():
        raise RuntimeError("refusing symlink in staging: %s" % local_name)
    root = managed_root().resolve()
    path = raw_path.resolve()
    if not str(path).startswith(str(root) + os.sep):
        raise RuntimeError("local path escaped staging root: %s" % local_name)
    return path


def managed_name(path):
    return str(path.resolve().relative_to(managed_root().resolve()))


def download_file(file, batch_dir, file_index, total_files):
    destination = batch_dir / ("%d__%s" % (file.id, staged_filename_for_camera_path(file.path)))
    ensure_parent_dir(destination)
    part_path = destination.with_name(destination.name + ".part")
    delay = DEFAULT_RETRY_INITIAL_DELAY_SECONDS
    for attempt in range(1, DEFAULT_RETRY_ATTEMPTS + 1):
        response = None
        started = time.monotonic()
        received = 0
        last_report = started
        try:
            progress_message = "download %d/%d %s" % (
                file_index,
                total_files,
                file.path,
            )
            log_line("%s attempt %d/%d" % (progress_message, attempt, DEFAULT_RETRY_ATTEMPTS))
            response = CLIENT.open_download(file.path, timeout_seconds=CONFIG.download_timeout_seconds,
                                            file_index=file_index, total_files=total_files)
            status = response.getcode()
            if status != 200:
                body = response.read().decode("utf-8", "replace")
                if status in RETRYABLE_HTTP_STATUSES and attempt < DEFAULT_RETRY_ATTEMPTS:
                    log_line(
                        "download %d/%d HTTP %s for %s: %s; retrying in %.1fs" % (
                            file_index,
                            total_files,
                            status,
                            file.path,
                            body_preview(body),
                            delay,
                        )
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError("download failed for %s: HTTP %s %s" % (file.path, status, body.strip()))

            with part_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 64)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 2.0:
                        log_line("receiving %d/%d: %.1f/%.1f MiB, %.2f MiB/s, %.1fs, attempt %d" % (
                            file_index, total_files, received / 1048576.0,
                            file.size_bytes / 1048576.0,
                            received / 1048576.0 / max(now - started, 0.001), now - started, attempt))
                        last_report = now
                handle.flush()
                os.fsync(handle.fileno())

            downloaded_size = part_path.stat().st_size
            if downloaded_size != file.size_bytes:
                raise IOError("size mismatch for %s: expected %s got %s" % (file.path, file.size_bytes, downloaded_size))

            os.replace(str(part_path), str(destination))
            os.utime(str(destination), (file.mtime_ms / 1000.0, file.mtime_ms / 1000.0))
            elapsed = max(time.monotonic() - started, 0.001)
            done_message = "Downloaded %d/%d\n%.1f MiB in %.1fs\n%.2f MiB/s | attempt %d" % (
                file_index, total_files, received / 1048576.0, elapsed,
                received / 1048576.0 / elapsed, attempt)
            log_line(done_message)
            return destination
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            if attempt == DEFAULT_RETRY_ATTEMPTS:
                raise RuntimeError("download failed for %s after %d attempts: %s: %s" % (
                    file.path,
                    DEFAULT_RETRY_ATTEMPTS,
                    exc.__class__.__name__,
                    exc,
                ))
            log_line(
                "download %d/%d attempt %d/%d failed %s: %s: %s; retrying in %.1fs" % (
                    file_index,
                    total_files,
                    attempt,
                    DEFAULT_RETRY_ATTEMPTS,
                    file.path,
                    exc.__class__.__name__,
                    exc,
                    delay,
                )
            )
            set_ui_status("Retry %d/%d: file %d/%d\nStopped at %.1f MiB after %.1fs" % (
                attempt, DEFAULT_RETRY_ATTEMPTS, file_index, total_files,
                received / 1048576.0, time.monotonic() - started))
            time.sleep(delay)
            delay *= 2
        finally:
            if response is not None:
                response.close()
            if part_path.exists():
                part_path.unlink()


def close_cursor():
    try:
        status, body = CLIENT.cursor_close()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print("cursor close failed: %s: %s" % (exc.__class__.__name__, exc), file=sys.stderr)
        return
    print_block("cursor/close", status, body)
    if status != 200:
        raise RuntimeError("cursor close failed with HTTP %s" % status)


def render_immich_upload_args(year_label, import_path):
    rendered = []
    for arg in CONFIG.immich_upload_args:
        if arg == "/import":
            rendered.append(import_path)
            continue
        rendered.append(
            str(arg)
            .replace("{year}", year_label)
            .replace("{import_path}", import_path)
        )
    return rendered


def run_immich_upload(batch_dir, batch_label):
    container_import_path = "/import"
    upload_message = "uploading %s dir=%s" % (batch_label, batch_dir)
    log_line(upload_message)
    set_ui_status(upload_message)
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-v",
        "%s:%s:ro" % (str(batch_dir), container_import_path),
        "-e",
        "IMMICH_INSTANCE_URL=%s" % CONFIG.immich_api_url,
        "-e",
        "IMMICH_API_KEY=%s" % CONFIG.immich_api_key,
        CONFIG.immich_cli_image,
    ] + render_immich_upload_args(batch_label, container_import_path)
    subprocess.run(command, check=True)
    done_message = "uploaded %s" % batch_label
    log_line(done_message)
    set_ui_status(done_message)


def current_batch_dir():
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    return managed_root() / "batches" / ("%s-%s" % (stamp, os.getpid()))


def open_store():
    if CONFIG.database_file.exists() and CONFIG.database_file.stat().st_size > 0:
        baseline = 0
    else:
        baseline = legacy_baseline_mtime_ms(CONFIG.state_file, CONFIG.initial_last_synced)
    return SyncStore(CONFIG.database_file, CONFIG.camera_id, baseline)


def cleanup_imported_files(store):
    cleaned = 0
    for file in store.imported_files():
        try:
            path = managed_path(file.local_name)
            if path.exists():
                path.unlink()
            store.mark_cleaned(file.id)
            cleaned += 1
        except (OSError, RuntimeError) as exc:
            store.record_error(file.id, "cleanup: %s" % exc)
            log_line("cleanup pending %s: %s" % (file.path, exc))
    return cleaned


def reconcile_pending_downloads(store):
    for file in store.pending_files(1000000):
        if not file.local_name:
            continue
        try:
            path = managed_path(file.local_name)
            if path.is_file() and path.stat().st_size == file.size_bytes:
                store.mark_downloaded(file.id, file.local_name)
        except (OSError, RuntimeError) as exc:
            store.record_error(file.id, "reconcile: %s" % exc)


def process_downloaded_batches(store):
    imported = 0
    for relative_dir, files in store.downloaded_groups().items():
        batch_dir = managed_path(relative_dir)
        if not batch_dir.is_dir():
            for file in files:
                store.record_error(file.id, "missing batch directory: %s" % batch_dir)
            raise RuntimeError("missing downloaded batch directory: %s" % batch_dir)
        try:
            run_immich_upload(batch_dir, "batch files=%d" % len(files))
        except Exception as exc:
            for file in files:
                store.record_error(file.id, "Immich import: %s" % exc)
            raise
        store.mark_imported([file.id for file in files])
        imported += len(files)
        cleanup_imported_files(store)
        try:
            batch_dir.rmdir()
        except OSError:
            pass
    return imported


def recover_local_work(store):
    reconcile_pending_downloads(store)
    cleanup_imported_files(store)
    imported = process_downloaded_batches(store)
    cleanup_imported_files(store)
    return imported


def discover_inventory(store):
    completed_mtime_ms = store.completed_mtime_ms()
    modified_after_ms = completed_mtime_ms - int(CONFIG.safety_window_seconds * 1000)
    set_ui_status("creating cursor")
    create_cursor(modified_after_ms)
    try:
        cursor_snapshot = wait_cursor_ready(CONFIG.cursor_wait_timeout_seconds)
        matched_total = int(cursor_snapshot.get("matched", "0"))
        log_line(
            "cursor ready: matched=%s scanned=%s emitted=%s remaining=%s" % (
                cursor_snapshot.get("matched", "?"),
                cursor_snapshot.get("scanned", "?"),
                cursor_snapshot.get("emitted", "?"),
                cursor_snapshot.get("remaining", "?"),
            )
        )
        files = fetch_inventory(matched_total)
    finally:
        close_cursor()
    if not files:
        log_line("No files returned by cursor.")
        set_ui_status("no files to import")
        return 0
    store.start_inventory(files)
    log_line("inventory stored: %d files" % len(files))
    return len(files)


def download_next_batch(store):
    files = store.pending_files(CONFIG.batch_size)
    if not files:
        return 0
    total, remaining = store.inventory_progress()
    completed_before = max(0, total - remaining)
    existing_batch_dir = None
    for file in files:
        if file.local_name:
            existing_batch_dir = managed_path(str(Path(file.local_name).parent))
            break
    batch_dir = existing_batch_dir or current_batch_dir()
    batch_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for index, file in enumerate(files):
        if existing_batch_dir and file.local_name and managed_path(file.local_name).parent != batch_dir:
            continue
        if not file.local_name:
            local_path = batch_dir / ("%d__%s" % (
                file.id,
                staged_filename_for_camera_path(file.path),
            ))
            local_name = managed_name(local_path)
            store.assign_local_name(file.id, local_name)
        else:
            local_path = managed_path(file.local_name)
        if local_path.is_file() and local_path.stat().st_size == file.size_bytes:
            store.mark_downloaded(file.id, managed_name(local_path))
            downloaded += 1
            continue
        file_index = completed_before + index + 1
        local_path = download_file(file, batch_dir, file_index, total)
        local_name = managed_name(local_path)
        store.mark_downloaded(file.id, local_name)
        downloaded += 1
    return downloaded


def sync_once():
    store = open_store()
    downloaded_count = 0
    try:
        recover_local_work(store)
        if store.has_inventory() and store.finish_inventory_if_ready():
            return 0

        while True:
            if not store.has_inventory():
                camera_ready, camera_error = probe_camera()
                if not camera_ready:
                    if camera_error:
                        raise RuntimeError("camera is not reachable: %s" % camera_error)
                    raise RuntimeError("camera is not reachable")
                if discover_inventory(store) == 0:
                    return downloaded_count

            if store.finish_inventory_if_ready():
                set_ui_status("sync complete")
                return downloaded_count

            camera_ready, camera_error = probe_camera()
            if not camera_ready:
                if camera_error:
                    raise RuntimeError("camera is not reachable: %s" % camera_error)
                raise RuntimeError("camera is not reachable")
            downloaded_count += download_next_batch(store)
            recover_local_work(store)
    except Exception:
        try:
            recover_local_work(store)
        except Exception as recovery_error:
            log_line("local recovery failed: %s" % recovery_error)
        set_ui_status("Sync failed\nSee sync service log on Linux")
        raise
    finally:
        store.close()


def wait_for_camera_disconnect():
    misses = 0
    while True:
        camera_ready, _ = probe_camera(retries=1, log_failures=False)
        if camera_ready:
            misses = 0
        else:
            misses += 1
            if misses >= CONFIG.disconnect_failure_threshold:
                return
        time.sleep(CONFIG.disconnect_poll_seconds)


def monitor_loop():
    while True:
        try:
            store = open_store()
            try:
                recover_local_work(store)
                store.finish_inventory_if_ready()
            finally:
                store.close()
        except Exception as exc:
            print("local recovery failed: %s" % exc, file=sys.stderr)
        camera_ready, _ = probe_camera(retries=1, log_failures=False)
        if not camera_ready:
            time.sleep(CONFIG.idle_poll_seconds)
            continue
        try:
            sync_once()
        except Exception as exc:
            print("sync failed: %s" % exc, file=sys.stderr)
            time.sleep(CONFIG.failure_retry_seconds)
            continue
        wait_for_camera_disconnect()


def main():
    args = build_argument_parser().parse_args()
    config = load_config(args.config)
    configure_runtime(config)
    lock = SyncLock(config.database_file)
    try:
        lock.acquire()
        if args.monitor:
            monitor_loop()
            return 0
        sync_once()
        return 0
    except Exception as exc:
        print("sync failed: %s" % exc, file=sys.stderr)
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
