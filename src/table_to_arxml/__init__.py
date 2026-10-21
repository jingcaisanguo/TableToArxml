"""Excel 表格转 AUTOSAR ARXML 工具集：table-to-system-arxml 与 table-to-swc-arxml。"""

from pathlib import Path

_package_dir = Path(__file__).resolve().parent
_root = _package_dir.parents[1]
if (_root / ".git").exists() and _package_dir == _root / "src" / "table_to_arxml":
    from setuptools_scm import get_version

    __version__ = get_version(root=_root, dist_name="table-to-arxml")
else:
    from ._version import __version__ as __version__
