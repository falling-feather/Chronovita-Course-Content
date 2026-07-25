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
COURSE_ARCHIVE_RELATIVE_PATH = Path(
    "C-early-civilization",
    "releases",
    "rel-01a2b3c4d5-0001",
    "arc-f64b2d24a81a398212867020c4c40ae1",
)
ASSET_ARCHIVE_RELATIVE_PATH = Path(
    "people",
    "tech-person-sample",
    "versions",
    "v001",
    "arc-05b1ae9b4d9ba9a21302ddf522b1df5c",
)


class ContentArchiveValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name)
        shutil.copytree(
            REPOSITORY_ROOT / ".chronovita",
            self.repository / ".chronovita",
        )

        course_root = self.repository / "courses"
        course_root.mkdir()
        shutil.copy2(REPOSITORY_ROOT / "courses" / ".gitkeep", course_root)
        shutil.copytree(
            REPOSITORY_ROOT / "tests" / "fixtures" / "dayu-course-archive",
            course_root / COURSE_ARCHIVE_RELATIVE_PATH,
        )

        asset_root = self.repository / "assets"
        asset_root.mkdir()
        shutil.copy2(REPOSITORY_ROOT / "assets" / ".gitkeep", asset_root)
        shutil.copytree(
            REPOSITORY_ROOT / "tests" / "fixtures" / "person-asset-archive",
            asset_root / ASSET_ARCHIVE_RELATIVE_PATH,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_course_and_asset_technical_fixtures_pass(self) -> None:
        self.assertEqual(validate_repository(self.repository), (2, 10))

    def test_changed_course_archive_blob_is_rejected(self) -> None:
        release_file = (
            self.repository
            / "courses"
            / COURSE_ARCHIVE_RELATIVE_PATH
            / "课程发布清单.json"
        )
        release_file.write_bytes(release_file.read_bytes() + b" ")

        with self.assertRaisesRegex(
            ValidationFailure,
            "size mismatch|SHA-256 mismatch",
        ):
            validate_repository(self.repository)

    def test_changed_asset_source_blob_is_rejected(self) -> None:
        source_file = (
            self.repository
            / "assets"
            / ASSET_ARCHIVE_RELATIVE_PATH
            / "李大钊(技术样板)-人物档案.json"
        )
        source_file.write_bytes(source_file.read_bytes() + b" ")

        with self.assertRaisesRegex(
            ValidationFailure,
            "size mismatch|SHA-256 mismatch",
        ):
            validate_repository(self.repository)

    def test_untracked_course_file_is_rejected(self) -> None:
        extra_file = (
            self.repository
            / "courses"
            / COURSE_ARCHIVE_RELATIVE_PATH
            / "未登记文件.txt"
        )
        extra_file.write_text(
            "must be declared by the manifest",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValidationFailure, "file set differ"):
            validate_repository(self.repository)

    def test_untracked_asset_file_is_rejected(self) -> None:
        extra_file = (
            self.repository
            / "assets"
            / ASSET_ARCHIVE_RELATIVE_PATH
            / "extra.json"
        )
        extra_file.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ValidationFailure, "file set differ"):
            validate_repository(self.repository)

    def test_asset_archive_at_wrong_path_is_rejected(self) -> None:
        original = self.repository / "assets" / ASSET_ARCHIVE_RELATIVE_PATH
        wrong = (
            self.repository
            / "assets"
            / "people"
            / "tech-person-sample"
            / "versions"
            / "v002"
            / ASSET_ARCHIVE_RELATIVE_PATH.name
        )
        wrong.parent.mkdir(parents=True)
        shutil.move(str(original), str(wrong))

        with self.assertRaisesRegex(
            ValidationFailure,
            "manifest location and archive_path differ",
        ):
            validate_repository(self.repository)

    def test_duplicate_json_key_is_rejected(self) -> None:
        policy_path = self.repository / ".chronovita" / "repository-policy.json"
        raw = policy_path.read_text(encoding="utf-8")
        raw = raw.replace(
            '"owner": "falling-feather",',
            '"owner": "falling-feather",\n'
            '  "owner": "falling-feather",',
            1,
        )
        policy_path.write_text(raw, encoding="utf-8")

        with self.assertRaisesRegex(ValidationFailure, "duplicate JSON key"):
            validate_repository(self.repository)

    def test_policy_cannot_store_credentials(self) -> None:
        policy_path = self.repository / ".chronovita" / "repository-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["github_token"] = "not-a-real-token"
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValidationFailure,
            "credential-like field is not allowed",
        ):
            validate_repository(self.repository)

    def test_asset_source_cannot_store_credentials(self) -> None:
        source_path = (
            self.repository
            / "assets"
            / ASSET_ARCHIVE_RELATIVE_PATH
            / "李大钊(技术样板)-人物档案.json"
        )
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["access_token"] = "not-a-real-token"
        source_path.write_text(
            json.dumps(source, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ValidationFailure,
            "size mismatch|credential-like field",
        ):
            validate_repository(self.repository)


if __name__ == "__main__":
    unittest.main()
