from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from typing import Callable

from scripts.validate_content_archive import (
    ASSET_MANIFEST_FILENAME,
    ValidationFailure,
    canonical_checksum,
    validate_repository,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COURSE_ARCHIVE_RELATIVE_PATH = Path(
    "C-early-civilization",
    "releases",
    "rel-01a2b3c4d5-0001",
    "arc-f64b2d24a81a398212867020c4c40ae1",
)
ASSET_FIXTURE_NAMES = {
    "person": "person-asset-archive",
    "keyword": "keyword-asset-archive",
    "scenario": "scenario-asset-archive",
}


def _fixture_manifest(kind: str) -> dict[str, object]:
    path = (
        REPOSITORY_ROOT
        / "tests"
        / "fixtures"
        / ASSET_FIXTURE_NAMES[kind]
        / ASSET_MANIFEST_FILENAME
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_archive_path(kind: str) -> Path:
    value = str(_fixture_manifest(kind)["archive_path"])
    return Path(*PurePosixPath(value).parts)


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
        for kind, fixture_name in ASSET_FIXTURE_NAMES.items():
            shutil.copytree(
                REPOSITORY_ROOT / "tests" / "fixtures" / fixture_name,
                asset_root / _fixture_archive_path(kind),
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _asset_archive_dir(self, kind: str) -> Path:
        return self.repository / "assets" / _fixture_archive_path(kind)

    def _asset_source_path(self, kind: str) -> Path:
        manifest = _fixture_manifest(kind)
        relative = str(manifest["files"][0]["path"])
        return self._asset_archive_dir(kind) / relative

    def _resign_asset_archive(
        self,
        kind: str,
        mutate_source: Callable[[dict[str, object]], None],
        *,
        renamed_source: str | None = None,
    ) -> Path:
        archive_dir = self._asset_archive_dir(kind)
        manifest_path = archive_dir / ASSET_MANIFEST_FILENAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        descriptor = manifest["files"][0]
        source_path = archive_dir / descriptor["path"]
        source = json.loads(source_path.read_text(encoding="utf-8"))
        mutate_source(source)

        source["checksum"] = None
        source["checksum"] = canonical_checksum(source)
        source_bytes = (
            json.dumps(source, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if renamed_source is not None:
            source_path.unlink()
            source_path = archive_dir / renamed_source
            descriptor["path"] = renamed_source
        source_path.write_bytes(source_bytes)

        descriptor["size_bytes"] = len(source_bytes)
        descriptor["blob_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        descriptor["contract_checksum"] = source["checksum"]
        manifest["source_checksum"] = source["checksum"]
        manifest["total_size_bytes"] = len(source_bytes)

        payload = {
            key: value
            for key, value in manifest.items()
            if key
            not in {
                "archive_id",
                "archive_checksum",
                "archive_path",
                "manifest_checksum",
            }
        }
        archive_checksum = canonical_checksum(payload)
        archive_id = f"arc-{archive_checksum[:32]}"
        directory = {
            "person": "people",
            "keyword": "keywords",
            "scenario": "scenarios",
        }[kind]
        archive_path = (
            f"{directory}/{manifest['asset_id']}/versions/"
            f"v{manifest['version']:03d}/{archive_id}"
        )
        manifest["archive_id"] = archive_id
        manifest["archive_checksum"] = archive_checksum
        manifest["archive_path"] = archive_path
        manifest["manifest_checksum"] = None
        manifest["manifest_checksum"] = canonical_checksum(manifest)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        destination = self.repository / "assets" / Path(
            *PurePosixPath(archive_path).parts
        )
        if destination != archive_dir:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archive_dir), str(destination))
        return destination

    def test_course_and_all_asset_technical_fixtures_pass(self) -> None:
        self.assertEqual(validate_repository(self.repository), (4, 14))

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
        source_file = self._asset_source_path("person")
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
        extra_file = self._asset_archive_dir("person") / "extra.json"
        extra_file.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ValidationFailure, "file set differ"):
            validate_repository(self.repository)

    def test_asset_archive_at_wrong_path_is_rejected(self) -> None:
        original = self._asset_archive_dir("person")
        relative = _fixture_archive_path("person")
        wrong = original.parents[1] / "v002" / relative.name
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

    def test_resigned_asset_source_cannot_store_credentials(self) -> None:
        self._resign_asset_archive(
            "person",
            lambda source: source.__setitem__(
                "access_token",
                "not-a-real-token",
            ),
        )

        with self.assertRaisesRegex(
            ValidationFailure,
            "credential-like field is not allowed",
        ):
            validate_repository(self.repository)

    def test_resigned_unknown_source_field_is_rejected_by_source_schema(self) -> None:
        self._resign_asset_archive(
            "person",
            lambda source: source.__setitem__(
                "unsupported_future_field",
                "not in person-profile/v1",
            ),
        )

        with self.assertRaisesRegex(
            ValidationFailure,
            "Additional properties are not allowed",
        ):
            validate_repository(self.repository)

    def test_resigned_noncanonical_asset_filename_is_rejected(self) -> None:
        self._resign_asset_archive(
            "keyword",
            lambda _source: None,
            renamed_source="arbitrary.json",
        )

        with self.assertRaisesRegex(
            ValidationFailure,
            "asset source filename must be",
        ):
            validate_repository(self.repository)


if __name__ == "__main__":
    unittest.main()
