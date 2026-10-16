"""System 工具端到端测试：运行 BCM（绿表）和 MC（白表）样例，与 expected/ 下的基线比对。"""

from __future__ import annotations

import pytest

from helpers import ROOT, SYSTEM, assert_matches_baseline, run_tool


@pytest.mark.parametrize("variant, prefix", [("BcmTest", "Bcm"), ("McTest", "Mc")])
def test_system_sample(tmp_path, variant, prefix):
    result = run_tool(
        SYSTEM, "--config", ROOT / f"test/tableToSystemArxml/{prefix}TableToSystemArxmlConfig.json",
        "--output-dir", tmp_path / "out", "--log-dir", tmp_path / "log",
    )
    assert result.returncode == 0, result.stderr
    expected_dir = ROOT / "test/tableToSystemArxml/expected" / variant
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == sorted(
        path.name for path in expected_dir.iterdir())
    for expected in expected_dir.iterdir():
        assert_matches_baseline(tmp_path / "out" / expected.name, expected)
