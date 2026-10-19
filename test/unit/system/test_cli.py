"""System 命令行：配置错误退出码 2、不在依赖检查前导入 armodel、默认配置位置。"""

from __future__ import annotations

import json

import pytest

from helpers import SYSTEM, run_python, run_tool
from system_fixtures import write_config, write_model, write_white_table

# 与 write_config 默认值等价的 TOML 配置。
TOML_CONFIG = """\
# 注释
defaultVariant = "T"
projectRoot = "."
outputPrefix = "T"
whiteTable = "white.xlsx"   # 白表模式
arxmlFileList = "model.lst"
outputDir = "out"
logDir = "log"
generateSystem = false
"""


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
    ({"project": "unused"}, "Unknown config option"),
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


def test_config_is_required(tmp_path):
    write_config(tmp_path)
    result = run_tool(SYSTEM, cwd=tmp_path)
    assert result.returncode == 2
    assert "the following arguments are required: --config" in result.stderr
    assert not (tmp_path / "log").exists()


def test_toml_config_is_used(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    config = tmp_path / "system.toml"
    config.write_text(TOML_CONFIG, encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "TSystem.arxml").is_file()


@pytest.mark.parametrize("content, message", [
    ('outputPrefix = "T"\noutputPrefix = "U"\n', "Cannot read config file"),
    ('unknownKey = 1\n', "Unknown config option"),
    (TOML_CONFIG.replace("generateSystem = false", "generateSystem = 'no'"), "config.generateSystem must be"),
])
def test_toml_configuration_errors_exit_with_code_2(tmp_path, content, message):
    path = tmp_path / "system.toml"
    path.write_text(content, encoding="utf-8")
    result = run_tool(SYSTEM, "--config", path)
    assert result.returncode == 2, result.stderr
    assert message in result.stderr
    assert not (tmp_path / "log").exists()


def test_legacy_json_config_with_other_extension_still_works(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    path = write_config(tmp_path).rename(tmp_path / "config.cfg")
    result = run_tool(SYSTEM, "--config", path)
    assert result.returncode == 0, result.stderr


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


def test_case_only_output_prefixes_are_rejected(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    first = write_config(tmp_path, outputPrefix="Pair")
    first.rename(tmp_path / "first.json")
    second = write_config(tmp_path, outputPrefix="pair")
    result = run_tool(SYSTEM, "--config", tmp_path / "first.json", "--config", second)
    assert result.returncode == 2, result.stderr
    assert "Conflicting output path" in result.stderr
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "log").exists()


@pytest.mark.parametrize("unused", [
    {"tresosPreferenceFiles": {"unused": True}},
    {"tresosOutputBaseDir": "${notUsed}", "buildType": ["unused"]},
])
def test_list_mode_ignores_all_unused_eb_options(tmp_path, unused):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, **unused))
    assert result.returncode == 0, result.stderr


def test_cli_overrides_are_applied_before_json_value_validation(tmp_path):
    write_model(tmp_path)
    white = write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    config = write_config(
        tmp_path, outputDir="", logDir=[], arxmlFileList=None, whiteTable=42,
        strictList="invalid", logLevel="invalid", excludeArxml=42, projectRoot=None,
    )
    result = run_tool(
        SYSTEM, "--config", config, "--output-dir", tmp_path / "out",
        "--log-dir", tmp_path / "log", "--project-root", tmp_path,
        "--arxml-file-list", tmp_path / "model.lst", "--white-table", white,
        "--strict-list", "--log-level", "INFO", "--exclude-arxml", "unused.arxml",
    )
    assert result.returncode == 0, result.stderr


def test_cli_list_override_ignores_eb_configuration(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": [["A", "A", "/Pkg/A", "Reference"]]})
    config = write_config(tmp_path, tresosPreferenceFiles={"unused": True}, arxmlFileList=None)
    result = run_tool(SYSTEM, "--config", config, "--arxml-file-list", tmp_path / "model.lst")
    assert result.returncode == 0, result.stderr
