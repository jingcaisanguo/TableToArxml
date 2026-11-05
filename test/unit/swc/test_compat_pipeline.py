"""处理流程的路径兼容性回归：输出目录中的清单文件排除不依赖 ``Path.resolve()``。"""

from __future__ import annotations

import logging
from pathlib import Path

from table_to_arxml.swc.pipeline import exclude_output_files


def test_output_directory_with_wildcard_characters_does_not_fail(tmp_path):
    # Python 3.9 的 Windows 版 Path.resolve() 对含通配符的路径抛出 WinError 123。
    source = tmp_path / "a.arxml"
    source.touch()
    assert exclude_output_files([source], tmp_path / "out*", tmp_path) == [source]


def test_files_below_a_missing_relative_output_directory_are_kept(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "a.arxml"
    source.touch()
    assert exclude_output_files([source], Path("out"), tmp_path) == [source]


def test_output_files_are_excluded_through_relative_paths(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    source, previous = tmp_path / "a.arxml", tmp_path / "out" / "a.arxml"
    source.touch()
    previous.touch()
    assert exclude_output_files([source, previous], Path("out"), tmp_path) == [source]
    assert f"Exclude ARXML file under outputDir: {previous}" in caplog.text
