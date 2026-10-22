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
最低要求不等于所有版本均已验证；CI 矩阵为 Windows/Ubuntu、Python 3.9/3.13、armodel 1.8.7/1.9.2/1.9.8。
从 GitHub Release 下载 `table_to_arxml-<版本>-py3-none-any.whl` 后执行：

```powershell
python -m pip install --upgrade --upgrade-strategy eager ./table_to_arxml-<版本>-py3-none-any.whl
table-to-system-arxml --help
table-to-swc-arxml --help
```

也可以直接用 Release 中 wheel 附件的下载链接安装，或在项目的 `requirements.txt` 中固定版本：

```text
table-to-arxml @ <Release 中 table_to_arxml-<版本>-py3-none-any.whl 的下载链接>
```

- 安装时自动安装依赖：`armodel>=1.8.7,<=1.9.8,!=1.9.1`、`openpyxl>=3.1.2`、`packaging>=21.3`，Python 3.11 以下另装 `tomli>=1.1.0`（读取 TOML 配置）。armodel 上限固定在已验证的 `1.9.8`，排除包入口不完整的 `1.9.1`；新版本须补充兼容测试、通过后提交更新版本上限。新环境从索引安装时 pip 默认选择约束范围内可用的最新版；上面的 `--upgrade --upgrade-strategy eager` 也会升级已有环境中满足约束但较旧的依赖（不限于 armodel）。范围外的 armodel 在工具启动时报错。
- wheel 本身跨平台，但依赖需要适合目标平台的发行包；离线环境需提前下载依赖。
- 私有仓库先通过已认证的 GitHub 客户端或 CI 下载 wheel，再用本地路径安装；不要把令牌写进 URL、配置或版本库。

## 调用方式

| 调用方式 | 说明 |
| --- | --- |
| `table-to-system-arxml`、`table-to-swc-arxml` | 安装后的命令 |
| `python -m table_to_arxml.system`、`python -m table_to_arxml.swc` | 命令不在 `PATH` 时使用，参数相同 |

`table_to_arxml.system` 和 `table_to_arxml.swc` 是 Python 模块路径，不是独立 shell 命令。运行模块时必须通过 Python 的 `-m` 选项，例如：

```powershell
python -m table_to_arxml.system --config ./config/system.toml
python -m table_to_arxml.swc --config ./config/swc.toml
```

使用虚拟环境时，确保 `python` 来自安装 wheel 的同一个环境。`pyproject.toml` 注册的安装命令是 `table-to-system-arxml` 和 `table-to-swc-arxml`，并不会创建名为 `table_to_arxml.system` 的终端命令。

- 两个工具的配置文件使用 TOML 格式，见下文 [配置文件格式](#配置文件格式)。
- 两个工具可在命令行覆盖的配置项使用统一命名（如 `outputDir` 对应 `--output-dir`），命令行优先，参数名须完整书写。并非所有配置项都有命令行参数；System 的接口映射等选项只在配置文件中设置，完整列表见各工具说明。
- 配置文件中的相对路径以配置文件所在目录为基准；命令行相对路径以当前工作目录为基准。
- 日志目录必须通过 `--log-dir` 或配置指定；`table-to-system-arxml` 的产物目录也必须显式指定。安装后的命令不会向 `site-packages` 写配置、产物或日志。
- 日志同时写入日志文件和控制台：普通信息到 stdout，`WARNING`、`ERROR` 到 stderr，每行为 `<时间戳> <等级>: <消息>`。
- 两个工具读取 ARXML 清单（`arxmlFileList`）的规则相同，见各工具的配置说明；`strictList` / `--strict-list` 使无效条目直接失败。
- 退出码：`0` 成功；`1` 工具自身运行或校验失败，不发布本次产物；`2` 命令行或配置文件错误，打印用法，不创建日志。System 工具的 armodel 解析器诊断即使以 `ERROR` 级别打印，也不单独导致退出码 `1`；详见 [System 日志与退出码说明](system/arxml-and-errors.md#日志与退出码)。
- EB 模式（由 tresos 导入器生成 ARXML 清单）需要调用方环境提供 `PrefSystemImporter`，wheel 不包含 EB 工具。

## 配置文件格式

两个工具的配置文件使用 [TOML 1.0](https://toml.io/cn/v1.0.0)，扩展名为 `.toml`，可以用 `#` 写注释。

- 文件使用 UTF-8 编码，允许带 BOM。
- 键名区分大小写；未知键会报错，不会被忽略；重复的键属于语法错误。
- 不需要的可选项直接省略；表示“空”时按各项说明写空字符串或空数组。
- 分组写成表，例如 `[general]`；多条同类配置写成表数组，例如 `[[interfaceMappings]]`。表必须写在所有顶层键之后。
- Windows 路径建议写成 `D:/Work/Project`。使用反斜杠时，要么写单引号字面字符串 `'D:\Work\Project'`，要么在双引号中写成 `"D:\\Work\\Project"`；双引号中的单个 `\` 是转义符。

```toml
# 行注释
outputPrefix = "Bcm"               # 行尾注释
strictList = false
greenTables = ["GreenTable/Bcm/*.xlsx"]

[general]
Composition = "/ARDesign/SystemDesign/TopLevelComposition"
```

旧版本使用的 JSON 配置文件仍兼容，可继续使用。
