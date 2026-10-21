"""Publish new releases or explicitly restore missing assets without overwriting."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def gh(*arguments: str) -> str:
    return subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, text=True,
    ).stdout


def asset_names(tag: str) -> tuple[str, str]:
    return f"table_to_arxml-{tag}-py3-none-any.whl", f"table_to_arxml-{tag}-user-guide.zip"


def release_state(tag: str, recover: bool) -> dict | None:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
        raise ValueError(f"Release tag must be canonical x.y.z: {tag!r}")
    repository = os.environ["GH_REPO"]
    pages = json.loads(gh("api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp"))
    release = next((item for page in pages for item in page if item["tag_name"] == tag), None)
    if recover and release is None:
        raise ValueError(f"Recovery requires an existing release: {tag}")
    if not recover and release is not None:
        raise ValueError(f"Release {tag} already exists; use explicit missing-asset recovery")
    if recover and release is not None:
        existing = {asset["name"] for asset in release["assets"]}
        if set(asset_names(tag)) <= existing:
            raise ValueError(f"Release {tag} has all expected assets; nothing to recover")
    return release


def publish(tag: str, recover: bool, directory: Path) -> None:
    release = release_state(tag, recover)
    names = asset_names(tag)
    if sorted(path.name for path in directory.iterdir()) != sorted(names):
        raise ValueError(f"Expected exactly these release assets in {directory}: {names}")
    if release is None:
        gh("release", "create", tag, *(str(directory / name) for name in names),
           "--verify-tag", "--draft", "--title", tag, "--generate-notes")
        gh("release", "edit", tag, "--draft=false")
        return
    existing = {asset["name"] for asset in release["assets"]}
    missing = [name for name in names if name not in existing]
    for name in missing:
        gh("release", "upload", tag, str(directory / name))
        print(f"Restored missing asset: {name}")
    if release["draft"]:
        print(f"Release {tag} remains a draft; review its assets before publishing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "publish"))
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        tag = os.environ["TAG"]
        recover = os.environ.get("RECOVER", "false") == "true"
        if args.action == "preflight":
            release_state(tag, recover)
        else:
            publish(tag, recover, args.directory)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, f"Release failed: {detail}\n")


if __name__ == "__main__":
    main()
