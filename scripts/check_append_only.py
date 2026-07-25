from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ARCHIVE_ROOTS = ("courses/", "assets/")


class AppendOnlyFailure(Exception):
    pass


def parse_name_status(raw: bytes) -> list[tuple[str, str]]:
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise AppendOnlyFailure("git diff returned malformed name-status data")

    entries: list[tuple[str, str]] = []
    for index in range(0, len(parts), 2):
        try:
            status = parts[index].decode("ascii")
            path = parts[index + 1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppendOnlyFailure(
                "git diff returned a non-UTF-8 archive path"
            ) from exc
        entries.append((status, path))
    return entries


def changed_archive_entries(
    repository: Path,
    base: str,
    head: str,
) -> list[tuple[str, str]]:
    command = [
        "git",
        "-C",
        str(repository),
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        base,
        head,
        "--",
        "courses",
        "assets",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise AppendOnlyFailure(f"cannot compare Git revisions: {message}") from exc
    return parse_name_status(completed.stdout)


def validate_append_only(entries: list[tuple[str, str]]) -> None:
    rejected = [
        (status, path)
        for status, path in entries
        if status != "A" or not path.startswith(ARCHIVE_ROOTS)
    ]
    if rejected:
        details = ", ".join(f"{status} {path}" for status, path in rejected)
        raise AppendOnlyFailure(
            "immutable archive history can only add new files; "
            f"rejected changes: {details}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject modifications or deletions of archived content."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="Base Git commit SHA.")
    parser.add_argument("--head", required=True, help="Head Git commit SHA.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        entries = changed_archive_entries(
            args.repository.resolve(strict=True),
            args.base,
            args.head,
        )
        validate_append_only(entries)
    except (AppendOnlyFailure, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "Chronovita append-only validation passed: "
        f"{len(entries)} new archive file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
