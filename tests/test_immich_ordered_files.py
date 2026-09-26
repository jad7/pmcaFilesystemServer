import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import immich_ordered_files as ordered


class OrderedFilesTests(unittest.TestCase):
    def test_fetch_assets_pages_with_camera_filters(self):
        responses = [
            {
                "assets": {
                    "items": [{
                        "id": "a",
                        "originalFileName": "1088__mnt__sdcard__DSC08489.JPG",
                        "fileCreatedAt": "2026-09-02T10:00:00.000Z",
                    }],
                    "nextPage": "2",
                }
            },
            {
                "assets": {
                    "items": [{
                        "id": "b",
                        "originalFileName": "1090__mnt__sdcard__DSC08487.JPG",
                        "fileCreatedAt": "2026-09-01T10:00:00.000Z",
                    }],
                    "nextPage": None,
                }
            },
        ]
        with patch.object(ordered, "request_json", side_effect=responses) as request:
            assets = ordered.fetch_assets(
                "http://immich/api", "secret", "2026-09", "SONY", "ILCE-6000", 1, 30
            )
        self.assertEqual([asset.asset_id for asset in assets], ["a", "b"])
        first_payload = request.call_args_list[0].args[2]
        self.assertEqual(first_payload["make"], "SONY")
        self.assertEqual(first_payload["model"], "ILCE-6000")
        self.assertEqual(first_payload["takenAfter"], "2026-09-01T00:00:00Z")
        self.assertEqual(first_payload["takenBefore"], "2026-10-01T00:00:00Z")

    def test_sort_key_and_camera_path(self):
        assets = [
            ordered.ImmichAsset(
                "a", "1088__mnt__sdcard__DCIM__103MSDCF__DSC08489.JPG", "", "2026-09-02T00:00:00Z", "", "", ""
            ),
            ordered.ImmichAsset(
                "b", "1090__mnt__sdcard__DCIM__103MSDCF__DSC08487.JPG", "", "2026-09-01T00:00:00Z", "", "", ""
            ),
        ]
        assets.sort(key=ordered.date_sort_key)
        self.assertEqual([asset.asset_id for asset in assets], ["b", "a"])
        self.assertEqual(assets[1].camera_path, "/mnt/sdcard/DCIM/103MSDCF/DSC08489.JPG")

    def test_write_tsv_escapes_values(self):
        asset = ordered.ImmichAsset(
            "asset", "DSC00001.JPG", "path\twith\nnewline", "2026-09-01T00:00:00Z", "", "", ""
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ordered.tsv"
            ordered.write_tsv(output, [asset], "2026-09", "SONY", "ILCE-6000")
            text = output.read_text()
        self.assertIn("path\\twith\\nnewline", text)
        self.assertIn("\tasset\t", text)


if __name__ == "__main__":
    unittest.main()
