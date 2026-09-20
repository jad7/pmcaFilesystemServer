import tempfile
import unittest
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sony_a6000_sync as sync
from sync_store import SyncStore


class Response(io.BytesIO):
    def getcode(self):
        return 200


class BatchTests(unittest.TestCase):
    def make_store(self, directory):
        directory = Path(directory)
        config = SimpleNamespace(
            temp_root=directory / "staging",
            database_file=directory / "sync.sqlite3",
            camera_id="camera",
            state_file=directory / "state.json",
            initial_last_synced="2026-01-01T00:00:00+00:00",
        )
        sync.CONFIG = config
        config.temp_root.joinpath("sqlite-v1", "batches", "one").mkdir(parents=True)
        return SyncStore(config.database_file, config.camera_id, 0)

    def test_download_and_import_are_batched(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            sync.CONFIG.batch_size = 2
            sync.CONFIG.download_timeout_seconds = 60
            records = [
                sync.CameraFile("/a.JPG", 10, 4, "image"),
                sync.CameraFile("/b.JPG", 20, 4, "image"),
                sync.CameraFile("/c.JPG", 30, 4, "image"),
            ]
            store.start_inventory(records)
            with patch.object(sync, "CLIENT") as client, patch.object(sync, "run_immich_upload") as upload:
                client.open_download.side_effect = lambda *args, **kwargs: Response(b"data")
                self.assertEqual(sync.download_next_batch(store), 2)
                self.assertEqual(sync.process_downloaded_batches(store), 2)
                self.assertEqual(upload.call_count, 1)
                self.assertEqual(sync.download_next_batch(store), 1)
                self.assertEqual(sync.process_downloaded_batches(store), 1)
                self.assertEqual(upload.call_count, 2)
            self.assertEqual(store.counts()["cleaned"], 3)
            store.close()

    def test_successful_import_deletes_only_imported_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            record = sync.CameraFile("/DCIM/a.JPG", 10, 4, "image")
            store.start_inventory([record])
            pending = store.pending_files(1)[0]
            path = sync.CONFIG.temp_root / "sqlite-v1/batches/one/1__a.JPG"
            path.write_bytes(b"data")
            store.mark_downloaded(pending.id, "batches/one/1__a.JPG")
            with patch.object(sync, "run_immich_upload"):
                self.assertEqual(sync.process_downloaded_batches(store), 1)
            self.assertFalse(path.exists())
            self.assertEqual(store.counts()["cleaned"], 1)
            store.close()

    def test_failed_import_keeps_files_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            record = sync.CameraFile("/DCIM/a.JPG", 10, 4, "image")
            store.start_inventory([record])
            pending = store.pending_files(1)[0]
            path = sync.CONFIG.temp_root / "sqlite-v1/batches/one/1__a.JPG"
            path.write_bytes(b"data")
            store.mark_downloaded(pending.id, "batches/one/1__a.JPG")
            with patch.object(sync, "run_immich_upload", side_effect=RuntimeError("Immich down")):
                with self.assertRaisesRegex(RuntimeError, "Immich down"):
                    sync.process_downloaded_batches(store)
            self.assertTrue(path.exists())
            self.assertEqual(store.counts()["downloaded"], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
