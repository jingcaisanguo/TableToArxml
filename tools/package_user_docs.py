"""Bundle the external user guide and templates alongside a built wheel."""

from __future__ import annotations

import argparse
from email.parser import Parser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from packaging.version import Version
from setuptools_scm import get_version

ROOT = Path(__file__).resolve().parents[1]


def package_user_docs(wheel_dir: Path, source_root: Path = ROOT) -> Path:
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"Expected exactly one wheel in {wheel_dir}, found {len(wheels)}")
    with ZipFile(wheels[0]) as wheel:
        metadata_files = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise ValueError("Wheel must contain exactly one distribution metadata file")
        metadata = Parser().parsestr(wheel.read(metadata_files[0]).decode("utf-8"))
        version = metadata["Version"]
        if metadata["Name"] != "table-to-arxml" or not version:
            raise ValueError("Expected a table-to-arxml wheel with a version")
        source_version = get_version(root=source_root, dist_name="table-to-arxml")
        if Version(version) != Version(source_version):
            raise ValueError(f"Wheel version {version} does not match this checkout's documentation {source_version}")
        allowed_roots = {"table_to_arxml", metadata_files[0].split("/")[0]}
        if any(name.split("/")[0] not in allowed_roots for name in wheel.namelist()):
            raise ValueError("Wheel contains files outside the package and distribution metadata")
    output = wheel_dir / f"table_to_arxml-{version}-user-guide.zip"
    docs = source_root / "docs" / "user"
    if not (docs / "README.md").is_file():
        raise ValueError(f"User documentation entry point is missing: {docs / 'README.md'}")
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(docs.rglob("*")):
            if path.is_file() and path.suffix in (".md", ".xlsx"):
                info = ZipInfo(path.relative_to(docs).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, default=Path("dist"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    package_user_docs(args.wheel_dir, args.source_root.resolve())


if __name__ == "__main__":
    main()
