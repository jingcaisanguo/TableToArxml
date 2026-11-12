"""System 运行结果：dry-run、逐行结果工作簿（含绿表来源）、单侧声明提示、发布路径和结论行。"""

from __future__ import annotations

import json

from openpyxl import Workbook, load_workbook

from helpers import SYSTEM, run_tool
from system_fixtures import write_config, write_model, write_white_table

COMPONENTS = [["A", "A", "/Pkg/A", "Reference"], ["B", "B", "/Pkg/B", "Reference"]]
MAPPING = {"providerInterface": "/Pkg/IfA", "requesterInterface": "/Pkg/IfB", "dataElements": {"a": "b"}}
GENERAL = {
    "Composition": "/Pkg/Top", "System": "/Pkg/Sys",
    "RootSwCompositionPrototype": "/Pkg/Sys/RootProto", "EcuInstance": "/Pkg/Ecu",
}


def green_table(path, receive=(), send=()):
    workbook = Workbook()
    workbook.active.title = "R-Port"
    workbook.create_sheet("P-Port")
    for sheet in workbook:
        sheet.append(["", "Port", "Module", "", "", "", "", "DataElement", "Component", "Port", "Remark"])
    for sheet, rows in (("R-Port", receive), ("P-Port", send)):
        for port, peer, peer_port in rows:
            workbook[sheet].append(["", port, "SWC", "", "", "", "", "", peer, peer_port, ""])
    workbook.save(path)
    workbook.close()


def green_config(tmp_path, **options):
    config = write_config(
        tmp_path, greenTables=["A.xlsx", "B.xlsx"], general=GENERAL, componentAliases={"A": "/Pkg/Top/A"},
        interfaceMismatch="map", interfaceMappings=[MAPPING], **options)
    contents = json.loads(config.read_text(encoding="utf-8"))
    del contents["whiteTable"]
    config.write_text(json.dumps(contents), encoding="utf-8")
    return config


def result_sheet(tmp_path, title):
    [path] = (tmp_path / "log").glob("*_result.xlsx")
    workbook = load_workbook(path)
    try:
        sheet = workbook[title]
        headers = [cell.value for cell in sheet[1]]
        return {
            row[0].row: dict(zip(headers, (cell.value for cell in row)))
            for row in sheet.iter_rows(min_row=2) if any(cell.value is not None for cell in row)
        }
    finally:
        workbook.close()


def test_green_rows_show_generated_connector_and_one_sided_hint(tmp_path):
    write_model(tmp_path)
    green_table(tmp_path / "A.xlsx", send=[("pOut", "B", "rIn")])
    green_table(tmp_path / "B.xlsx")
    result = run_tool(SYSTEM, "--config", green_config(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "1 component connection(s) are declared in only one of the two green tables" in result.stderr
    assert f"Published: {tmp_path / 'out' / 'TSystem.arxml'}" in result.stdout
    assert "RESULT: SUCCESS - table_to_system_arxml: 0 error(s)," in result.stdout
    assert "3 artifact(s) published" in result.stdout
    assert "Saving" not in result.stdout
    row = result_sheet(tmp_path, "A_P-Port")[2]
    assert row["结果"] == "新建"
    assert row["对象"] == "/Pkg/Top/ac_A_pOut_B_rIn"
    assert row["目标文件"] == str(tmp_path / "out" / "TSystem.arxml")
    assert "对端绿表 B 的 R-Port 页没有声明这条连接" in row["说明"]
    white = result_sheet(tmp_path, "TW_AssemblySwConnection")
    assert white[2]["结果"] == "新建"
    components = result_sheet(tmp_path, "TW_Components")
    assert {row["结果"] for row in components.values()} == {"复用已有对象（有警告）"}
    assert "has no SWC-INTERNAL-BEHAVIOR" in components[2]["说明"]
    [diff] = (tmp_path / "log").glob("*.diff")
    assert "New file" in diff.read_text(encoding="utf-8")

    repeated = run_tool(SYSTEM, "--config", green_config(tmp_path))
    assert repeated.returncode == 0, repeated.stderr
    diffs = sorted((tmp_path / "log").glob("*.diff"))
    assert any("=== Unchanged" in path.read_text(encoding="utf-8") for path in diffs)


def test_both_sides_declared_has_no_hint(tmp_path):
    write_model(tmp_path)
    green_table(tmp_path / "A.xlsx", send=[("pOut", "B", "rIn")])
    green_table(tmp_path / "B.xlsx", receive=[("rIn", "A", "pOut")])
    result = run_tool(SYSTEM, "--config", green_config(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "declared in only one" not in result.stderr
    assert result_sheet(tmp_path, "B_R-Port")[2]["结果"] == "新建"


def test_green_row_error_points_to_the_green_row(tmp_path):
    write_model(tmp_path)
    green_table(tmp_path / "A.xlsx", send=[("pOut", "Nope", "rIn")])
    green_table(tmp_path / "B.xlsx")
    result = run_tool(SYSTEM, "--config", green_config(tmp_path))
    assert result.returncode == 1
    assert "unresolved component 'Nope'" in result.stderr and "[A.xlsx P-Port!2]" in result.stderr
    assert "RESULT: FAILED - table_to_system_arxml: 1 error(s)" in result.stdout
    assert result_sheet(tmp_path, "A_P-Port")[2]["结果"] == "错误"
    assert not (tmp_path / "out").exists()


def test_dry_run_without_output_dir_publishes_nothing(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": COMPONENTS, "AssemblySwConnection": [["A", "pOut", "B", "rIn"]]})
    config = write_config(tmp_path, interfaceMismatch="skip", dryRun=True)
    contents = json.loads(config.read_text(encoding="utf-8"))
    del contents["outputDir"]
    config.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config)
    assert result.returncode == 0, result.stderr
    assert "RESULT: DRY-RUN - table_to_system_arxml" in result.stdout
    assert "nothing published" in result.stdout
    assert not (tmp_path / "out").exists()
    rows = result_sheet(tmp_path, "TW_AssemblySwConnection")
    assert rows[2]["结果"] == "按配置跳过（有警告）"
    assert {row["结果"] for row in result_sheet(tmp_path, "TW_Components").values()} == {"引用已有实例"}


def test_cli_dry_run_overrides_config_and_keeps_existing_outputs(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": COMPONENTS})
    config = write_config(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "TSystem.arxml").write_text("old", encoding="utf-8")
    result = run_tool(SYSTEM, "--config", config, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would be published to" in result.stdout
    assert (tmp_path / "out" / "TSystem.arxml").read_text(encoding="utf-8") == "old"
    [diff] = (tmp_path / "log").glob("*.diff")
    assert "-old" in diff.read_text(encoding="utf-8")


def test_dry_run_must_agree_across_configs(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {"Components": COMPONENTS})
    first = write_config(tmp_path, dryRun=True)
    second = tmp_path / "second.json"
    contents = json.loads(first.read_text(encoding="utf-8"))
    contents.update(outputPrefix="U", dryRun=False)
    second.write_text(json.dumps(contents), encoding="utf-8")
    result = run_tool(SYSTEM, "--config", first, "--config", second)
    assert result.returncode == 2
    assert "config.dryRun differs across configs" in result.stderr


def test_white_row_error_is_located_and_marked(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS,
        "AssemblySwConnection": [["A", "pOut", "B", "rIn"], ["A", "pOut", "X", "rIn"]],
    })
    result = run_tool(SYSTEM, "--config", write_config(tmp_path, interfaceMismatch="skip"))
    assert result.returncode == 1
    assert "[white.xlsx AssemblySwConnection!3]" in result.stderr
    assert "RESULT: FAILED - table_to_system_arxml: 1 error(s)" in result.stdout
    rows = result_sheet(tmp_path, "TW_AssemblySwConnection")
    assert rows[3]["结果"] == "错误" and "unknown component 'X'" in rows[3]["说明"]


def test_receiver_with_several_senders_is_hinted(tmp_path):
    write_model(tmp_path)
    write_white_table(tmp_path, {
        "Components": COMPONENTS,
        "AssemblySwConnection": [["A", "pOut", "B", "rIn"], ["A", "pWide", "B", "rIn"]],
    })
    wide = {"providerInterface": "/Pkg/IfC", "requesterInterface": "/Pkg/IfB", "dataElements": {"c": "b"}}
    result = run_tool(SYSTEM, "--config", write_config(
        tmp_path, interfaceMismatch="map", interfaceMappings=[MAPPING, wide]))
    assert result.returncode == 0, result.stderr
    rows = result_sheet(tmp_path, "TW_AssemblySwConnection")
    for number in (2, 3):
        assert rows[number]["结果"] == "新建"
        assert "R-port B.rIn 连接了 2 个发送方" in rows[number]["说明"]
