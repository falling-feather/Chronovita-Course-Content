from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator


MANIFEST_FILENAME = "课程归档清单.json"
POLICY_PATH = Path(".chronovita/repository-policy.json")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_FILES = 128
CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ARCHIVE_ID_PATTERN = re.compile(r"^arc-[0-9a-f]{32}$")
RELEASE_ID_PATTERN = re.compile(r"^rel-[0-9a-f]{10}-\d{4,}$")
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


def read_json(path: Path, *, max_bytes: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValidationFailure(f"{path}: JSON 文件超过 {max_bytes} 字节")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValidationFailure(f"{path}: JSON 文件不得包含 UTF-8 BOM")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure(f"{path}: 不是有效的 UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationFailure(f"{path}: JSON 顶层必须是对象")
    return value


def safe_archive_path(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationFailure(f"{field}: 必须是字符串")
    if not value or value != value.strip() or len(value) > 512:
        raise ValidationFailure(f"{field}: 路径长度或首尾空白不合法")
    if unicodedata.normalize("NFKC", value) != value:
        raise ValidationFailure(f"{field}: 路径必须使用 NFKC 规范化 Unicode")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise ValidationFailure(f"{field}: 必须是相对 POSIX 路径")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValidationFailure(f"{field}: 路径不得包含控制字符")

    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValidationFailure(f"{field}: 路径不得包含空、点或父级片段")
    for part in parts:
        if (
            len(part) > 120
            or len(part.encode("utf-8")) > 240
            or len(part.encode("utf-16-le")) // 2 > 120
            or part != part.strip()
            or part.endswith((".", " "))
            or any(char in part for char in '<>:"|?*')
        ):
            raise ValidationFailure(f"{field}: 路径片段不具备跨平台可移植性")
        stem = part.split(".", 1)[0].casefold()
        if stem in WINDOWS_RESERVED_NAMES or part.casefold() == ".git":
            raise ValidationFailure(f"{field}: 路径包含保留名称")
    if PurePosixPath(*parts).as_posix() != value:
        raise ValidationFailure(f"{field}: 路径不是规范形式")
    return value


def ensure_inside(root: Path, candidate: Path, *, field: str) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise ValidationFailure(f"{field}: 文件不存在或逃逸归档目录") from exc

    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValidationFailure(f"{field}: 归档中禁止符号链接")
        current = current.parent
    return resolved


def validate_policy(repository: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    policy_file = repository / POLICY_PATH
    policy = read_json(policy_file)
    expected_values = {
        "schema_version": "content-history-repository/v1",
        "visibility": "private",
        "default_branch": "main",
        "archive_root": "courses",
        "default_publication_mode": "pull_request",
        "direct_commit_requires_confirmation": True,
    }
    for field, expected in expected_values.items():
        if policy.get(field) != expected:
            raise ValidationFailure(
                f"{policy_file}: {field} 必须固定为 {expected!r}"
            )
    if not isinstance(policy.get("repository_id"), int) or policy["repository_id"] < 1:
        raise ValidationFailure(f"{policy_file}: repository_id 必须是正整数")
    if policy.get("allowed_publication_modes") != [
        "pull_request",
        "direct_commit",
    ]:
        raise ValidationFailure(f"{policy_file}: 发布模式顺序或集合不合法")

    forbidden_fragments = ("token", "secret", "private_key", "credential")
    for key in walk_keys(policy):
        lowered = key.casefold()
        if any(fragment in lowered for fragment in forbidden_fragments):
            raise ValidationFailure(f"{policy_file}: 策略文件不得包含凭据字段 {key!r}")

    contract = policy.get("contract")
    if not isinstance(contract, dict):
        raise ValidationFailure(f"{policy_file}: contract 必须是对象")
    if contract.get("schema_version") != "course-archive/v1":
        raise ValidationFailure(f"{policy_file}: 仅允许 course-archive/v1")
    schema_relative = safe_archive_path(
        contract.get("schema_file"),
        field=f"{policy_file}: contract.schema_file",
    )
    schema_path = repository / Path(*PurePosixPath(schema_relative).parts)
    ensure_inside(repository, schema_path, field="contract.schema_file")
    expected_schema_hash = contract.get("schema_sha256")
    if (
        not isinstance(expected_schema_hash, str)
        or not CHECKSUM_PATTERN.fullmatch(expected_schema_hash)
        or file_checksum(schema_path) != expected_schema_hash
    ):
        raise ValidationFailure(f"{policy_file}: Schema SHA-256 不匹配")
    schema = read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return policy, schema_path, schema


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


def validate_manifest_schema(
    manifest: dict[str, Any],
    manifest_path: Path,
    schema: dict[str, Any],
) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda item: list(item.path))
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.path) or "<root>"
    raise ValidationFailure(
        f"{manifest_path}: Schema 校验失败于 {location}: {first.message}"
    )


def validate_manifest_identity(
    manifest: dict[str, Any],
    manifest_path: Path,
    archive_root: Path,
) -> None:
    archive_id = manifest.get("archive_id")
    archive_checksum = manifest.get("archive_checksum")
    release_id = manifest.get("release_id")
    release_no = manifest.get("release_no")
    if not isinstance(archive_id, str) or not ARCHIVE_ID_PATTERN.fullmatch(archive_id):
        raise ValidationFailure(f"{manifest_path}: archive_id 不合法")
    if (
        not isinstance(archive_checksum, str)
        or not CHECKSUM_PATTERN.fullmatch(archive_checksum)
    ):
        raise ValidationFailure(f"{manifest_path}: archive_checksum 不合法")
    if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ValidationFailure(f"{manifest_path}: release_id 不合法")
    if not isinstance(release_no, int) or release_no < 1:
        raise ValidationFailure(f"{manifest_path}: release_no 不合法")
    if int(release_id.rsplit("-", 1)[1]) != release_no:
        raise ValidationFailure(f"{manifest_path}: release_id 序号与 release_no 不一致")

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
        raise ValidationFailure(f"{manifest_path}: archive_checksum 不匹配")
    expected_archive_id = f"arc-{expected_archive_checksum[:32]}"
    if archive_id != expected_archive_id:
        raise ValidationFailure(f"{manifest_path}: archive_id 未从归档校验和派生")

    course_id = manifest.get("course_id")
    expected_archive_path = f"{course_id}/releases/{release_id}/{archive_id}"
    if manifest.get("archive_path") != expected_archive_path:
        raise ValidationFailure(f"{manifest_path}: archive_path 不符合稳定目录规则")
    actual_archive_path = manifest_path.parent.relative_to(archive_root).as_posix()
    if actual_archive_path != expected_archive_path:
        raise ValidationFailure(f"{manifest_path}: 清单所在目录与 archive_path 不一致")

    manifest_copy = json.loads(json.dumps(manifest, ensure_ascii=False))
    manifest_copy["manifest_checksum"] = None
    if manifest.get("manifest_checksum") != canonical_checksum(manifest_copy):
        raise ValidationFailure(f"{manifest_path}: manifest_checksum 不匹配")


def validate_archive_files(
    manifest: dict[str, Any],
    manifest_path: Path,
) -> set[Path]:
    archive_dir = manifest_path.parent
    file_items = manifest.get("files")
    if not isinstance(file_items, list) or not file_items:
        raise ValidationFailure(f"{manifest_path}: files 必须是非空数组")
    if len(file_items) > MAX_ARCHIVE_FILES:
        raise ValidationFailure(f"{manifest_path}: 归档文件数量超限")
    if manifest.get("file_count") != len(file_items):
        raise ValidationFailure(f"{manifest_path}: file_count 与 files 长度不一致")

    descriptor_paths: list[str] = []
    validated_files = {manifest_path.resolve()}
    total_size = 0
    for index, item in enumerate(file_items):
        if not isinstance(item, dict):
            raise ValidationFailure(f"{manifest_path}: files[{index}] 必须是对象")
        relative = safe_archive_path(
            item.get("path"),
            field=f"{manifest_path}: files[{index}].path",
        )
        if relative == MANIFEST_FILENAME:
            raise ValidationFailure(f"{manifest_path}: 清单不得描述自身")
        descriptor_paths.append(relative)
        candidate = archive_dir / Path(*PurePosixPath(relative).parts)
        resolved = ensure_inside(
            archive_dir,
            candidate,
            field=f"{manifest_path}: {relative}",
        )
        if not resolved.is_file():
            raise ValidationFailure(f"{manifest_path}: {relative} 不是普通文件")

        size = resolved.stat().st_size
        expected_size = item.get("size_bytes")
        if (
            not isinstance(expected_size, int)
            or expected_size < 1
            or expected_size > MAX_ARCHIVE_FILE_BYTES
            or size != expected_size
        ):
            raise ValidationFailure(f"{manifest_path}: {relative} 字节数不匹配或超限")
        expected_hash = item.get("blob_sha256")
        if (
            not isinstance(expected_hash, str)
            or not CHECKSUM_PATTERN.fullmatch(expected_hash)
            or file_checksum(resolved) != expected_hash
        ):
            raise ValidationFailure(f"{manifest_path}: {relative} SHA-256 不匹配")

        media_type = item.get("media_type")
        raw = resolved.read_bytes()
        if media_type == "application/json":
            try:
                json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationFailure(
                    f"{manifest_path}: {relative} 不是有效 UTF-8 JSON"
                ) from exc
        elif media_type in {
            "text/markdown; charset=utf-8",
            "text/html; charset=utf-8",
        }:
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValidationFailure(
                    f"{manifest_path}: {relative} 不是有效 UTF-8 文本"
                ) from exc
        else:
            raise ValidationFailure(f"{manifest_path}: {relative} 媒体类型不受支持")

        total_size += size
        validated_files.add(resolved)

    expected_order = sorted(descriptor_paths, key=str.casefold)
    if descriptor_paths != expected_order:
        raise ValidationFailure(f"{manifest_path}: 文件描述必须按路径排序")
    canonical_paths = [
        unicodedata.normalize("NFKC", value).casefold() for value in descriptor_paths
    ]
    if len(canonical_paths) != len(set(canonical_paths)):
        raise ValidationFailure(f"{manifest_path}: 文件路径存在 Unicode/大小写碰撞")
    if total_size != manifest.get("total_size_bytes"):
        raise ValidationFailure(f"{manifest_path}: total_size_bytes 不匹配")
    if total_size > MAX_ARCHIVE_TOTAL_BYTES:
        raise ValidationFailure(f"{manifest_path}: 归档总字节数超限")

    actual_files = {
        path.resolve()
        for path in archive_dir.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != validated_files:
        missing = sorted(str(path) for path in validated_files - actual_files)
        extra = sorted(str(path) for path in actual_files - validated_files)
        raise ValidationFailure(
            f"{manifest_path}: 清单与实际文件集合不一致；缺失={missing}；额外={extra}"
        )
    return validated_files


def validate_repository(repository: Path) -> tuple[int, int]:
    repository = repository.resolve(strict=True)
    policy, _schema_path, schema = validate_policy(repository)
    archive_root = repository / policy["archive_root"]
    if not archive_root.is_dir() or archive_root.is_symlink():
        raise ValidationFailure(f"{archive_root}: 归档根目录不存在或是符号链接")

    manifests = sorted(archive_root.rglob(MANIFEST_FILENAME))
    owned_files: set[Path] = set()
    archive_count = 0
    for manifest_path in manifests:
        manifest = read_json(manifest_path)
        validate_manifest_schema(manifest, manifest_path, schema)
        validate_manifest_identity(manifest, manifest_path, archive_root)
        owned_files.update(validate_archive_files(manifest, manifest_path))
        archive_count += 1

    actual_content_files = {
        path.resolve()
        for path in archive_root.rglob("*")
        if (path.is_file() or path.is_symlink()) and path.name != ".gitkeep"
    }
    if actual_content_files != owned_files:
        extra = sorted(str(path) for path in actual_content_files - owned_files)
        missing = sorted(str(path) for path in owned_files - actual_content_files)
        raise ValidationFailure(
            f"{archive_root}: 存在未归档文件；缺失={missing}；额外={extra}"
        )
    return archive_count, len(actual_content_files)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Chronovita private course-content history repository."
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
