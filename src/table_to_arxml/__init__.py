"""Excel 表格转 AUTOSAR ARXML 工具集：table-to-system-arxml 与 table-to-swc-arxml。"""

from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if (_root / ".git").exists():
    from setuptools_scm import get_version

    __version__ = get_version(root=_root)
else:
    from ._version import __version__ as __version__
