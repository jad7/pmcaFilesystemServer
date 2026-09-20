import json
import tempfile
import unittest
from pathlib import Path

from sync_store import SyncStore
from sync_store import legacy_baseline_mtime_ms
from sony_a6000_sync import CameraFile


class StoreTests(unittest.TestCase):
    def test_inventory_survives_restart_and_completes_after_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "sync.sqlite3"
            store = SyncStore(db_path, "sony-a6000", 1000)
            records = [
                CameraFile("/DCIM/a.JPG", 2000, 10, "image"),
                CameraFile("/DCIM/b.ARW", 3000, 20, "raw"),
                CameraFile("/DCIM/c.MP4", 4000, 30, "video"),
            ]
            store.start_inventory(records)
            pending = store.pending_files(20)
            self.assertEqual(len(pending), 3)
            store.mark_downloaded(pending[0].id, "batches/one/1__a.JPG")
            store.close()

            reopened = SyncStore(db_path, "sony-a6000", 1000)
            self.assertEqual(len(reopened.downloaded_groups()["batches/one"]), 1)
            remaining = reopened.pending_files(20)
            self.assertEqual(len(remaining), 2)
            for record in remaining:
                reopened.mark_downloaded(record.id, "batches/one/%d__file" % record.id)
            files = reopened.downloaded_groups()["batches/one"]
            reopened.mark_imported([record.id for record in files])
            self.assertTrue(reopened.finish_inventory_if_ready())
            self.assertEqual(reopened.completed_mtime_ms(), 4000)
            self.assertEqual(reopened.inventory_progress(), (0, 0))
            reopened.mark_cleaned(files[0].id)
            reopened.close()

    def test_duplicate_inventory_does_not_reset_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SyncStore(Path(directory) / "sync.sqlite3", "camera", 0)
            record = CameraFile("/a.JPG", 10, 5, "image")
            store.start_inventory([record])
            stored = store.pending_files(1)[0]
            store.mark_downloaded(stored.id, "batches/one/a.JPG")
            store.start_inventory([record])
            self.assertEqual(store.downloaded_groups()["batches/one"][0].status, "downloaded")
            store.close()

    def test_legacy_baseline_reads_integer_and_iso_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"last_imported_mtime_ms": 1234}))
            self.assertEqual(legacy_baseline_mtime_ms(path, None), 1234)
            path.write_text(json.dumps({"last_synced": "2026-01-01T00:00:00+00:00"}))
            self.assertEqual(legacy_baseline_mtime_ms(path, None), 1767225600000)


if __name__ == "__main__":
    unittest.main()
