"""System options: command-line paths stay relative to the working directory on every Python version."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from system_fixtures import write_config
from table_to_arxml.common.errors import ConfigError
from table_to_arxml.system import cli
from table_to_arxml.system import options as system_options
from table_to_arxml.system.options import RunOptions, _filename_part, prepare_jobs


@pytest.fixture
def python39_windows_resolve(monkeypatch):
    """Mimic ``Path.resolve()`` of Python 3.9 on Windows (bpo-38671 and WinError 123)."""

    resolve = Path.resolve

    def legacy_resolve(self, strict=False):
        if any(character in str(self) for character in "*?"):
            raise OSError(22, "The filename, directory name, or volume label syntax is incorrect", str(self))
        if not self.is_absolute() and not self.exists():
            return self
        return resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", legacy_resolve)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    working_dir = tmp_path / "cwd"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    return config_dir, working_dir


def test_missing_command_line_paths_are_relative_to_working_directory(layout, python39_windows_resolve):
    config_dir, working_dir = layout
    config = write_config(config_dir)
    job = prepare_jobs(RunOptions(
        config_paths=(config,), output_dir="out", log_dir="log", project_root="proj",
        white_table=os.path.join("sub", "W.xlsx"), arxml_file_list="missing.lst",
    ))[0]
    assert job.white_table == working_dir / "sub" / "W.xlsx"
    assert job.output_dir == working_dir / "out"
    assert job.log_dir == working_dir / "log"
    assert job.project_root == working_dir / "proj"
    assert job.arxml_file_list == working_dir / "missing.lst"


def test_relative_config_path_is_absolute(layout, python39_windows_resolve):
    config_dir, working_dir = layout
    write_config(config_dir, outputDir="missing-out")
    job = prepare_jobs(RunOptions(config_paths=(Path("..") / "cfg" / "config.json",)))[0]
    assert job.config_path == config_dir / "config.json"
    assert job.output_dir == config_dir / "missing-out"


@pytest.mark.parametrize("key", ["whiteTable", "arxmlFileList", "outputDir", "logDir"])
def test_wildcard_characters_in_literal_config_paths_do_not_crash(layout, python39_windows_resolve, key):
    config_dir, _working_dir = layout
    job = prepare_jobs(RunOptions(config_paths=(write_config(config_dir, **{key: "wh*.x?x"}),)))[0]
    attribute = {"whiteTable": "white_table", "arxmlFileList": "arxml_file_list",
                 "outputDir": "output_dir", "logDir": "log_dir"}[key]
    assert getattr(job, attribute) == config_dir / "wh*.x?x"


def test_path_errors_while_preparing_configs_are_configuration_errors(layout, monkeypatch):
    config_dir, _working_dir = layout
    config = write_config(config_dir)

    def unsupported(*args, **kwargs):
        raise OSError(1, "Incorrect function")

    monkeypatch.setattr(system_options, "absolute_path", unsupported)
    with pytest.raises(ConfigError, match="Incorrect function"):
        prepare_jobs(RunOptions(config_paths=(config,)))


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path prefix")
def test_long_path_prefix_is_accepted_on_the_command_line(layout):
    config_dir, working_dir = layout
    white = working_dir / "W.xlsx"
    white.touch()
    job = prepare_jobs(RunOptions(
        config_paths=(write_config(config_dir),), white_table="\\\\?\\" + str(white),
        output_dir="\\\\?\\" + str(working_dir / "out"),
    ))[0]
    assert str(job.white_table).endswith(str(white)[2:])
    assert job.white_table.is_file()


@pytest.mark.parametrize("value", ["NUL", "nul", "Con", "PRN", "aux", "COM1", "lpt9", "NUL.backup"])
def test_windows_reserved_device_names_are_rejected(value):
    with pytest.raises(ConfigError, match="portable filename"):
        _filename_part(value, "variant")


@pytest.mark.parametrize("value", ["NULL", "Console", "COM10", "LPT0", "Auxiliary", "variantA"])
def test_names_containing_device_names_are_accepted(value):
    assert _filename_part(value, "variant") == value


def test_reserved_variant_fails_as_configuration_error(layout):
    config_dir, _working_dir = layout
    with pytest.raises(ConfigError, match="variant must be a portable filename"):
        prepare_jobs(RunOptions(config_paths=(write_config(config_dir, defaultVariant="NUL"),)))


@pytest.mark.parametrize("prefix", ["Aux", "Con", "NUL"])
def test_output_prefix_is_only_rejected_when_output_names_are_device_names(layout, prefix):
    config_dir, _working_dir = layout
    job = prepare_jobs(RunOptions(config_paths=(write_config(config_dir, outputPrefix=prefix),)))[0]
    assert job.output_prefix == prefix
    with pytest.raises(ConfigError, match="Windows device file names"):
        prepare_jobs(RunOptions(config_paths=(write_config(config_dir, outputPrefix=f"{prefix}.v1"),)))


@pytest.mark.parametrize("value", [
    "cfg/${variant}.toml",
    "D:cfg.toml" if os.name == "nt" else "D:\\cfg.toml",
])
def test_invalid_config_argument_is_a_usage_error(value, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["--config", value])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "usage:" in error and "--config" in error
