from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


COURSE_MANIFEST_FILENAME = "课程归档清单.json"
ASSET_MANIFEST_FILENAME = "内容资产归档清单.json"
POLICY_PATH = Path(".chronovita/repository-policy.json")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_FILES = 128
CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ARCHIVE_ID_PATTERN = re.compile(r"^arc-[0-9a-f]{32}$")
RELEASE_ID_PATTERN = re.compile(r"^rel-[0-9a-f]{10}-\d{4,}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
WINDOWS_RESERVED_NAMES = {
    "aux",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}
ASSET_KIND_RULES = {
    "person": {
        "directory": "people",
        "file_kind": "sealed-person",
        "schema_version": "person-profile/v1",
        "identity_field": "asset_id",
        "version_field": "version",
        "title_field": "name",
    },
    "keyword": {
        "directory": "keywords",
        "file_kind": "sealed-keyword",
        "schema_version": "keyword-profile/v1",
        "identity_field": "asset_id",
        "version_field": "version",
        "title_field": "word",
    },
    "scenario": {
        "directory": "scenarios",
        "file_kind": "sealed-scenario",
        "schema_version": "scenario-template/v1",
        "identity_field": "scenario_id",
        "version_field": "scenario_version",
        "title_field": "title",
    },
}


class ValidationFailure(Exception):
    pass


def canonical_checksum(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValidationFailure(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def parse_json_bytes(raw: bytes, *, field: str) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValidationFailure(f"{field}: UTF-8 BOM is not allowed")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except ValidationFailure as exc:
        raise ValidationFailure(f"{field}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure(f"{field}: invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationFailure(f"{field}: top-level JSON value must be an object")
    return value


def read_json(path: Path, *, max_bytes: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValidationFailure(f"{path}: JSON file exceeds {max_bytes} bytes")
    return parse_json_bytes(raw, field=str(path))


def safe_archive_path(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationFailure(f"{field}: path must be a string")
    if not value or value != value.strip() or len(value) > 512:
        raise ValidationFailure(f"{field}: invalid path length or surrounding space")
    if unicodedata.normalize("NFKC", value) != value:
        raise ValidationFailure(f"{field}: path must use NFKC-normalized Unicode")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise ValidationFailure(f"{field}: path must be relative POSIX syntax")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValidationFailure(f"{field}: path contains a control character")

    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValidationFailure(f"{field}: empty, dot or parent segment is not allowed")
    for part in parts:
        if (
            len(part) > 120
            or len(part.encode("utf-8")) > 240
            or len(part.encode("utf-16-le")) // 2 > 120
            or part != part.strip()
            or part.endswith((".", " "))
            or any(char in part for char in '<>:"|?*')
        ):
            raise ValidationFailure(f"{field}: path segment is not portable")
        stem = part.split(".", 1)[0].casefold()
        if stem in WINDOWS_RESERVED_NAMES or part.casefold() == ".git":
            raise ValidationFailure(f"{field}: path contains a reserved name")
    if PurePosixPath(*parts).as_posix() != value:
        raise ValidationFailure(f"{field}: path is not canonical")
    return value


def ensure_inside(root: Path, candidate: Path, *, field: str) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise ValidationFailure(
            f"{field}: file is missing or escapes the archive directory"
        ) from exc

    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValidationFailure(f"{field}: symbolic links are not allowed")
        current = current.parent
    return resolved


def walk_keys(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(walk_keys(item))
    return keys


def reject_credentials(value: object, *, field: str) -> None:
    forbidden_fragments = ("token", "secret", "private_key", "credential")
    for key in walk_keys(value):
        lowered = key.casefold()
        if any(fragment in lowered for fragment in forbidden_fragments):
            raise ValidationFailure(
                f"{field}: credential-like field is not allowed: {key!r}"
            )


def _load_contract_schema(
    repository: Path,
    policy_file: Path,
    policy: dict[str, Any],
    *,
    contract_field: str,
    expected_schema_version: str,
) -> dict[str, Any]:
    contract = policy.get(contract_field)
    if not isinstance(contract, dict):
        raise ValidationFailure(f"{policy_file}: {contract_field} must be an object")
    if contract.get("schema_version") != expected_schema_version:
        raise ValidationFailure(
            f"{policy_file}: {contract_field}.schema_version must be "
            f"{expected_schema_version!r}"
        )
    schema_relative = safe_archive_path(
        contract.get("schema_file"),
        field=f"{policy_file}: {contract_field}.schema_file",
    )
    schema_path = repository / Path(*PurePosixPath(schema_relative).parts)
    ensure_inside(
        repository,
        schema_path,
        field=f"{contract_field}.schema_file",
    )
    expected_schema_hash = contract.get("schema_sha256")
    if (
        not isinstance(expected_schema_hash, str)
        or not CHECKSUM_PATTERN.fullmatch(expected_schema_hash)
        or file_checksum(schema_path) != expected_schema_hash
    ):
        raise ValidationFailure(
            f"{policy_file}: {contract_field} schema SHA-256 mismatch"
        )
    if contract.get("source_repository") != "falling-feather/Chronovita":
        raise ValidationFailure(
            f"{policy_file}: {contract_field}.source_repository is invalid"
        )
    if contract.get("source_branch") != "class":
        raise ValidationFailure(
            f"{policy_file}: {contract_field}.source_branch must be 'class'"
        )
    source_commit = contract.get("source_commit")
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(
        source_commit
    ):
        raise ValidationFailure(
            f"{policy_file}: {contract_field}.source_commit must be a full Git SHA"
        )
    schema = read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return schema


def validate_policy(
    repository: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy_file = repository / POLICY_PATH
    policy = read_json(policy_file)
    expected_values = {
        "schema_version": "content-history-repository/v1",
        "visibility": "private",
        "default_branch": "main",
        "archive_root": "courses",
        "asset_archive_root": "assets",
        "default_publication_mode": "pull_request",
        "direct_commit_requires_confirmation": True,
    }
    for field, expected in expected_values.items():
        if policy.get(field) != expected:
            raise ValidationFailure(
                f"{policy_file}: {field} must be fixed to {expected!r}"
            )
    if not isinstance(policy.get("repository_id"), int) or policy["repository_id"] < 1:
        raise ValidationFailure(f"{policy_file}: repository_id must be positive")
    if policy.get("allowed_publication_modes") != [
        "pull_request",
        "direct_commit",
    ]:
        raise ValidationFailure(
            f"{policy_file}: publication mode set or order is invalid"
        )
    reject_credentials(policy, field=str(policy_file))

    course_schema = _load_contract_schema(
        repository,
        policy_file,
        policy,
        contract_field="contract",
        expected_schema_version="course-archive/v1",
    )
    asset_schema = _load_contract_schema(
        repository,
        policy_file,
        policy,
        contract_field="asset_contract",
        expected_schema_version="content-asset-archive/v1",
    )
    return policy, course_schema, asset_schema


def validate_manifest_schema(
    manifest: dict[str, Any],
    manifest_path: Path,
    schema: dict[str, Any],
) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.path) or "<root>"
    raise ValidationFailure(
        f"{manifest_path}: schema validation failed at {location}: {first.message}"
    )


def _validate_archive_identity_fields(
    manifest: dict[str, Any],
    manifest_path: Path,
) -> tuple[str, str]:
    archive_id = manifest.get("archive_id")
    archive_checksum = manifest.get("archive_checksum")
    if not isinstance(archive_id, str) or not ARCHIVE_ID_PATTERN.fullmatch(archive_id):
        raise ValidationFailure(f"{manifest_path}: invalid archive_id")
    if (
        not isinstance(archive_checksum, str)
        or not CHECKSUM_PATTERN.fullmatch(archive_checksum)
    ):
        raise ValidationFailure(f"{manifest_path}: invalid archive_checksum")

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
    expected_archive_checksum = canonical_checksum(payload)
    if archive_checksum != expected_archive_checksum:
        raise ValidationFailure(f"{manifest_path}: archive_checksum mismatch")
    expected_archive_id = f"arc-{expected_archive_checksum[:32]}"
    if archive_id != expected_archive_id:
        raise ValidationFailure(
            f"{manifest_path}: archive_id is not derived from archive_checksum"
        )

    manifest_copy = json.loads(json.dumps(manifest, ensure_ascii=False))
    manifest_copy["manifest_checksum"] = None
    if manifest.get("manifest_checksum") != canonical_checksum(manifest_copy):
        raise ValidationFailure(f"{manifest_path}: manifest_checksum mismatch")
    return archive_id, archive_checksum


def validate_course_manifest_identity(
    manifest: dict[str, Any],
    manifest_path: Path,
    archive_root: Path,
) -> None:
    if manifest.get("schema_version") != "course-archive/v1":
        raise ValidationFailure(
            f"{manifest_path}: schema_version must be 'course-archive/v1'"
        )
    archive_id, _archive_checksum = _validate_archive_identity_fields(
        manifest,
        manifest_path,
    )
    release_id = manifest.get("release_id")
    release_no = manifest.get("release_no")
    if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ValidationFailure(f"{manifest_path}: invalid release_id")
    if not isinstance(release_no, int) or release_no < 1:
        raise ValidationFailure(f"{manifest_path}: invalid release_no")
    if int(release_id.rsplit("-", 1)[1]) != release_no:
        raise ValidationFailure(f"{manifest_path}: release_id and release_no differ")

    course_id = manifest.get("course_id")
    expected_archive_path = f"{course_id}/releases/{release_id}/{archive_id}"
    if manifest.get("archive_path") != expected_archive_path:
        raise ValidationFailure(
            f"{manifest_path}: archive_path violates the course directory contract"
        )
    actual_archive_path = manifest_path.parent.relative_to(archive_root).as_posix()
    if actual_archive_path != expected_archive_path:
        raise ValidationFailure(
            f"{manifest_path}: manifest location and archive_path differ"
        )


def validate_asset_manifest_identity(
    manifest: dict[str, Any],
    manifest_path: Path,
    archive_root: Path,
) -> dict[str, str]:
    if manifest.get("schema_version") != "content-asset-archive/v1":
        raise ValidationFailure(
            f"{manifest_path}: schema_version must be 'content-asset-archive/v1'"
        )
    archive_id, _archive_checksum = _validate_archive_identity_fields(
        manifest,
        manifest_path,
    )
    asset_kind = manifest.get("asset_kind")
    rules = ASSET_KIND_RULES.get(str(asset_kind))
    if rules is None:
        raise ValidationFailure(f"{manifest_path}: invalid asset_kind")
    asset_id = manifest.get("asset_id")
    version = manifest.get("version")
    if not isinstance(asset_id, str) or not ASSET_ID_PATTERN.fullmatch(asset_id):
        raise ValidationFailure(f"{manifest_path}: invalid asset_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValidationFailure(f"{manifest_path}: invalid version")

    expected_archive_path = (
        f"{rules['directory']}/{asset_id}/versions/v{version:03d}/{archive_id}"
    )
    if manifest.get("archive_path") != expected_archive_path:
        raise ValidationFailure(
            f"{manifest_path}: archive_path violates the asset directory contract"
        )
    actual_archive_path = manifest_path.parent.relative_to(archive_root).as_posix()
    if actual_archive_path != expected_archive_path:
        raise ValidationFailure(
            f"{manifest_path}: manifest location and archive_path differ"
        )
    return rules


def validate_archive_files(
    manifest: dict[str, Any],
    manifest_path: Path,
    *,
    manifest_filename: str,
    allowed_media_types: set[str],
) -> dict[str, Path]:
    archive_dir = manifest_path.parent
    file_items = manifest.get("files")
    if not isinstance(file_items, list) or not file_items:
        raise ValidationFailure(f"{manifest_path}: files must be a non-empty array")
    if len(file_items) > MAX_ARCHIVE_FILES:
        raise ValidationFailure(f"{manifest_path}: too many archive files")
    if manifest.get("file_count") != len(file_items):
        raise ValidationFailure(f"{manifest_path}: file_count differs from files")

    descriptor_paths: list[str] = []
    validated_files = {manifest_path.resolve()}
    resolved_by_path: dict[str, Path] = {}
    total_size = 0
    for index, item in enumerate(file_items):
        if not isinstance(item, dict):
            raise ValidationFailure(
                f"{manifest_path}: files[{index}] must be an object"
            )
        relative = safe_archive_path(
            item.get("path"),
            field=f"{manifest_path}: files[{index}].path",
        )
        if relative == manifest_filename:
            raise ValidationFailure(f"{manifest_path}: manifest cannot describe itself")
        descriptor_paths.append(relative)
        candidate = archive_dir / Path(*PurePosixPath(relative).parts)
        resolved = ensure_inside(
            archive_dir,
            candidate,
            field=f"{manifest_path}: {relative}",
        )
        if not resolved.is_file():
            raise ValidationFailure(
                f"{manifest_path}: {relative} is not a regular file"
            )

        size = resolved.stat().st_size
        expected_size = item.get("size_bytes")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 1
            or expected_size > MAX_ARCHIVE_FILE_BYTES
            or size != expected_size
        ):
            raise ValidationFailure(
                f"{manifest_path}: {relative} size mismatch or limit exceeded"
            )
        expected_hash = item.get("blob_sha256")
        if (
            not isinstance(expected_hash, str)
            or not CHECKSUM_PATTERN.fullmatch(expected_hash)
            or file_checksum(resolved) != expected_hash
        ):
            raise ValidationFailure(f"{manifest_path}: {relative} SHA-256 mismatch")

        media_type = item.get("media_type")
        if media_type not in allowed_media_types:
            raise ValidationFailure(
                f"{manifest_path}: {relative} has unsupported media type"
            )
        raw = resolved.read_bytes()
        if media_type == "application/json":
            payload = parse_json_bytes(raw, field=f"{manifest_path}: {relative}")
            reject_credentials(payload, field=f"{manifest_path}: {relative}")
        else:
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValidationFailure(
                    f"{manifest_path}: {relative} is not valid UTF-8 text"
                ) from exc

        total_size += size
        validated_files.add(resolved)
        resolved_by_path[relative] = resolved

    expected_order = sorted(descriptor_paths, key=str.casefold)
    if descriptor_paths != expected_order:
        raise ValidationFailure(f"{manifest_path}: file descriptors must be sorted")
    canonical_paths = [
        unicodedata.normalize("NFKC", value).casefold() for value in descriptor_paths
    ]
    if len(canonical_paths) != len(set(canonical_paths)):
        raise ValidationFailure(
            f"{manifest_path}: file paths collide by Unicode or case"
        )
    if total_size != manifest.get("total_size_bytes"):
        raise ValidationFailure(f"{manifest_path}: total_size_bytes mismatch")
    if total_size > MAX_ARCHIVE_TOTAL_BYTES:
        raise ValidationFailure(f"{manifest_path}: archive byte limit exceeded")

    actual_files = {
        path.resolve()
        for path in archive_dir.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != validated_files:
        missing = sorted(str(path) for path in validated_files - actual_files)
        extra = sorted(str(path) for path in actual_files - validated_files)
        raise ValidationFailure(
            f"{manifest_path}: manifest and file set differ; "
            f"missing={missing}; extra={extra}"
        )
    return resolved_by_path


def validate_asset_source(
    manifest: dict[str, Any],
    manifest_path: Path,
    rules: dict[str, str],
    resolved_files: dict[str, Path],
) -> None:
    file_items = manifest["files"]
    if manifest.get("file_count") != 1 or len(file_items) != 1:
        raise ValidationFailure(
            f"{manifest_path}: an asset archive must contain exactly one source file"
        )
    descriptor = file_items[0]
    relative = descriptor["path"]
    if "/" in relative:
        raise ValidationFailure(
            f"{manifest_path}: asset source must be a top-level JSON file"
        )
    if descriptor.get("kind") != rules["file_kind"]:
        raise ValidationFailure(f"{manifest_path}: asset file kind mismatch")
    if descriptor.get("media_type", "application/json") != "application/json":
        raise ValidationFailure(f"{manifest_path}: asset source must be JSON")
    source_schema_version = manifest.get("source_schema_version")
    if source_schema_version != rules["schema_version"]:
        raise ValidationFailure(
            f"{manifest_path}: source_schema_version does not match asset_kind"
        )
    if descriptor.get("schema_version") != source_schema_version:
        raise ValidationFailure(
            f"{manifest_path}: descriptor schema_version mismatch"
        )
    source_checksum = manifest.get("source_checksum")
    if descriptor.get("contract_checksum") != source_checksum:
        raise ValidationFailure(
            f"{manifest_path}: descriptor contract_checksum mismatch"
        )

    source_path = resolved_files[relative]
    source = read_json(source_path, max_bytes=MAX_ARCHIVE_FILE_BYTES)
    if source.get("schema_version") != source_schema_version:
        raise ValidationFailure(f"{manifest_path}: source schema_version mismatch")
    if source.get(rules["identity_field"]) != manifest.get("asset_id"):
        raise ValidationFailure(f"{manifest_path}: source identity mismatch")
    source_version = source.get(rules["version_field"])
    if (
        not isinstance(source_version, int)
        or isinstance(source_version, bool)
        or source_version != manifest.get("version")
    ):
        raise ValidationFailure(f"{manifest_path}: source version mismatch")
    if source.get(rules["title_field"]) != manifest.get("title"):
        raise ValidationFailure(f"{manifest_path}: source title mismatch")
    if source.get("status") != "sealed":
        raise ValidationFailure(f"{manifest_path}: source status must be sealed")
    if source.get("sealed_at") != manifest.get("sealed_at"):
        raise ValidationFailure(f"{manifest_path}: source sealed_at mismatch")
    if source.get("sealed_by") != manifest.get("sealed_by"):
        raise ValidationFailure(f"{manifest_path}: source sealed_by mismatch")
    if source.get("checksum") != source_checksum:
        raise ValidationFailure(f"{manifest_path}: source checksum field mismatch")
    source_copy = json.loads(json.dumps(source, ensure_ascii=False))
    source_copy["checksum"] = None
    if source_checksum != canonical_checksum(source_copy):
        raise ValidationFailure(f"{manifest_path}: source contract checksum mismatch")


def _validate_archive_root_files(
    archive_root: Path,
    owned_files: set[Path],
) -> int:
    symbolic_links = sorted(
        str(path) for path in archive_root.rglob("*") if path.is_symlink()
    )
    if symbolic_links:
        raise ValidationFailure(
            f"{archive_root}: symbolic links are not allowed: {symbolic_links}"
        )
    actual_content_files = {
        path.resolve()
        for path in archive_root.rglob("*")
        if (path.is_file() or path.is_symlink()) and path.name != ".gitkeep"
    }
    if actual_content_files != owned_files:
        extra = sorted(str(path) for path in actual_content_files - owned_files)
        missing = sorted(str(path) for path in owned_files - actual_content_files)
        raise ValidationFailure(
            f"{archive_root}: unowned archive files found; "
            f"missing={missing}; extra={extra}"
        )
    return len(actual_content_files)


def validate_repository(repository: Path) -> tuple[int, int]:
    repository = repository.resolve(strict=True)
    policy, course_schema, asset_schema = validate_policy(repository)
    course_root = repository / policy["archive_root"]
    asset_root = repository / policy["asset_archive_root"]
    for root in (course_root, asset_root):
        if not root.is_dir() or root.is_symlink():
            raise ValidationFailure(
                f"{root}: archive root is missing or is a symbolic link"
            )

    course_owned_files: set[Path] = set()
    course_count = 0
    for manifest_path in sorted(course_root.rglob(COURSE_MANIFEST_FILENAME)):
        ensure_inside(course_root, manifest_path, field=str(manifest_path))
        manifest = read_json(manifest_path)
        reject_credentials(manifest, field=str(manifest_path))
        validate_manifest_schema(manifest, manifest_path, course_schema)
        validate_course_manifest_identity(manifest, manifest_path, course_root)
        resolved_files = validate_archive_files(
            manifest,
            manifest_path,
            manifest_filename=COURSE_MANIFEST_FILENAME,
            allowed_media_types={
                "application/json",
                "text/markdown; charset=utf-8",
                "text/html; charset=utf-8",
            },
        )
        course_owned_files.add(manifest_path.resolve())
        course_owned_files.update(resolved_files.values())
        course_count += 1

    asset_owned_files: set[Path] = set()
    asset_count = 0
    for manifest_path in sorted(asset_root.rglob(ASSET_MANIFEST_FILENAME)):
        ensure_inside(asset_root, manifest_path, field=str(manifest_path))
        manifest = read_json(manifest_path)
        reject_credentials(manifest, field=str(manifest_path))
        validate_manifest_schema(manifest, manifest_path, asset_schema)
        rules = validate_asset_manifest_identity(manifest, manifest_path, asset_root)
        resolved_files = validate_archive_files(
            manifest,
            manifest_path,
            manifest_filename=ASSET_MANIFEST_FILENAME,
            allowed_media_types={"application/json"},
        )
        validate_asset_source(manifest, manifest_path, rules, resolved_files)
        asset_owned_files.add(manifest_path.resolve())
        asset_owned_files.update(resolved_files.values())
        asset_count += 1

    file_count = _validate_archive_root_files(
        course_root,
        course_owned_files,
    ) + _validate_archive_root_files(asset_root, asset_owned_files)
    return course_count + asset_count, file_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the Chronovita private course and content-asset "
            "history repository."
        )
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to the current working directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        archive_count, file_count = validate_repository(args.repository)
    except (ValidationFailure, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "Chronovita content validation passed: "
        f"{archive_count} archive(s), {file_count} tracked archive file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
