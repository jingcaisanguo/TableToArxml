"""运行依赖检查；armodel 版本范围只在这里定义，并由测试保证与 requirements.txt 一致。"""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError, version

# 上限随兼容测试更新；1.9.1 的包入口不完整，单独排除。
ARMODEL_REQUIREMENT = ">=1.8.7,<=1.9.8,!=1.9.1"
REQUIRED_MODULES = ("packaging", "openpyxl", "armodel") + (("tomli",) if sys.version_info < (3, 11) else ())


def check_dependencies() -> None:
    """确认第三方依赖可以导入，且 armodel 版本在支持范围内；否则抛出 ``RuntimeError``。

    只检查版本元数据并导入顶层模块，不加载 ARXML，因此可以在读取输入前尽早失败。
    """

    failures: list[str] = []
    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as error:  # 依赖的任何导入错误都要汇总给用户
            failures.append(f"{module_name}: {type(error).__name__}: {error}")
    if not failures:
        specifier = importlib.import_module("packaging.specifiers").SpecifierSet(ARMODEL_REQUIREMENT)
        try:
            installed = version("armodel")
        except PackageNotFoundError:
            installed = "unknown"
        if installed not in specifier:
            failures.append(f"armodel{ARMODEL_REQUIREMENT} is required; installed: {installed}")
    if failures:
        raise RuntimeError(
            f"Python dependencies are missing or unusable (interpreter: {sys.executable}). "
            "Install or repair the project dependencies in this interpreter. " + "; ".join(failures)
        )
