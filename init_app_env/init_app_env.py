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


def install_pip_packages(package_path: str):
    # armodel first, so the tools' dependency resolution never pulls an incompatible armodel from the index.
    pip_packages = {
        "armodel": ("1.9.2", "armodel-1.9.2-py3-none-any.whl"),
        # Not used by the tools here; kept for external tools.
        "py_eb_model": ("1.3.1", "py_eb_model-1.3.1-py3-none-any.whl"),
        "table_to_arxml": ("0.2.0", "table_to_arxml-0.2.0-py3-none-any.whl"),
    }

    for package_name, (required_version, whl_name) in pip_packages.items():
        try:
            installed_version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            installed_version = None

        if installed_version == required_version:
            logger.info("Skip installed package %s==%s", package_name, required_version)
            continue

        whl_file = os.path.join(package_path, whl_name)
        if not os.path.isfile(whl_file):
            logger.error("Wheel file does not exist: %s", whl_file)
            sys.exit(1)

        logger.info("Install %s==%s (installed: %s)", package_name, required_version,
                    installed_version or "not installed")
        cmd = [sys.executable, "-m", "pip", "install", "-q", whl_file]
        for attempt in range(1, 4):
            result = subprocess.run(cmd, check=False)
            if result.returncode == 0:
                break
            logger.error("Attempt %d failed to install %s", attempt, whl_name)
            if attempt < 3:
                time.sleep(10)
        else:
            logger.error("Failed to install %s after 3 attempts", whl_name)
            sys.exit(1)


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
