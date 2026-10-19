"""配置文件读取：.toml 按 TOML 解析，其他扩展名兼容旧版本按 JSON 解析，错误统一为 ConfigError。"""

from __future__ import annotations

import pytest

from table_to_arxml.common import config_file
from table_to_arxml.common.config_file import read_config_file
from table_to_arxml.common.errors import ConfigError

TOML = """\
# 注释
name = "A"   # 行尾注释
enabled = true
items = ["a", "b",]

[section]
path = 'D:\\Work\\Project'
"""


def test_toml_and_json_give_the_same_values(tmp_path):
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(TOML, encoding="utf-8")
    json_path = tmp_path / "config.json"
    json_path.write_text(
        '{"name": "A", "enabled": true, "items": ["a", "b"], "section": {"path": "D:\\\\Work\\\\Project"}}',
        encoding="utf-8")
    expected = {"name": "A", "enabled": True, "items": ["a", "b"], "section": {"path": "D:\\Work\\Project"}}
    assert read_config_file(toml_path) == expected
    assert read_config_file(json_path) == expected


@pytest.mark.parametrize("name", ["config.toml", "config.json", "CONFIG.TOML"])
def test_utf8_bom_is_accepted(tmp_path, name):
    path = tmp_path / name
    content = 'name = "A"' if name.lower().endswith(".toml") else '{"name": "A"}'
    path.write_text(content, encoding="utf-8-sig")
    assert read_config_file(path) == {"name": "A"}


@pytest.mark.parametrize("name, content, message", [
    ("config.toml", 'name = "A"\nname = "B"\n', "Cannot read config file"),
    ("config.toml", "name = A\n", "Cannot read config file"),
    ("config.json", "{not json", "Cannot read config file"),
    ("config.json", "[1, 2]", "must contain a top-level object"),
    ("config.cfg", "name = A", "Cannot read config file"),
])
def test_invalid_config_files_raise_config_error(tmp_path, name, content, message):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        read_config_file(path)


@pytest.mark.parametrize("name", ["config.cfg", "config", "CONFIG.JSON"])
def test_non_toml_extensions_are_read_as_json(tmp_path, name):
    path = tmp_path / name
    path.write_text('{"name": "A"}', encoding="utf-8")
    assert read_config_file(path) == {"name": "A"}


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read config file"):
        read_config_file(tmp_path / "missing.toml")


def test_missing_toml_parser_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('name = "A"', encoding="utf-8")
    monkeypatch.setattr(config_file, "tomllib", None)
    with pytest.raises(ConfigError, match="requires the 'tomli' package"):
        read_config_file(path)
    json_path = tmp_path / "config.json"
    json_path.write_text('{"name": "A"}', encoding="utf-8")
    assert read_config_file(json_path) == {"name": "A"}
