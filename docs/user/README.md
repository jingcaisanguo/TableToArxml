# 表格转 ARXML 工具使用说明

本说明面向工具使用者，可单独提供给外部。两个命令行工具随同一个 wheel 安装：

| 命令 | 用途 | 说明 |
| --- | --- | --- |
| `table-to-system-arxml` | 绿表 → 白表 Excel → Composition/System ARXML，也可直接输入白表 | [tableToSystemArxml/README.md](tableToSystemArxml/README.md) |
| `table-to-swc-arxml` | 按 Excel 工作簿向已有 ARXML 增量添加端口、接口、数据类型及 RTE 访问对象 | [tableToSwcArxml/README.md](tableToSwcArxml/README.md) |

表格格式：

- 绿表：[tableToSystemArxml/green-table-rules.md](tableToSystemArxml/green-table-rules.md)
- 白表：[tableToSystemArxml/white-table-format.md](tableToSystemArxml/white-table-format.md)
- SWC 工作簿：[tableToSwcArxml/workbook-format.md](tableToSwcArxml/workbook-format.md)

## 安装

需要 Python 3.9 或更新版本，建议安装到项目自己的虚拟环境。从 GitHub Release 下载 `table_to_arxml-<版本>-py3-none-any.whl` 后执行：

```powershell
python -m pip install ./table_to_arxml-0.2.0-py3-none-any.whl
table-to-system-arxml --help
table-to-swc-arxml --help
```

也可以直接用 Release 中 wheel 附件的下载链接安装，或在项目的 `requirements.txt` 中固定版本：

```text
table-to-arxml @ <Release 中 table_to_arxml-0.2.0-py3-none-any.whl 的下载链接>
```

- 安装时自动安装依赖：`armodel>=1.8.7,<1.9.4`、`openpyxl>=3.1.2`、`packaging>=21.3`。实测 armodel 1.8.7 和 1.9.2 可用；从索引安装时 pip 会在该范围内选最新版，范围外的版本在工具启动时报错。
- wheel 本身跨平台，但依赖需要适合目标平台的发行包；离线环境需提前下载依赖。
- 私有仓库先通过已认证的 GitHub 客户端或 CI 下载 wheel，再用本地路径安装；不要把令牌写进 URL、配置或版本库。
- 0.2.0 之前两个工具分别发布为 `table-to-system-arxml`、`table-to-swc-arxml` 两个包。升级前先卸载旧包：`python -m pip uninstall table-to-system-arxml table-to-swc-arxml`，否则日后卸载旧包会删掉新包的文件。

## 调用方式

| 调用方式 | 说明 |
| --- | --- |
| `table-to-system-arxml`、`table-to-swc-arxml` | 安装后的命令 |
| `python -m table_to_system_arxml`、`python -m table_to_swc_arxml` | 命令不在 `PATH` 时使用，参数相同 |

- 两个工具的命令行参数都与 JSON 配置键一一对应（如 `outputDir` 对应 `--output-dir`），命令行优先，参数名须完整书写。
- JSON 中的相对路径以配置文件所在目录为基准；命令行相对路径以当前工作目录为基准。
- 日志目录必须通过 `--log-dir` 或配置指定；`table-to-system-arxml` 的产物目录也必须显式指定。安装后的命令不会向 `site-packages` 写配置、产物或日志。
- 退出码：`0` 成功，`1` 运行失败（存在 ERROR），`2` 参数错误。
- EB 模式（由 tresos 导入器生成 ARXML 清单）需要调用方环境提供 `PrefSystemImporter`，wheel 不包含 EB 工具。
