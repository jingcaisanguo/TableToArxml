"""SWC 命令行：参数错误退出码 2、已废弃的 backend、不在依赖检查前导入 armodel。"""

from __future__ import annotations

from helpers import ROOT, SWC, run_python, run_tool

CONFIG = ROOT / "test/samples/swc/TableToSwcArxml.json"


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


def test_unknown_backend_is_a_configuration_error(tmp_path):
    result = run_tool(SWC, "--config", CONFIG, "--backend", "other", "--log-dir", tmp_path / "log")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_deprecated_backend_is_reported_and_ignored(tmp_path):
    result = run_tool(
        SWC, "--config", CONFIG, "--backend", "armodel", "--dry-run", "--log-dir", tmp_path / "log",
    )
    assert result.returncode == 0, result.stderr
    assert "backend is deprecated and ignored" in result.stderr
    assert "Dry run successful" in result.stdout


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
