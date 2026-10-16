"""The Bash helper reuses supported environments and honors the selected interpreter."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import ROOT


def bash_executable() -> str:
    git = shutil.which("git")
    if git and os.name == "nt":
        bundled = Path(git).parent.parent / "usr" / "bin" / "bash.exe"
        if bundled.is_file():
            return str(bundled)
    bash = shutil.which("bash")
    if bash:
        return bash
    pytest.skip("Bash is not available")


def test_compatible_venv_is_reused(tmp_path):
    script = tmp_path / "init_venv.sh"
    shutil.copyfile(ROOT / "init_venv.sh", script)
    environment = tmp_path / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
    marker = environment / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    source = 'PATH="/usr/bin:$PATH"; source "$(cygpath -u "$1")"' if os.name == "nt" else 'source "$1"'
    command = source + ' || exit $?; python -c "import sys; print(sys.prefix)"'
    result = subprocess.run(
        [bash_executable(), "-c", command, "test", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert str(environment).replace("\\", "/") in result.stdout.replace("\\", "/")
    assert marker.read_text(encoding="utf-8") == "keep"


def test_existing_invalid_environment_is_not_deleted(tmp_path):
    script = tmp_path / "init_venv.sh"
    shutil.copyfile(ROOT / "init_venv.sh", script)
    environment = tmp_path / ".venv"
    environment.mkdir()
    marker = environment / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    source = 'PATH="/usr/bin:$PATH"; source "$(cygpath -u "$1")"' if os.name == "nt" else 'source "$1"'
    result = subprocess.run(
        [bash_executable(), "-c", source, "test", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 1
    assert "[ERROR]" in result.stderr
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("valid", [True, False])
def test_explicit_interpreter_controls_environment_creation(tmp_path, valid):
    script = tmp_path / "init_venv.sh"
    shutil.copyfile(ROOT / "init_venv.sh", script)
    environment = dict(os.environ)
    environment["TTA_PYTHON"] = sys.executable if valid else str(tmp_path / "missing-python")
    source = 'PATH="/usr/bin:$PATH"; source "$(cygpath -u "$1")"' if os.name == "nt" else 'source "$1"'
    result = subprocess.run(
        [bash_executable(), "-c", source, "test", str(script)],
        env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == (0 if valid else 1), result.stderr
    assert (tmp_path / ".venv").exists() == valid
    if not valid:
        assert "[ERROR]" in result.stderr
