# 表格转 ARXML 工具使用说明

本目录面向工具使用者。交付时同时提供同版本的 wheel 与
`table_to_arxml-<版本>-user-guide.zip`；后者包含本目录的全部说明和 [SWC 空白模板](swc/SwcTemplate.xlsx)，解压后从本页开始阅读。
文档和模板不在 wheel 内；从源码分发时可直接复制整个 `docs/user` 目录并保留结构。
工具说明统一位于 `system`、`swc`，不再保留旧路径跳转页或重复模板。
升级前请阅读 [迁移说明](migration.md)。两个命令行工具随同一个 wheel 安装：

| 命令 | 用途 | 说明 |
| --- | --- | --- |
| `table-to-system-arxml` | 绿表 → 白表 Excel → Composition/System ARXML，也可直接输入白表 | [System 使用说明](system/README.md) |
| `table-to-swc-arxml` | 按 Excel 工作簿向已有 ARXML 增量添加端口、接口、数据类型及 RTE 访问对象 | [SWC 使用说明](swc/README.md) |

表格格式：

- [绿表规则](system/green-table-rules.md)
- [白表格式](system/white-table-format.md)
- [SWC 工作簿格式](swc/workbook-format.md)

## 安装

最低要求为 Python 3.9，建议新环境使用仍受维护的 Python 3.13 并安装到项目自己的虚拟环境。
最低要求不等于所有版本均已验证；CI 矩阵为 Windows/Ubuntu、Python 3.9/3.13、armodel 1.8.7/1.9.2。
从 GitHub Release 下载 `table_to_arxml-<版本>-py3-none-any.whl` 后执行：

```powershell
python -m pip install ./table_to_arxml-0.3.1-py3-none-any.whl
table-to-system-arxml --help
table-to-swc-arxml --help
```

也可以直接用 Release 中 wheel 附件的下载链接安装，或在项目的 `requirements.txt` 中固定版本：

```text
table-to-arxml @ <Release 中 table_to_arxml-0.3.1-py3-none-any.whl 的下载链接>
```

- 安装时自动安装依赖：`armodel>=1.8.7,<1.9.4`、`openpyxl>=3.1.2`、`packaging>=21.3`。实测 armodel 1.8.7 和 1.9.2 可用；从索引安装时 pip 会在该范围内选最新版，范围外的版本在工具启动时报错。
- wheel 本身跨平台，但依赖需要适合目标平台的发行包；离线环境需提前下载依赖。
- 私有仓库先通过已认证的 GitHub 客户端或 CI 下载 wheel，再用本地路径安装；不要把令牌写进 URL、配置或版本库。

## 调用方式

| 调用方式 | 说明 |
| --- | --- |
| `table-to-system-arxml`、`table-to-swc-arxml` | 安装后的命令 |
| `python -m table_to_arxml.system`、`python -m table_to_arxml.swc` | 命令不在 `PATH` 时使用，参数相同 |

- 两个工具可在命令行覆盖的 JSON 项使用统一命名（如 `outputDir` 对应 `--output-dir`），命令行优先，参数名须完整书写。并非所有 JSON 项都有命令行参数；System 的接口映射等选项只在 JSON 中配置，完整列表见各工具说明。
- JSON 中的相对路径以配置文件所在目录为基准；命令行相对路径以当前工作目录为基准。
- 日志目录必须通过 `--log-dir` 或配置指定；`table-to-system-arxml` 的产物目录也必须显式指定。安装后的命令不会向 `site-packages` 写配置、产物或日志。
- 日志同时写入日志文件和控制台：普通信息到 stdout，`WARNING`、`ERROR` 到 stderr，每行为 `<时间戳> <等级>: <消息>`。
- 两个工具读取 ARXML 清单（`arxmlFileList`）的规则相同，见各工具的配置说明；`strictList` / `--strict-list` 使无效条目直接失败。
- 退出码：`0` 成功；`1` 工具自身运行或校验失败，不发布本次产物；`2` 命令行或配置文件错误，打印用法，不创建日志。System 工具的 armodel 解析器诊断即使以 `ERROR` 级别打印，也不单独导致退出码 `1`；详见 [System 日志与退出码说明](system/arxml-and-errors.md#日志与退出码)。
- EB 模式（由 tresos 导入器生成 ARXML 清单）需要调用方环境提供 `PrefSystemImporter`，wheel 不包含 EB 工具。
