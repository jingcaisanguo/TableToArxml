# 配置与命令行

返回 [README.md](README.md)。工作簿格式见 [workbook-format.md](workbook-format.md)。

运行参数可以写在配置文件里，也可以在命令行直接传入；两者同时出现时命令行优先。
配置文件使用 TOML 格式（`.toml`，可用 `#` 写注释），格式规则见 [配置文件格式](../README.md#配置文件格式)。键名区分大小写。

## 参数一览

命令行参数与配置键一一对应，命令行形式是配置键的短横线写法（如 `outputDir` 对应 `--output-dir`），
命名与 `table-to-system-arxml` 统一。参数名须完整书写，不接受缩写。

| 配置键 | 命令行参数 | 类型 | 必填 / 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| — | `--config` | 路径 | 可选 | TOML 配置文件；相对路径按当前工作目录解析 |
| `defaultVariant` | `--variant` | 字符串 | 可选 | 替换配置文件路径中的 `${variant}` |
| `projectRoot` | `--project-root` | 目录 | **必填** | 工程根目录；`Target ARXML`、清单内相对条目和 `excludeArxml` 的路径都以它为基准 |
| `arxmlFileList` | `--arxml-file-list` | 文件 | **必填** | ARXML 清单，工具只在清单列出的文件中查找组件和接口 |
| `workbook` | `--workbook` | 文件 | **必填** | Excel 工作簿，格式见 [workbook-format.md](workbook-format.md) |
| `logDir` | `--log-dir` | 目录 | **必填** | 日志目录，不存在时自动创建 |
| `logLevel` | `--log-level` | 字符串 | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR`，不区分大小写 |
| `strictList` | `--strict-list` | 布尔 | `false` | 清单中有缺失、非 `.arxml` 或无匹配的条目时直接失败，而不是警告后跳过 |
| `excludeArxml` | `--exclude-arxml` | 字符串数组 | `[]` | 排除文件名或相对 `projectRoot` 的路径；命令行可重复，覆盖配置列表 |
| `dryRun` | `--dry-run` | 布尔 | `false` | 只校验，不写文件；与三者都不选等价，用于显式声明 |
| `inPlace` | `--in-place` | 布尔 | `false` | 直接覆盖源 ARXML |
| `outputDir` | `--output-dir` | 目录 | 可选 | 把修改后的 ARXML 按 `projectRoot` 内的相对路径写到该目录，不改源文件 |

- `inPlace`、`outputDir`、`dryRun` 三者最多选一个；都不选时与 `dryRun` 相同，只校验不写文件。
- 命令行指定了其中任一项时，整体替换配置中的输出方式。例如配置写了 `outputDir`，命令行加 `--dry-run` 即只校验，不报互斥错误。
- 配置文件中出现表外的键会报错，并列出支持的键。
- 必填项缺失、配置文件无法读取（含语法错误）或取值非法时，参数解析阶段即打印用法并退出（退出码 `2`），不会创建日志。
- 依赖（armodel 版本等）在创建日志后检查，不满足时记录 `ERROR` 并返回 `1`；`--help` 不受依赖影响。
- 日志文件名为 `table_to_swc_arxml_<时间含微秒>_<进程号>.log`，每次运行新建，启动时打印完整路径。

## 路径规则

排除示例：`excludeArxml = ["Unused.arxml", "arxml/Bcm/TUR.arxml"]`。
命令行写法：`--exclude-arxml Unused.arxml --exclude-arxml arxml/Bcm/TUR.arxml`。
只写文件名会排除所有同名文件（文件名不区分大小写）；带目录则精确匹配工程根目录下的路径，兼容 `/` 和 `\`，不支持通配符或正则。
被排除的条目即使不存在也不会告警，日志记录 `Exclude ARXML file`，不修改 lst。
全部排除时报错退出；被排除文件里的组件和接口无法通过清单查找。
此选项不禁止工作簿通过 `Target ARXML` 显式指定该文件作为生成目标。

| 位置 | 相对路径基准 | 支持 `${variant}` 和 `*` |
| --- | --- | --- |
| 配置文件中的路径（`projectRoot`、`arxmlFileList`、`workbook`、`logDir`、`outputDir`） | 配置文件所在目录 | 支持 |
| 命令行路径（`--config`、`--project-root`、`--arxml-file-list`、`--workbook`、`--log-dir`、`--output-dir`） | 当前工作目录 | 不支持 |
| ARXML 清单中的条目 | `projectRoot` | 不支持 `${variant}`；支持通配符 |
| 工作簿中的 `Target ARXML` | `projectRoot`，必须是相对路径，允许 `..` | 不支持 |

配置文件中的路径按以下顺序处理：

1. 用 `--variant` 的值替换 `${variant}`；未传时使用 `defaultVariant`。用了占位符却没有 variant 时报错。
2. 以配置文件所在目录补全相对路径，允许 `..`；`\` 与 `/` 都可使用。
3. 路径按字面存在时优先使用，包括名称中有方括号的文件或目录；否则展开 `*`、`?`、`[...]`、`**` 通配符，基准目录按字面处理。必须唯一匹配一个文件（目录类参数匹配目录），零个或多个匹配都报错。
4. 日志和输出目录可以尚不存在，但这时不能使用通配符。

命令行相对路径的基准与 `table-to-system-arxml` 相同，都是当前工作目录。

## ARXML 清单

`arxmlFileList` 是 UTF-8 纯文本（兼容 BOM），每行一个 ARXML 路径；读取规则与 `table-to-system-arxml` 相同：

- 空行和以 `#` 开头的行被忽略；路径两端的引号会去掉，`\` 与 `/` 都可使用。
- 相对路径以 `projectRoot` 为基准，也可以写绝对路径。
- 条目可以含通配符（`*`、`?`、`[...]`，递归 `**`），只保留扩展名为 `.arxml` 的文件。
- 文件不存在、扩展名不是 `.arxml` 或通配符没有匹配的条目：默认记录 `WARNING` 并跳过；`strictList=true` 时立即报错退出。
- 同一文件重复出现时只保留第一次（Windows 下不区分大小写），记录 `INFO`。
- 跳过后一个可用文件都不剩时报错退出（`No usable ARXML files found`）。

工具只在清单列出的文件中查找组件和端口接口。新建类型或接口时写入的 `Target ARXML` 只要求文件存在；
但如果同一次运行中的端口要引用新建接口，该接口所在文件必须在清单中，否则端口找不到它。

## 配置示例

```toml
# swc_arxml.toml：相对路径以本文件所在目录为基准
defaultVariant = "variantA"
projectRoot = ".."
arxmlFileList = "../cfg/${variant}.lst"
workbook = "excel/swc_arxml_config.xlsx"
logDir = "../log"
# 输出方式三选一：outputDir、inPlace = true、dryRun = true
outputDir = "../output/swc"
logLevel = "INFO"
```

## 命令示例

命令不在 `PATH` 时可换成同一虚拟环境的 `python -m table_to_arxml.swc`，参数相同。

```powershell
# 只用配置文件
table-to-swc-arxml --config D:/code/project/config/swc_arxml.toml

# 相对当前目录的配置文件，并切换变体
table-to-swc-arxml --config config/swc_arxml.toml --variant variantA

# 不用配置文件，只校验
table-to-swc-arxml --project-root . --arxml-file-list path/to/files.lst --workbook path/to/swc.xlsx --log-dir log --dry-run

# 写到隔离目录检查差异
table-to-swc-arxml --project-root . --arxml-file-list path/to/files.lst --workbook path/to/swc.xlsx --log-dir log --output-dir D:\tmp\swc_out

# 确认无误后写回源文件
table-to-swc-arxml --project-root . --arxml-file-list path/to/files.lst --workbook path/to/swc.xlsx --log-dir log --in-place
```

## 部署建议

把 wheel 安装到项目的虚拟环境，配置、工作簿和清单随项目维护，通过绝对路径 `--config` 选择项目。
兼容版本升级通常只需重新安装 wheel；跨版本升级前仍应检查 [迁移说明](../migration.md)，不能保证所有旧配置和输入都无需调整。项目整体移动时，若全部项目路径都是相对路径，只需调整启动命令中的配置路径。
移动配置文件本身会改变其中相对路径的含义。
