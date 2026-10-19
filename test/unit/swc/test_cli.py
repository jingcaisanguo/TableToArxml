"""SWC 命令行：参数错误退出码 2、不在依赖检查前导入 armodel。"""

from __future__ import annotations

import json

import pytest

from helpers import ROOT, SWC, run_python, run_tool

CONFIG = ROOT / "test/samples/swc/TableToSwcArxml.toml"


def test_help_and_option_parsing_do_not_import_armodel():
    code = (
        "import sys\n"
        "from table_to_arxml.swc import cli, options\n"
        "options.parse_args(['--project-root', '.', '--workbook', 'w.xlsx', '--arxml-file-list', 'a.lst',"
        " '--log-dir', 'log'])\n"
        "assert 'armodel' not in sys.modules\n"
        "try:\n    cli.main(['--help'])\nexcept SystemExit as exit_:\n    assert exit_.code == 0\n"
        "assert 'armodel' not in sys.modules"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr


def test_missing_required_options_exit_with_code_2(tmp_path):
    result = run_tool(SWC, "--project-root", tmp_path)
    assert result.returncode == 2
    assert "--arxml-file-list, --workbook, --log-dir required" in result.stderr


def test_removed_backend_flag_is_rejected(tmp_path):
    result = run_tool(SWC, "--config", CONFIG, "--backend", "armodel", "--log-dir", tmp_path / "log")
    assert result.returncode == 2
    assert "unrecognized arguments: --backend armodel" in result.stderr
    assert not (tmp_path / "log").exists()


def test_removed_backend_config_key_is_rejected(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"backend": "armodel"}), encoding="utf-8")
    result = run_tool(SWC, "--config", config, "--log-dir", tmp_path / "log")
    assert result.returncode == 2
    assert "Unknown config option(s)" in result.stderr and "backend" in result.stderr
    assert not (tmp_path / "log").exists()


def test_json_config_is_still_supported(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "projectRoot": str(ROOT), "arxmlFileList": str(ROOT / "test/data/common/arxml.lst"),
        "workbook": str(CONFIG.parent / "SwcTestPorts.xlsx"), "logDir": "log", "dryRun": True,
    }), encoding="utf-8")
    result = run_tool(SWC, "--config", config)
    assert result.returncode == 0, result.stderr
    assert "Dry run successful" in result.stdout


@pytest.mark.parametrize("content, message", [
    ("strictList = 'yes'\n", "strictList must be true or false"),
    ("workbook = \n", "Cannot read config file"),
    ("backend = 'armodel'\n", "Unknown config option(s)"),
])
def test_toml_configuration_errors_exit_with_code_2(tmp_path, content, message):
    config = tmp_path / "config.toml"
    config.write_text(
        f"projectRoot = '.'\narxmlFileList = 'a.lst'\nlogDir = 'log'\n{content}", encoding="utf-8")
    result = run_tool(SWC, "--config", config, "--workbook", tmp_path / "w.xlsx")
    assert result.returncode == 2
    assert message in result.stderr
    assert not (tmp_path / "log").exists()


def test_cli_output_choice_replaces_config_output_choice(tmp_path):
    # 配置中写了 outputDir，命令行的 --dry-run 应整体替换它，而不是报互斥错误。
    result = run_tool(SWC, "--config", CONFIG, "--dry-run", "--log-dir", tmp_path / "log")
    assert result.returncode == 0, result.stderr
    assert "Dry run successful" in result.stdout
    in_place = run_tool(SWC, "--config", CONFIG, "--dry-run", "--in-place", "--log-dir", tmp_path / "log")
    assert in_place.returncode == 2
    assert "not allowed with argument" in in_place.stderr


def test_program_name_is_the_command_name(tmp_path):
    result = run_tool(SWC, "--project-root", tmp_path)
    assert result.stderr.startswith("usage: table-to-swc-arxml")
