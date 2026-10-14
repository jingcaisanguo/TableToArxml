# 配置与命令行

返回 [README.md](README.md)。工作簿格式见 [workbook-format.md](workbook-format.md)，参数处理见 [cli.py](../../table_to_swc_arxml/table_to_swc_arxml/cli.py)。

运行参数可以写在 JSON 配置文件里，也可以在命令行直接传入；两者同时出现时命令行优先。
配置文件使用 UTF-8 JSON 对象（兼容 BOM），键名区分大小写，不支持注释。

## 参数一览

命令行参数与 JSON 键一一对应，命令行形式是 JSON 键的短横线写法（如 `outputDir` 对应 `--output-dir`），
命名与 `table-to-system-arxml` 统一。参数名须完整书写，不接受缩写。

| JSON 键 | 命令行参数 | 类型 | 必填 / 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| — | `--config` | 路径 | 可选 | JSON 配置文件；绝对路径可单独使用，相对路径须同时传 `--project-root` |
| `defaultVariant` | `--variant` | 字符串 | 可选 | 替换 JSON 路径中的 `${variant}` |
| `projectRoot` | `--project-root` | 目录 | **必填** | 工程根目录；`Target ARXML`、清单内相对条目及命令行相对路径都以它为基准 |
| `arxmlFileList` | `--arxml-file-list` | 文件 | **必填** | ARXML 清单，工具只在清单列出的文件中查找组件和接口 |
| `workbook` | `--workbook` | 文件 | **必填** | Excel 工作簿，格式见 [workbook-format.md](workbook-format.md) |
| `logDir` | `--log-dir` | 目录 | **必填** | 日志目录，不存在时自动创建 |
| `logLevel` | `--log-level` | 字符串 | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR`，不区分大小写 |
| `strictList` | `--strict-list` | 布尔 | `false` | 清单中有无效条目时直接失败，而不是警告后跳过 |
| `excludeArxml` | `--exclude-arxml` | 字符串数组 | `[]` | 排除文件名或相对 `projectRoot` 的路径；命令行可重复，覆盖配置列表 |
| `backend` | `--backend` | 字符串 | `armodel` | ARXML 解析后端，目前只有 `armodel` |
| `dryRun` | `--dry-run` | 布尔 | `false` | 只校验，不写文件 |
| `inPlace` | `--in-place` | 布尔 | `false` | 直接覆盖源 ARXML |
| `outputDir` | `--output-dir` | 目录 | 可选 | 把修改后的 ARXML 按 `projectRoot` 内的相对路径写到该目录，不改源文件 |

- `inPlace`、`outputDir`、`dryRun` 三者最多选一个；都不选时与 `dryRun` 相同，只校验不写文件。
- JSON 中出现表外的键会报错，并列出支持的键。
- 必填项缺失时，参数解析阶段即报错退出（退出码 `2`），不会创建日志。
- 日志文件名为 `table_to_swc_arxml_<时间含微秒>_<进程号>.log`，每次运行新建，启动时打印完整路径。

## 路径规则

排除示例：`"excludeArxml": ["Unused.arxml", "test/common/arxml/BcmArxml/TUR.arxml"]`。
命令行写法：`--exclude-arxml Unused.arxml --exclude-arxml test/common/arxml/BcmArxml/TUR.arxml`。
只写文件名会排除所有同名文件（文件名不区分大小写）；带目录则精确匹配工程根目录下的路径，兼容 `/` 和 `\`，不支持通配符或正则。
排除发生在文件存在性检查之前，日志记录 `Exclude ARXML file`，不修改 lst。
全部排除时报错退出；被排除文件里的组件和接口无法通过清单查找。
此选项不禁止工作簿通过 `Target ARXML` 显式指定该文件作为生成目标。

| 位置 | 相对路径基准 | 支持 `${variant}` 和 `*` |
| --- | --- | --- |
| JSON 中的路径（`projectRoot`、`arxmlFileList`、`workbook`、`logDir`、`outputDir`） | 配置文件所在目录 | 支持 |
| 命令行 `--project-root` | 当前工作目录 | 不支持 |
| 命令行其他路径（`--config`、`--arxml-file-list`、`--workbook`、`--log-dir`、`--output-dir`） | `projectRoot` | 不支持 |
| ARXML 清单中的条目 | `projectRoot` | 不支持 |
| 工作簿中的 `Target ARXML` | `projectRoot`，必须是相对路径，允许 `..` | 不支持 |

JSON 路径按以下顺序处理：

1. 用 `--variant` 的值替换 `${variant}`；未传时使用 `defaultVariant`。用了占位符却没有 variant 时报错。
2. 以配置文件所在目录补全相对路径，允许 `..`；`\` 与 `/` 都可使用。
3. 含 `*`、`?` 时按通配符匹配，必须唯一匹配一个文件（目录类参数匹配目录）；零个或多个匹配都报错，不会自动选第一个。
4. 日志和输出目录可以尚不存在，但这时不能使用通配符。

注意：命令行相对路径的基准与 `table-to-system-arxml` 不同，后者按当前工作目录解析。
相对 `--config` 必须配合显式 `--project-root`，因为读配置前还不知道工程根目录。

## ARXML 清单

`arxmlFileList` 是纯文本，每行一个 ARXML 路径：

- 空行和以 `#` 开头的行被忽略；路径两端的引号会去掉。
- 相对路径以 `projectRoot` 为基准，也可以写绝对路径。
- 扩展名不是 `.arxml` 或文件不存在的条目：默认记录 `WARNING` 并跳过；`strictList=true` 时立即报错退出。
- 同一文件重复出现时只保留第一次（Windows 下不区分大小写），记录 `INFO`。
- 跳过后一个可用文件都不剩时报错退出。

工具只在清单列出的文件中查找组件和端口接口。新建类型或接口时写入的 `Target ARXML` 只要求文件存在；
但如果同一次运行中的端口要引用新建接口，该接口所在文件必须在清单中，否则端口找不到它。

## 配置示例

```json
{
  "defaultVariant": "zcud_m7",
  "projectRoot": "../../..",
  "arxmlFileList": "../arxml_integration/cfg/${variant}.lst",
  "workbook": "excel/swc_arxml_config.xlsx",
  "logDir": "../log",
  "outputDir": "../output/swc",
  "logLevel": "INFO"
}
```

本仓库的测试配置见 [TableToSwcArxml.json](../../test/tableToSwcArxml/TableToSwcArxml.json)，它向共享模型的 LIT、Horn 组件添加测试端口，
结果写到 `test/tableToSwcArxml/output/SwcTest/`，不修改源模型；运行方式见 [test/tableToSwcArxml/README.md](../../test/tableToSwcArxml/README.md)。

## 命令示例

以下使用安装 wheel 后的命令；未安装时可换成 `python table_to_swc_arxml/table_to_swc_arxml.py`，参数相同。

```powershell
# 只用配置文件（推荐绝对路径）
table-to-swc-arxml --config D:/code/project/config/swc_arxml.json

# 相对配置文件需同时给出工程根目录，并切换变体
table-to-swc-arxml --project-root . --config config/swc_arxml.json --variant zcud_m7

# 不用配置文件，只校验
table-to-swc-arxml --project-root . --arxml-file-list path/to/files.lst --workbook path/to/swc.xlsx --log-dir log --dry-run

# 写到隔离目录检查差异
table-to-swc-arxml --project-root . --arxml-file-list path/to/files.lst --workbook path/to/swc.xlsx --log-dir log --output-dir D:\tmp\swc_out

# 确认无误后写回源文件
table-to-swc-arxml --project-root . --arxml-file-list path/to/files.lst --workbook path/to/swc.xlsx --log-dir log --in-place
```

## 部署建议

把 wheel 安装到项目的虚拟环境，配置、工作簿和清单随项目维护，通过绝对路径 `--config` 选择项目。
升级工具只需重新安装 wheel，配置无需修改；项目整体移动时只需改启动命令中的配置路径。
移动 JSON 文件本身会改变其中相对路径的含义。

## 旧配置迁移

参数已与 `table-to-system-arxml` 统一：

| 旧写法 | 新写法 |
| --- | --- |
| `default_variant` | `defaultVariant` |
| `project_dir` / `--project-dir` | `projectRoot` / `--project-root` |
| `list_file` / `--list-file` | `arxmlFileList` / `--arxml-file-list` |
| `log_file` / `--log-file`（日志文件） | `logDir` / `--log-dir`（日志目录） |
| `log_level`、`strict_list`、`in_place`、`output_dir`、`dry_run` | `logLevel`、`strictList`、`inPlace`、`outputDir`、`dryRun` |

`workbook` 和 `logDir` 不再有默认值（旧版默认使用工具目录下的模板和日志），必须显式提供。
