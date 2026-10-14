'''

ARGV Parameters:
0: directory holding the whl packages

'''
import os
import sys
import importlib.metadata
import logging
import subprocess
import time

# 0.2.0 之前两个工具各自发布，与合并后的 table-to-arxml 包含同一批文件，必须先卸载。
LEGACY_PACKAGES = ("table_to_system_arxml", "table_to_swc_arxml")


def installed_version(package_name: str):
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_pip(args: list, description: str):
    cmd = [sys.executable, "-m", "pip", *args]
    for attempt in range(1, 4):
        if subprocess.run(cmd, check=False).returncode == 0:
            return
        logger.error("Attempt %d failed to %s", attempt, description)
        if attempt < 3:
            time.sleep(10)
    logger.error("Failed to %s after 3 attempts", description)
    sys.exit(1)


def remove_legacy_packages() -> bool:
    legacy = [name for name in LEGACY_PACKAGES if installed_version(name)]
    if legacy:
        logger.info("Uninstall legacy packages: %s", ", ".join(legacy))
        run_pip(["uninstall", "-y", "-q", *legacy], "uninstall " + ", ".join(legacy))
    return bool(legacy)


def install_pip_packages(package_path: str):
    # armodel first, so the tools' dependency resolution never pulls an incompatible armodel from the index.
    pip_packages = {
        "armodel": ("1.9.2", "armodel-1.9.2-py3-none-any.whl"),
        # Not used by the tools here; kept for external tools.
        "py_eb_model": ("1.3.1", "py_eb_model-1.3.1-py3-none-any.whl"),
        "table_to_arxml": ("0.2.0", "table_to_arxml-0.2.0-py3-none-any.whl"),
    }
    # 卸载旧包会删掉共用的文件，此时即使版本已符合也要重装 table_to_arxml。
    reinstall = {"table_to_arxml"} if remove_legacy_packages() else set()

    for package_name, (required_version, whl_name) in pip_packages.items():
        current_version = installed_version(package_name)
        if current_version == required_version and package_name not in reinstall:
            logger.info("Skip installed package %s==%s", package_name, required_version)
            continue

        whl_file = os.path.join(package_path, whl_name)
        if not os.path.isfile(whl_file):
            logger.error("Wheel file does not exist: %s", whl_file)
            sys.exit(1)

        logger.info("Install %s==%s (installed: %s)", package_name, required_version,
                    current_version or "not installed")
        options = ["--force-reinstall", "--no-deps"] if current_version == required_version else []
        run_pip(["install", "-q", *options, whl_file], "install " + whl_name)


if __name__ == "__main__":

    # initialize the logger
    logger = logging.getLogger()
    formatter = logging.Formatter('[%(levelname)s] : %(message)s')
    stdout_handler = logging.StreamHandler(sys.stderr)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)
    logger.setLevel(logging.DEBUG)

    (_, package_path) = sys.argv

    install_pip_packages(package_path)
