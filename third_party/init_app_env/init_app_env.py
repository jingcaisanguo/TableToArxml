"""从本地 wheel 目录安装内部工程所需的固定版本 Python 包。

用法：``python init_app_env.py <wheel 目录>``

已安装且版本相同的包会跳过；安装失败时最多重试 3 次，仍失败则以退出码 1 结束。
armodel 最先安装，保证后续包解析依赖时不会从索引拉取不兼容的 armodel 版本。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
import subprocess
import sys
import time
from typing import Sequence

LOGGER = logging.getLogger("init_app_env")

# 包名 -> (版本, wheel 文件名)；按安装顺序排列。
PIP_PACKAGES = {
    "armodel": ("1.9.2", "armodel-1.9.2-py3-none-any.whl"),
    # 本仓库工具不使用，保留给外部工具。
    "py_eb_model": ("1.3.1", "py_eb_model-1.3.1-py3-none-any.whl"),
    "table_to_arxml": ("0.3.0", "table_to_arxml-0.3.0-py3-none-any.whl"),
}
INSTALL_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


def installed_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def install_pip_packages(package_path: str) -> int:
    """按 :data:`PIP_PACKAGES` 安装缺失或版本不同的包，返回退出码。"""

    for package_name, (required_version, whl_name) in PIP_PACKAGES.items():
        current_version = installed_version(package_name)
        if current_version == required_version:
            LOGGER.info("Skip installed package %s==%s", package_name, required_version)
            continue

        whl_file = os.path.join(package_path, whl_name)
        if not os.path.isfile(whl_file):
            LOGGER.error("Wheel file does not exist: %s", whl_file)
            return 1

        LOGGER.info("Install %s==%s (installed: %s)", package_name, required_version,
                    current_version or "not installed")
        command = [sys.executable, "-m", "pip", "install", "-q", whl_file]
        for attempt in range(1, INSTALL_ATTEMPTS + 1):
            if subprocess.run(command, check=False).returncode == 0:
                break
            LOGGER.error("Attempt %d failed to install %s", attempt, whl_name)
            if attempt < INSTALL_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
        else:
            LOGGER.error("Failed to install %s after %d attempts", whl_name, INSTALL_ATTEMPTS)
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned wheels used by the internal project.")
    parser.add_argument("package_path", help="directory holding the wheel files")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.package_path):
        parser.error(f"wheel directory does not exist: {args.package_path}")

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] : %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.DEBUG)
    return install_pip_packages(args.package_path)


if __name__ == "__main__":
    raise SystemExit(main())
