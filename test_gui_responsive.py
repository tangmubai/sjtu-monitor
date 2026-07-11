import hashlib
import json
import unittest

import config
from gui_backend import build_snapshot


RUNTIME_FILES = [
    config.STATE_FILE,
    config.SWAP_STATE_FILE,
    config.CATALOG_FILE,
    config.USER_SETTINGS_FILE,
    config.ZZXK_CAPACITY_FILE,
    config.SEAT_DETAILS_FILE,
    config.RATINGS_FILE,
]


def file_hash(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GuiBackendSnapshotTests(unittest.TestCase):
    def test_snapshot_is_json_serializable(self):
        snapshot = build_snapshot()
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertIn("metrics", encoded)
        self.assertIn("courses", snapshot)
        self.assertIn("groups", snapshot)
        self.assertIn("onboarding", snapshot)

    def test_snapshot_does_not_mutate_runtime_files(self):
        before = {str(path): file_hash(path) for path in RUNTIME_FILES}
        build_snapshot()
        after = {str(path): file_hash(path) for path in RUNTIME_FILES}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
