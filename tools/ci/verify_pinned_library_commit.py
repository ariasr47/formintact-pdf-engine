#!/usr/bin/env python3
"""Verify the engine's canonical noneditable library against its release pin.

Directory installs authenticate owner/source/license identity and installed bytes.
Legacy Git-lock callers retain the resolved lock versus installed PEP610 check.
"""
from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

from library_provenance import ProvenanceError, verify_installed, verify_library_record


class PinCheckError(Exception):
    """A condition that must stop the build with a clear, specific message."""


def pinned_commit_from_lock(lock_path: Path) -> str:
    if not lock_path.is_file():
        raise PinCheckError(f"lock file not found: {lock_path}")
    with open(lock_path, "rb") as handle:
        lock = tomllib.load(handle)
    for entry in lock.get("package", []):
        if entry.get("name") != "pdf-translate":
            continue
        git_url = entry.get("source", {}).get("git", "")
        if "#" not in git_url:
            raise PinCheckError(
                f"pdf-translate lock entry has no resolved commit fragment: {git_url!r}"
            )
        return git_url.rsplit("#", 1)[1]
    raise PinCheckError(f"no 'pdf-translate' package entry found in {lock_path}")


def library_entry_from_lock(lock_path: Path) -> dict:
    if not lock_path.is_file():
        raise PinCheckError(f"lock file not found: {lock_path}")
    with open(lock_path, "rb") as handle:
        lock = tomllib.load(handle)
    for entry in lock.get("package", []):
        if entry.get("name") == "pdf-translate":
            return entry
    raise PinCheckError(f"no 'pdf-translate' package entry found in {lock_path}")


def resolve_venv_dir(api_dir: Path) -> Path:
    venv_env = os.environ.get("UV_PROJECT_ENVIRONMENT")
    venv_dir = Path(venv_env) if venv_env else api_dir / ".venv"
    if not venv_dir.is_dir():
        raise PinCheckError(f"virtual environment not found: {venv_dir}")
    return venv_dir


def find_direct_url(venv_dir: Path) -> Path:
    for candidate in sorted(venv_dir.glob("**/direct_url.json")):
        normalized = candidate.parent.name.lower().replace("_", "-")
        if normalized.startswith("pdf-translate-"):
            return candidate
    raise PinCheckError(
        f"no installed pdf-translate distribution with a direct_url.json was found "
        f"under {venv_dir}; uv recorded no installed commit to verify against"
    )


def find_site_packages(venv_dir: Path) -> Path:
    candidates = sorted(path.parent for path in venv_dir.glob("**/site-packages/pdf_translate"))
    if len(candidates) != 1:
        raise PinCheckError(f"expected one installed pdf_translate package under {venv_dir}")
    return candidates[0]


def installed_commit(direct_url_path: Path) -> str:
    data = json.loads(direct_url_path.read_text(encoding="utf-8"))
    commit = data.get("vcs_info", {}).get("commit_id")
    if not commit:
        raise PinCheckError(f"{direct_url_path} has no vcs_info.commit_id: {data!r}")
    return commit


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: verify_pinned_library_commit.py <api-directory>", file=sys.stderr)
        return 2

    api_dir = Path(argv[0])
    try:
        entry = library_entry_from_lock(api_dir / "uv.lock")
        venv_dir = resolve_venv_dir(api_dir)
        source = entry.get("source", {})
        if "directory" in source:
            directory = source["directory"]
            if not isinstance(directory, str) or not directory:
                raise PinCheckError("pdf-translate directory source is malformed")
            library_dir = (api_dir / directory).resolve()
            record_path = library_dir / "provenance" / "release.json"
            if not record_path.is_file():
                raise PinCheckError(f"canonical pdf-translate release record not found: {record_path}")
            try:
                record = verify_library_record(library_dir, record_path)
                site_packages = find_site_packages(venv_dir)
                verify_installed(site_packages, record)
            except (OSError, ValueError, ProvenanceError) as exc:
                raise PinCheckError(str(exc)) from exc
            lock_version = entry.get("version")
            if lock_version != record["version"]:
                raise PinCheckError(
                    f"pdf-translate lock version {lock_version!r} differs from release record {record['version']!r}"
                )
            print(f"pdf-translate canonical owner record and installed bytes match: {record['adopted_owner_revision']}")
            return 0
        pinned = pinned_commit_from_lock(api_dir / "uv.lock")
        direct_url_path = find_direct_url(venv_dir)
        installed = installed_commit(direct_url_path)
    except PinCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if installed != pinned:
        print(
            f"pdf-translate is installed at commit {installed}, "
            f"but {api_dir / 'uv.lock'} pins {pinned}",
            file=sys.stderr,
        )
        return 1

    print(f"pdf-translate installed commit matches the lock: {installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
