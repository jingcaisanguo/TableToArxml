"""读取配置文件，返回顶层键值；字段校验由各工具负责。

``.toml`` 按 TOML 解析（推荐）；其他扩展名与旧版本一致，按 JSON 解析。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .errors import ConfigError
from .messages import clean_message

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:  # 依赖不完整时只影响 TOML，读取时给出明确提示
        tomllib = None

def read_config_file(path: Path) -> dict:
    is_toml = path.suffix.lower() == ".toml"
    if is_toml and tomllib is None:
        raise ConfigError(
            f"Reading TOML config {path} on Python < 3.11 requires the 'tomli' package; "
            f"install the project dependencies in this interpreter ({sys.executable})")
    try:
        # TOML 规范要求 UTF-8；两种格式都容忍 Windows 编辑器写入的 BOM。
        text = path.read_text(encoding="utf-8-sig")
        config = tomllib.loads(text) if is_toml else json.loads(text)
    except (OSError, UnicodeError, ValueError) as error:
        raise ConfigError(f"Cannot read config file {path}: {clean_message(error)}") from error
    if not isinstance(config, dict):
        raise ConfigError(f"Config file must contain a top-level object: {path}")
    return config
