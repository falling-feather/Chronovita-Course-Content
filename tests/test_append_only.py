from __future__ import annotations

import unittest

from scripts.check_append_only import (
    AppendOnlyFailure,
    parse_name_status,
    validate_append_only,
)


class AppendOnlyValidatorTests(unittest.TestCase):
    def test_new_course_and_asset_files_are_allowed(self) -> None:
        entries = [
            ("A", "courses/C-sample/releases/rel-sample/file.json"),
            ("A", "assets/people/person-sample/versions/v001/file.json"),
        ]
        validate_append_only(entries)

    def test_existing_archive_modification_is_rejected(self) -> None:
        with self.assertRaisesRegex(AppendOnlyFailure, "M courses/"):
            validate_append_only([("M", "courses/C-sample/archive.json")])

    def test_existing_archive_deletion_is_rejected(self) -> None:
        with self.assertRaisesRegex(AppendOnlyFailure, "D assets/"):
            validate_append_only([("D", "assets/people/person.json")])

    def test_nul_delimited_git_output_is_parsed_without_path_loss(self) -> None:
        raw = (
            b"A\0assets/keywords/keyword-sample/file.json\0"
            b"M\0courses/C-sample/file.json\0"
        )
        self.assertEqual(
            parse_name_status(raw),
            [
                ("A", "assets/keywords/keyword-sample/file.json"),
                ("M", "courses/C-sample/file.json"),
            ],
        )

    def test_malformed_git_output_is_rejected(self) -> None:
        with self.assertRaisesRegex(AppendOnlyFailure, "malformed"):
            parse_name_status(b"A\0")


if __name__ == "__main__":
    unittest.main()
