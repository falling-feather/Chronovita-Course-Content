from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_content_archive import (
    ValidationFailure,
    validate_repository,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_RELATIVE_PATH = Path(
    "C-early-civilization",
    "releases",
    "rel-01a2b3c4d5-0001",
    "arc-f64b2d24a81a398212867020c4c40ae1",
)


class ContentArchiveValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name)
        shutil.copytree(
            REPOSITORY_ROOT / ".chronovita",
            self.repository / ".chronovita",
        )
        archive_root = self.repository / "courses"
        archive_root.mkdir()
        shutil.copy2(REPOSITORY_ROOT / "courses" / ".gitkeep", archive_root)
        shutil.copytree(
            REPOSITORY_ROOT / "tests" / "fixtures" / "dayu-course-archive",
            archive_root / ARCHIVE_RELATIVE_PATH,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_technical_fixture_passes(self) -> None:
        self.assertEqual(validate_repository(self.repository), (1, 8))

    def test_changed_archive_blob_is_rejected(self) -> None:
        release_file = (
            self.repository
            / "courses"
            / ARCHIVE_RELATIVE_PATH
            / "课程发布清单.json"
        )
        release_file.write_bytes(release_file.read_bytes() + b" ")

        with self.assertRaisesRegex(ValidationFailure, "字节数|SHA-256"):
            validate_repository(self.repository)

    def test_untracked_file_is_rejected(self) -> None:
        extra_file = (
            self.repository
            / "courses"
            / ARCHIVE_RELATIVE_PATH
            / "未登记文件.txt"
        )
        extra_file.write_text("must be declared by the manifest", encoding="utf-8")

        with self.assertRaisesRegex(ValidationFailure, "文件集合不一致"):
            validate_repository(self.repository)

    def test_policy_cannot_store_credentials(self) -> None:
        policy_path = self.repository / ".chronovita" / "repository-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["github_token"] = "not-a-real-token"
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValidationFailure, "不得包含凭据字段"):
            validate_repository(self.repository)


if __name__ == "__main__":
    unittest.main()
