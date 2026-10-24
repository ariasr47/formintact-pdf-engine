"""Create and validate the canonical pdf-translate release identity.

The independently selected release pin authenticates the candidate record.
Git owner bytes are checked when available; archive/container installs still
have to match the same pin, installed modules, metadata and active MIT bytes.
"""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import subprocess
import sys
import tarfile
import tomllib
from urllib.parse import urlsplit
from urllib.request import url2pathname
import zipfile
from pathlib import Path
from typing import Any

ORIGIN_REVISION = "d392c5d0f1bd9f9312879dcd4dc4336d96201bb7"
RELOCATION_RELEASE = {
    "scope": "relocation", "version": "89.0.0",
    "release_id": "pdf-translate-89.0.0-relocation",
    "adopted_owner_revision": "44e5370de6ead8f23cb67c1d36d92f3d9bb55cf6",
    "source_digest": "c4b3a6b49a936b1ea52dedff0ba8c45fa688fcd3719455c2c75229c748997d17",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 26,
}
# Root selected this tuple from committed B5 owner blobs, independently of
# provenance/release.json. Origin d392 remains ancestry, not the adopted owner.
RETENTION_RELEASE = {
    "scope": "behavior-release", "version": "89.0.1",
    "release_id": "pdf-translate-89.0.1-retention-form-repair",
    "adopted_owner_revision": "88a5c50a6484967ee7762af146a5d3c08018c47c",
    "source_digest": "68d4d835475b6de7b34415bd16a98691842a9f2692709a116ab64e7b968728df",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# Rodrigo approved the successor; derive this pin from the distinct committed
# source owner before writing any candidate record or distribution artifact.
OVERRIDE_RELEASE = {
    "scope": "behavior-release", "version": "89.0.2",
    "release_id": "pdf-translate-89.0.2-native-override-repair",
    "adopted_owner_revision": "c1636cd3b666f19cb65a828873ff0c21d712a91e",
    "source_digest": "3500513cbd38a9a2507a0025b63f2c5dfa2b8563cb73d0c267f877f1eb687272",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# The same 89.0.3 module bytes verified at f457ac49 are adopted on the backend
# branch at its actual immutable source-owner commit. No ancestry check is relaxed.
FINAL_LINE_ADVANCE_RELEASE = {
    "scope": "behavior-release", "version": "89.0.3",
    "release_id": "pdf-translate-89.0.3-reportlab-final-line-advance",
    "adopted_owner_revision": "a5d4323c36688a21d94d995cef82a44356270a9e",
    "source_digest": "8d9a529b8bf7462cea36989c85b7da31e3e544e917a3dd8cefe083b11c8f9044",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-08 whole-codebase review fixes F4 and F15: a tooltip-only rewrite keeps
# checkbox appearance streams, and an optional ink envelope never refuses a job.
# Only strip_text.py and source_retention.py differ from 89.0.3. The pin was derived
# from the committed owner tree before the candidate record or any artifact was written.
REVIEW_FIXES_RELEASE = {
    "scope": "behavior-release", "version": "89.0.4",
    "release_id": "pdf-translate-89.0.4-review-fixes",
    "adopted_owner_revision": "b9059acf9981a0fddd863e5e80cdc0831d53e916",
    "source_digest": "452730b9c265d2b633df8e3bdae3347f7785c73e23d5910229ccee0a833a322e",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-11 dogfood fixes (prompt-fit-and-bold L1): unvouched identity runs are
# redrawn, pass-through runs fall back to the role face, unmapped text keeps its
# source glyphs, a leading unmapped bullet passes through alone, and bold reads the
# font weight with bold runs reported. extract_segments, mapping, retypeset,
# source_retention and typography differ from 89.0.4. The pin was derived from the
# committed owner tree before the candidate record or any artifact was written.
DOGFOOD_FIXES_RELEASE = {
    "scope": "behavior-release", "version": "89.0.5",
    "release_id": "pdf-translate-89.0.5-dogfood-fixes",
    "adopted_owner_revision": "8c77b9380d82ddcbf77956af507359587696347f",
    "source_digest": "1f39024756a8604202fb4b623ca5303993ea56727b53439fc1787b3f9cf1b2ac",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-13 cap-line fix (staging order review): a line of short letters only may
# rise to its face's measured cap line instead of its own x-height ink top, so a
# translation with a capital, ascender or accent is no longer shrunk below the
# floor. Only source_retention differs from 89.0.5. The pin was derived from the
# committed owner tree before the candidate record or any artifact was written.
CAP_LINE_RELEASE = {
    "scope": "behavior-release", "version": "89.0.6",
    "release_id": "pdf-translate-89.0.6-cap-line",
    "adopted_owner_revision": "0ae755cf2858e9b0e187bac318baa3177b59b59e",
    "source_digest": "7ab3ef4ad29c9d4fd0eba1e19b477786f9e3e50c94840d1ca86000f47a937403",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-13 answer-area fix (W-4 fidelity review): a line that mixes bold and
# regular text stops at the drawn rules and controls beside its caption, as a
# plain line already did, so it no longer writes into a drawn answer cell whose
# widget covers only the cell's bottom. Only retypeset differs from 89.0.6. The pin
# was derived from the committed owner tree before the candidate record or any
# artifact was written.
ANSWER_AREA_RELEASE = {
    "scope": "behavior-release", "version": "89.0.7",
    "release_id": "pdf-translate-89.0.7-answer-area",
    "adopted_owner_revision": "6b8d4919497dd9f577ba84cc0546ef48a01db92a",
    "source_digest": "8c77894e353f67eb3a15fc2542cb0be9b4269027d19ff6f4a0d918617cb6bfdd",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-13 marker weight and paragraph scale (W-4 fidelity gaps 2 and 3): a list
# marker takes the weight of its own source characters and, on a mixed-weight line,
# its body's scale; `scale_caps` lets the engine start each line of a paragraph at
# the paragraph's smallest scale. Only retypeset differs from 89.0.7. The pin was
# derived from the committed owner tree before the candidate record or any artifact
# was written.
MARKER_AND_SCALE_RELEASE = {
    "scope": "behavior-release", "version": "89.0.8",
    "release_id": "pdf-translate-89.0.8-marker-and-scale",
    "adopted_owner_revision": "9d1af9183f340114283d9ac9aad975ef060d5b12",
    "source_digest": "87f263da96a6d40a6fe2cd509072660f8cf6424c853fbfecc8134c0810c27557",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-23 narrower widths (fitting ladder step 3): a `widths` mapping key draws a
# line in FormIntact Sans SemiCondensed or Condensed before the engine keeps it in the
# source language; lines without a width draw exactly as before. Only retypeset
# differs from 89.0.8. The pin was derived from the committed owner tree before the
# candidate record or any artifact was written.
NARROW_WIDTHS_RELEASE = {
    "scope": "behavior-release", "version": "89.0.9",
    "release_id": "pdf-translate-89.0.9-narrow-widths",
    "adopted_owner_revision": "657c65b590ccd28495ee7984f30562f30fbb5ce0",
    "source_digest": "77a1e151601796de8d639ee73277eded3710889d791dd49f0f76feb15e66becd",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-23 field labels (fitting ladder release 2): a printed checkbox in a dingbat
# font is its own drawing, replayed from its source glyphs; a line that starts with
# spaces under a field starts at its first letter; underscore blanks are leaders, and a
# field over a label's leaders caps the label's room. extract_segments and retypeset
# differ from 89.0.9. The pin was derived from the committed owner tree before the
# candidate record or any artifact was written.
FIELD_LABELS_RELEASE = {
    "scope": "behavior-release", "version": "89.0.10",
    "release_id": "pdf-translate-89.0.10-field-labels",
    "adopted_owner_revision": "e6191eb833828ceb4ad019794d36b5ac9071f108",
    "source_digest": "9efd13d15741ed6aa2c3fbd4a92c629786e35f6dcadfa52f4485d05fcbc6c5a7",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
# 2026-10-24 watermark length: extract records a rotated run's source length, and a
# rotated run printed over horizontal text (a stamp or watermark) keeps to it, so its
# translation never draws a larger box than the source. extract_segments and
# retypeset differ from 89.0.10. The pin was derived from the committed owner tree
# before the candidate record or any artifact was written.
ADOPTED_RELEASE = {
    "scope": "behavior-release", "version": "89.0.11",
    "release_id": "pdf-translate-89.0.11-watermark-length",
    "adopted_owner_revision": "57cfb97a38f177ca40456fd8bc01ddb4adaf9d83",
    "source_digest": "0be7c83938243f8caff8d5c7f20a63a6dc3795ead102bdcd2a5bd51302eb35b9",
    "license_digest": "1bf3136c754246a00a7ecd2591b7ffad12be6ff621333c6828a9ddf0db572a3f",
    "modules": 27,
}
DEFAULT_VERSION = ADOPTED_RELEASE["version"]
MODULE_COUNTS = {"89.0.0": 26, "89.0.1": 27, "89.0.2": 27, "89.0.3": 27, "89.0.4": 27,
                 "89.0.5": 27, "89.0.6": 27, "89.0.7": 27, "89.0.8": 27, "89.0.9": 27,
                 "89.0.10": 27, "89.0.11": 27}
PACKAGE_PREFIX = "libs/pdf-translate/pdf_translate/"
PACKAGE_NAME = "pdf-translate"
RECORD_KEYS = {
    "schema_version", "release_id", "scope", "version", "origin_revision",
    "adopted_owner_revision", "source_digest", "license_digest", "files",
}


class ProvenanceError(ValueError):
    """The release identity or installed package does not match its owner."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=root, check=True,
                                capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProvenanceError(f"Git metadata check failed: git {' '.join(args)}") from exc
    return result.stdout.strip()


def _git_blob(root: Path, revision: str, path: str) -> bytes:
    try:
        result = subprocess.run(["git", "cat-file", "blob", f"{revision}:{path}"],
                                cwd=root, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProvenanceError(f"owner blob is missing: {path}") from exc
    return result.stdout


def _repo_root(library: Path) -> Path:
    try:
        return Path(_git(library, "rev-parse", "--show-toplevel")).resolve()
    except ProvenanceError:
        raise


def _inventory_from_git(repo: Path, owner_revision: str, expected_count: int) -> list[dict[str, Any]]:
    output = _git(repo, "ls-tree", "-r", "--full-tree", owner_revision, "--", PACKAGE_PREFIX)
    files: list[dict[str, Any]] = []
    for line in output.splitlines():
        header, path = line.split("\t", 1)
        mode, kind, blob = header.split()
        if kind != "blob" or not path.endswith(".py"):
            raise ProvenanceError(f"package tree contains a non-Python file: {path}")
        relative = path.removeprefix(PACKAGE_PREFIX)
        data = _git_blob(repo, owner_revision, path)
        files.append({"path": f"pdf_translate/{relative}", "size": len(data),
                      "sha256": _sha256_bytes(data), "mode": mode, "blob": blob})
    files.sort(key=lambda item: item["path"])
    if len(files) != expected_count or len({item["path"] for item in files}) != expected_count:
        raise ProvenanceError(f"owner package tree must contain exactly {expected_count} Python files, got {len(files)}")
    return files


def _canonical_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"path": item["path"], "size": item["size"], "sha256": item["sha256"]}
            for item in sorted(files, key=lambda row: row["path"])]


def source_digest(files: list[dict[str, Any]], license_digest: str) -> str:
    payload = {"files": _canonical_files(files), "license_digest": license_digest}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(encoded)


def _validate_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ProvenanceError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _validate_revision(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ProvenanceError(f"{field} must be a 40-character lowercase Git revision")
    return value


def _validate_files(raw: Any, expected_count: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) != expected_count:
        raise ProvenanceError(f"files must contain exactly {expected_count} entries")
    allowed = {"path", "size", "sha256"}
    files = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != allowed:
            raise ProvenanceError(f"files[{index}] has an invalid shape")
        path = item.get("path")
        if (not isinstance(path, str) or "\\" in path or not path.startswith("pdf_translate/")
                or not path.endswith(".py") or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise ProvenanceError(f"files[{index}].path is not a package Python path")
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ProvenanceError(f"files[{index}].size must be a non-negative integer")
        files.append({"path": path, "size": size, "sha256": _validate_digest(item.get("sha256"), f"files[{index}].sha256")})
    if [row["path"] for row in files] != sorted({row["path"] for row in files}):
        raise ProvenanceError("files must be sorted by unique path")
    return files


def validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ProvenanceError("release record has missing or extra top-level fields")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ProvenanceError("schema_version must be 1")
    if not isinstance(record["release_id"], str) or not record["release_id"]:
        raise ProvenanceError("release_id must be non-empty")
    if record["scope"] not in {"relocation", "behavior-release"}:
        raise ProvenanceError("scope must be relocation or behavior-release")
    if not isinstance(record["version"], str) or not record["version"]:
        raise ProvenanceError("version must be non-empty")
    if record["scope"] == "relocation" and record["version"] != "89.0.0":
        raise ProvenanceError("relocation records must use version 89.0.0")
    if record["scope"] == "behavior-release" and record["version"] == "89.0.0":
        raise ProvenanceError("behavior-release cannot reuse relocation version 89.0.0")
    if record["version"] not in MODULE_COUNTS:
        raise ProvenanceError("version is not supported by this owner verifier")
    if record["origin_revision"] != ORIGIN_REVISION:
        raise ProvenanceError(f"origin_revision must be {ORIGIN_REVISION}")
    _validate_revision(record["adopted_owner_revision"], "adopted_owner_revision")
    _validate_digest(record["license_digest"], "license_digest")
    files = _validate_files(record["files"], MODULE_COUNTS[record["version"]])
    expected = source_digest(files, record["license_digest"])
    if record["source_digest"] != expected:
        raise ProvenanceError("source_digest does not match files and license_digest")
    return record


def verify_release_identity(record: Any, *, expected_release: dict | None = None) -> dict[str, Any]:
    """Authenticate against a trusted pin; the candidate cannot select it."""
    record = validate_record(record)
    expected = ADOPTED_RELEASE if expected_release is None else expected_release
    if set(expected) != set(ADOPTED_RELEASE):
        raise ProvenanceError("approved release pin has an invalid shape")
    for field in ("scope", "version", "release_id", "adopted_owner_revision",
                  "source_digest", "license_digest"):
        if record[field] != expected[field]:
            raise ProvenanceError(f"adopted release identity differs: {field}")
    if len(record["files"]) != expected["modules"]:
        raise ProvenanceError("adopted release identity has a different module count")
    return record


def verify_owner_tree(repo: Path, record: dict, *, tested_revision: str | None = None,
                      expected_release: dict | None = None) -> None:
    """Bind owner, ancestry, version and an app-only tested revision to bytes."""
    verify_release_identity(record, expected_release=expected_release)
    owner = record["adopted_owner_revision"]
    if _git(repo, "rev-parse", f"{owner}^{{commit}}") != owner:
        raise ProvenanceError("adopted owner revision does not resolve exactly")
    _git(repo, "merge-base", "--is-ancestor", ORIGIN_REVISION, owner)
    tested = _git(repo, "rev-parse", f"{tested_revision or 'HEAD'}^{{commit}}")
    _git(repo, "merge-base", "--is-ancestor", owner, tested)
    for revision in dict.fromkeys((owner, tested)):
        files = _canonical_files(_inventory_from_git(repo, revision, len(record["files"])))
        license_bytes = _git_blob(repo, revision, "libs/pdf-translate/LICENSE")
        if files != record["files"] or _sha256_bytes(license_bytes) != record["license_digest"]:
            raise ProvenanceError("owner/tested revision changes the adopted package identity")
        project = tomllib.loads(_git_blob(repo, revision, "libs/pdf-translate/pyproject.toml").decode())
        if project["project"]["version"] != record["version"]:
            raise ProvenanceError("owner/tested revision changes the adopted package version")


def _available_repo(library: Path) -> Path | None:
    for directory in (library.resolve(), *library.resolve().parents):
        if (directory / ".git").exists():
            return directory
    return None


def _record_path(library: Path) -> Path:
    return library / "provenance" / "release.json"


def write_record(library: Path, owner_revision: str, release_id: str,
                 scope: str, version: str = DEFAULT_VERSION, *,
                 expected_release: dict | None = None) -> Path:
    library = library.resolve()
    repo = _repo_root(library)
    owner_revision = _validate_revision(owner_revision, "adopted_owner_revision")
    if _git(repo, "rev-parse", f"{owner_revision}^{{commit}}") != owner_revision:
        raise ProvenanceError("adopted owner revision does not resolve exactly")
    if version not in MODULE_COUNTS:
        raise ProvenanceError("version is not supported by this owner verifier")
    files_with_meta = _inventory_from_git(repo, owner_revision, MODULE_COUNTS[version])
    active_license = _git_blob(repo, owner_revision, "libs/pdf-translate/LICENSE")
    if library.joinpath("LICENSE").read_bytes() != active_license:
        raise ProvenanceError("active library LICENSE differs from owner Git blob")
    for item in files_with_meta:
        current = library / item["path"]
        if not current.is_file() or current.read_bytes() != _git_blob(repo, owner_revision, PACKAGE_PREFIX + item["path"].removeprefix("pdf_translate/")):
            raise ProvenanceError(f"working-tree package bytes differ: {item['path']}")
    files = _canonical_files(files_with_meta)
    record = {
        "schema_version": 1, "release_id": release_id, "scope": scope,
        "version": version, "origin_revision": ORIGIN_REVISION,
        "adopted_owner_revision": owner_revision,
        "source_digest": source_digest(files, _sha256_bytes(active_license)),
        "license_digest": _sha256_bytes(active_license), "files": files,
    }
    verify_release_identity(record, expected_release=expected_release)
    verify_owner_tree(repo, record, expected_release=expected_release)
    path = _record_path(library)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def verify_library_record(library: Path, record_path: Path, *,
                          expected_release: dict | None = None,
                          tested_revision: str | None = None) -> dict[str, Any]:
    record = verify_release_identity(json.loads(record_path.read_text(encoding="utf-8")),
                                     expected_release=expected_release)
    actual = {f"pdf_translate/{path.relative_to(library / 'pdf_translate').as_posix()}"
              for path in (library / "pdf_translate").rglob("*.py")}
    if actual != {item["path"] for item in record["files"]}:
        raise ProvenanceError("library package has missing or extra Python modules")
    for item in record["files"]:
        path = library / item["path"]
        if not path.is_file() or path.stat().st_size != item["size"] or _sha256_file(path) != item["sha256"]:
            raise ProvenanceError(f"installed/source library bytes differ: {item['path']}")
    license_digest = _sha256_file(library / "LICENSE")
    if license_digest != record["license_digest"]:
        raise ProvenanceError("library LICENSE differs from release record")
    repo = _available_repo(library)
    if repo is not None:
        verify_owner_tree(repo, record, tested_revision=tested_revision,
                          expected_release=expected_release)
    return record


def _member_path(name: str, kind: str) -> str:
    if (not name or '\\' in name or ':' in name or
            any(part in {'', '.', '..'} for part in name.rstrip('/').split('/'))):
        raise ProvenanceError(f'{kind} contains an unsafe member path')
    return name.rstrip('/')


def _verify_package_members(members: dict[str, bytes], record: dict, kind: str) -> None:
    actual = {name for name in members if name.startswith('pdf_translate/')}
    if actual != {row['path'] for row in record['files']}:
        raise ProvenanceError(f'{kind} package has missing or extra modules')
    for row in record['files']:
        data = members[row['path']]
        if len(data) != row['size'] or _sha256_bytes(data) != row['sha256']:
            raise ProvenanceError(f'{kind} package bytes differ: {row["path"]}')


def _verify_metadata(data: bytes, record: dict, kind: str) -> None:
    fields = BytesParser().parsebytes(data, headersonly=True)
    if fields.get_all('Name') != [PACKAGE_NAME]:
        raise ProvenanceError(f'{kind} distribution name differs from release record')
    if fields.get_all('Version') != [record['version']]:
        raise ProvenanceError(f'{kind} distribution version differs from release record')


def verify_wheel(wheel: Path, record: dict, *, wheel_digest: str,
                 expected_release: dict | None = None) -> bytes:
    """Authenticate archive contents; return metadata for installation comparison."""
    verify_release_identity(record, expected_release=expected_release)
    if _sha256_file(wheel) != _validate_digest(wheel_digest, 'wheel_digest'):
        raise ProvenanceError('wheel digest differs from approved artifact')
    try:
        with zipfile.ZipFile(wheel) as archive:
            entries = archive.infolist()
            names = [_member_path(item.filename, 'wheel') for item in entries]
            if len(set(names)) != len(names) or any(
                    (item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries):
                raise ProvenanceError('wheel contains duplicate or linked members')
            members = {item.filename: archive.read(item) for item in entries if not item.is_dir()}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ProvenanceError('wheel archive is absent or malformed') from exc
    dist = f'pdf_translate-{record["version"]}.dist-info/'
    if any(not name.startswith(('pdf_translate/', dist)) for name in members):
        raise ProvenanceError('wheel contains files outside the adopted distribution')
    _verify_package_members(members, record, 'wheel')
    metadata = members.get(dist + 'METADATA', b'')
    _verify_metadata(metadata, record, 'wheel')
    license_bytes = members.get(dist + 'licenses/LICENSE', b'')
    if _sha256_bytes(license_bytes) != record['license_digest']:
        raise ProvenanceError('wheel MIT license differs from release record')
    return metadata


def verify_source_archive(source: Path, record: dict, *, source_archive_digest: str,
                          expected_release: dict | None = None) -> None:
    """Verify the complete source archive, including its canonical release record."""
    verify_release_identity(record, expected_release=expected_release)
    if _sha256_file(source) != _validate_digest(source_archive_digest, 'source_archive_digest'):
        raise ProvenanceError('source archive digest differs from approved artifact')
    try:
        with tarfile.open(source, 'r:*') as archive:
            entries = archive.getmembers()
            names = [_member_path(item.name, 'source archive') for item in entries]
            roots = {name.split('/')[0] for name in names}
            if len(roots) != 1 or len(set(names)) != len(names):
                raise ProvenanceError('source archive must have one root and unique members')
            if any(not (item.isfile() or item.isdir()) for item in entries):
                raise ProvenanceError('source archive contains a linked or special member')
            prefix = next(iter(roots)) + '/'
            members = {item.name.removeprefix(prefix): archive.extractfile(item).read()
                       for item in entries if item.isfile()}
    except (OSError, tarfile.TarError) as exc:
        raise ProvenanceError('source archive is absent or malformed') from exc
    _verify_package_members(members, record, 'source archive')
    if _sha256_bytes(members.get('LICENSE', b'')) != record['license_digest']:
        raise ProvenanceError('source archive MIT license differs from release record')
    try:
        project = tomllib.loads(members['pyproject.toml'].decode('utf-8'))['project']
        archived_record = json.loads(members['provenance/release.json'])
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ProvenanceError('source archive lacks valid package/release metadata') from exc
    if project.get('name') != PACKAGE_NAME or project.get('version') != record['version']:
        raise ProvenanceError('source archive package name/version differs')
    verify_release_identity(archived_record, expected_release=expected_release)
    if archived_record != record:
        raise ProvenanceError('source archive record differs from canonical release record')


def verify_installed(installed: Path, record: dict[str, Any], *,
                     expected_release: dict | None = None,
                     wheel_digest: str | None = None,
                     wheel_path: Path | None = None) -> None:
    verify_release_identity(record, expected_release=expected_release)
    package = installed / "pdf_translate"
    actual = {f"pdf_translate/{p.relative_to(package).as_posix()}" for p in package.rglob("*.py")}
    expected = {item["path"] for item in record["files"]}
    if actual != expected:
        raise ProvenanceError("installed package has missing or extra Python modules")
    metadata = sorted(installed.glob("pdf_translate-*.dist-info/METADATA"))
    if len(metadata) != 1:
        raise ProvenanceError("expected exactly one installed pdf-translate METADATA")
    direct_url = metadata[0].parent / 'direct_url.json'
    if wheel_path is not None:
        if wheel_digest is None:
            raise ProvenanceError('wheel provenance requires an approved wheel digest')
        wheel_metadata = verify_wheel(Path(wheel_path), record, wheel_digest=wheel_digest,
                                      expected_release=expected_release)
        if metadata[0].read_bytes() != wheel_metadata:
            raise ProvenanceError('installed metadata differs from authenticated wheel')
    if wheel_digest is not None:
        _validate_digest(wheel_digest, 'wheel_digest')
        if not direct_url.is_file():
            raise ProvenanceError('wheel digest requires observed wheel provenance')
    if direct_url.is_file():
        try:
            data = json.loads(direct_url.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProvenanceError("direct_url.json is malformed") from exc
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise ProvenanceError("direct_url.json must contain a URL object")
        if "dir_info" in data:
            info = data["dir_info"]
            if not isinstance(info, dict) or info.get("editable") is not False:
                raise ProvenanceError("editable or malformed local-source installation is not accepted")
            if not data["url"].startswith("file:"):
                raise ProvenanceError("non-editable directory provenance must use a file URL")
            if wheel_digest is not None:
                raise ProvenanceError('wheel digest cannot authenticate directory provenance')
        elif urlsplit(data['url']).scheme in ('file', 'http', 'https'):
            if not isinstance(data.get("archive_info"), dict):
                raise ProvenanceError("file URL installation must carry archive_info")
            if wheel_digest is not None:
                archive = data["archive_info"]
                digests = []
                if isinstance(archive.get('hashes'), dict) and 'sha256' in archive['hashes']:
                    digests.append(archive['hashes']['sha256'])
                if isinstance(archive.get('hash'), str):
                    digests.append(archive['hash'].removeprefix('sha256='))
                if any(digest != wheel_digest for digest in digests):
                    raise ProvenanceError("installed wheel digest differs from approved artifact")
                url = urlsplit(data['url'])
                if wheel_path is not None and url.scheme == 'file':
                    if url.netloc not in ('', 'localhost') or url.query or url.fragment:
                        raise ProvenanceError('wheel provenance uses an unsafe file URL')
                    observed_path = Path(url2pathname(url.path)).resolve()
                    if observed_path != Path(wheel_path).resolve():
                        raise ProvenanceError('wheel provenance references a different artifact')
                elif not digests:
                    raise ProvenanceError('wheel digest requires hashed archive provenance or an authenticated local wheel')
        elif data["url"].startswith(("/", "\\")):
            raise ProvenanceError("checkout-path installation is not accepted")
        elif wheel_digest is not None:
            raise ProvenanceError('wheel digest requires supported archive provenance')
    for item in record["files"]:
        path = installed / item["path"]
        if path.stat().st_size != item["size"] or _sha256_file(path) != item["sha256"]:
            raise ProvenanceError(f"installed package bytes differ: {item['path']}")
    _verify_metadata(metadata[0].read_bytes(), record, 'installed')
    licenses = [path for path in (metadata[0].parent / "licenses" / "LICENSE",
                                  metadata[0].parent / "LICENSE") if path.is_file()]
    if len(licenses) != 1 or _sha256_file(licenses[0]) != record["license_digest"]:
        raise ProvenanceError("installed MIT license differs from release record")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--library", type=Path, required=True)
    write.add_argument("--owner-revision", required=True)
    write.add_argument("--release-id", required=True)
    write.add_argument("--scope", choices=("relocation", "behavior-release"), required=True)
    write.add_argument("--version", default=DEFAULT_VERSION)
    verify = sub.add_parser("verify")
    verify.add_argument("--library", type=Path, required=True)
    verify.add_argument("--record", type=Path)
    verify.add_argument("--installed", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            print(write_record(args.library, args.owner_revision, args.release_id, args.scope, args.version))
        else:
            record = verify_library_record(args.library, args.record or _record_path(args.library))
            if args.installed:
                verify_installed(args.installed, record)
            print("library provenance verified")
    except (OSError, json.JSONDecodeError, ProvenanceError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
