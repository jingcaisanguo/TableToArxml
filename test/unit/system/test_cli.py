"""System 命令行：配置错误退出码 2、不在依赖检查前导入 armodel、默认配置位置。"""

from __future__ import annotations

import json

import pytest

from helpers import SYSTEM, run_python, run_tool
from system_fixtures import write_config, write_model, write_white_table


def test_help_does_not_import_armodel():
    code = (
        "import sys; sys.argv = ['table-to-system-arxml', '--help']\n"
        "from table_to_arxml.system import cli\n"
        "assert 'armodel' not in sys.modules\n"
        "try:\n    cli.main()\nexcept SystemExit as exit_:\n    assert exit_.code == 0\n"
        "assert 'armodel' not in sys.modules"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("config, message", [
    ("{not json", "Cannot read config file"),
    ({"unknownKey": 1}, "Unknown config option"),
    ({"defaultVariant": "T", "projectRoot": ".", "greenTables": "x.xlsx"}, "Missing config.outputPrefix"),
    ({"defaultVariant": "T", "projectRoot": ".", "outputPrefix": "T", "greenTables": "x.xlsx",
      "outputDir": "out", "logDir": "log"}, "an ARXML list is required"),
    ({"defaultVariant": "T", "projectRoot": ".", "outputPrefix": "T", "greenTables": "x.xlsx",
      "arxmlFileList": "a.lst", "strictList": "yes"}, "config.strictList must be true or false"),
    ({"defaultVariant": "T", "projectRoot": ".", "outputPrefix": "T", "greenTables": "x.xlsx",
      "arxmlFileList": "a.lst"}, "directory is required via --log-dir"),
])
def test_configuration_errors_exit_with_code_2(tmp_path, config, message):
    path = tmp_path / "config.json"
    path.write_text(config if isinstance(config, str) else json.dumps(config), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", path)
    assert result.returncode == 2, result.stderr
    assert message in result.stderr
    assert "usage:" in result.stderr
    assert not (tmp_path / "log").exists()


def test_default_config_is_read_from_working_directory(tmp_path):
    result = run_tool(SYSTEM, cwd=tmp_path)
    assert result.returncode == 2
    assert str(tmp_path / "config.json") in result.stderr


def test_strict_list_option_fails_on_missing_entries(tmp_path):
    list_file = write_model(tmp_path)
    list_file.write_text("model.arxml\nmissing.arxml\n", encoding="utf-8")
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    config = write_config(tmp_path)
    relaxed = run_tool(SYSTEM, "--config", config)
    assert relaxed.returncode == 0, relaxed.stderr
    assert "Listed ARXML not found" in relaxed.stderr
    strict = run_tool(SYSTEM, "--config", config, "--strict-list", "--output-dir", tmp_path / "strict")
    assert strict.returncode == 1
    assert "Listed ARXML not found" in strict.stderr
    assert not (tmp_path / "strict").exists()
