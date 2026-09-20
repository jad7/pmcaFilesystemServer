import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sony_a6000_sync as sync


class Response(io.BytesIO):
    def getcode(self):
        return 200


class BrokenResponse(Response):
    def read(self, size=-1):
        if self.tell():
            raise TimeoutError("connection lost")
        return super().read(size)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.ui_status.return_value = (200, "ok")
        self.config = SimpleNamespace(download_timeout_seconds=60, page_limit=100)
        self.runtime = patch.multiple(sync, CLIENT=self.client, CONFIG=self.config)
        self.runtime.start()
        self.addCleanup(self.runtime.stop)

    def test_page_timeout_is_not_retried(self):
        self.client.cursor_files.side_effect = TimeoutError("lost response")
        with self.assertRaises(TimeoutError):
            sync.fetch_inventory(2)
        self.client.cursor_files.assert_called_once()

    def test_missing_inventory_aborts_before_download(self):
        self.client.cursor_files.return_value = (200, "# has_more=0\n")
        with self.assertRaisesRegex(RuntimeError, "incomplete inventory"):
            sync.fetch_inventory(2)
        self.client.open_download.assert_not_called()

    def test_disconnect_retries_and_checks_size(self):
        payload = b"x" * (128 * 1024)
        self.client.open_download.side_effect = [BrokenResponse(payload), Response(payload)]
        file = sync.CameraFile("/DCIM/test.ARW", 1783300000000, len(payload), "raw", 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(sync.time, "sleep"):
            destination = sync.download_file(file, Path(directory), 2, 1480)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(destination.with_name(destination.name + ".part").exists())
        self.assertEqual(self.client.open_download.call_count, 2)
        self.assertEqual(self.client.open_download.call_args.kwargs["file_index"], 2)
