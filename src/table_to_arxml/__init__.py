"""Excel 表格转 AUTOSAR ARXML 工具集：table-to-system-arxml 与 table-to-swc-arxml。"""

import os
from pathlib import Path


def _detect_version() -> str:
    """源码工作区用 setuptools-scm 的实时版本；不可用时依次取构建生成的版本文件、安装元数据。

    只有 ``src/table_to_arxml`` 布局且根目录有 Git 标记时才视为源码工作区，
    安装包即使位于某个 Git 工作区内也不使用该工作区的版本。
    """

    try:
        package_dir = Path(os.path.realpath(__file__)).parent
    except (OSError, ValueError):
        package_dir = Path(os.path.abspath(__file__)).parent
    root = package_dir.parents[1]
    if package_dir == root / "src" / "table_to_arxml" and os.path.exists(root / ".git"):
        try:
            from setuptools_scm import get_version

            return get_version(root=str(root), dist_name="table-to-arxml")
        except Exception:  # 未安装 setuptools-scm、PATH 中没有 git 等，继续回退
            pass
    try:
        from ._version import __version__ as generated
    except ImportError:
        pass
    else:
        return generated
    try:
        from importlib.metadata import version

        return version("table-to-arxml")
    except Exception:
        return "0+unknown"


__version__ = _detect_version()