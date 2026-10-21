# table-to-system-arxml 使用说明

返回 [../README.md](../README.md)。`table-to-system-arxml` 在同一进程内完成：收集 ARXML 文件、加载一次模型、
根据绿表生成白表（或直接输入白表）、复用模型生成 Composition/System ARXML。

## 说明索引

| 文档 | 内容 |
| --- | --- |
| [configuration.md](configuration.md) | TOML 配置参数、路径基准、优先级、General 与 AUTOSAR 引用 |
| [green-table-rules.md](green-table-rules.md) | 绿表命名、列定义、连接方向、CAN/LIN、去重、转换示例 |
| [white-table-format.md](white-table-format.md) | 白表页签、列定义、取值规则及生成语义 |
| [arxml-and-errors.md](arxml-and-errors.md) | EB 清单生成、lst 读取、排除规则、日志、退出码及排错 |

本文档描述当前实现。工具约定（例如 `its` 前缀、信号路径拼接方式）不是 AUTOSAR 标准规定的命名方式。

## 环境与输入

- 安装方式见 [../README.md](../README.md)。安装 wheel 会自动安装 Python 依赖：`armodel>=1.8.7,<=1.9.8,!=1.9.1`、`openpyxl>=3.1.2`、`packaging>=21.3`，Python 3.11 以下另装 `tomli>=1.1.0`。armodel 上限固定在已验证的 `1.9.8`，新版本通过兼容测试后才更新上限；1.9.1 的包入口不完整，不能使用。
- 使用 EB 模式时，`PrefSystemImporter` 必须能从当前进程的 `PATH` 找到；使用已有 lst 时不需要启动该命令。
- 输入应包含绿表引用的组件类型、端口、接口、实现、信号，以及 General 引用的系统和拓扑定义。lst 只是路径清单，不能代替缺失的定义。
- 工具不负责生成 BSW/SWC 的前置 ARXML；先用工程已有流程准备这些输入。

### 依赖清单与检查

| 依赖 | 用途 | 使用已有 lst | 使用 EB 生成 lst |
| --- | --- | --- | --- |
| Python 3.9 或更新版本 | 执行工具及标准库 | 必需 | 必需 |
| `openpyxl` | 读绿表、写白表及合并工作簿 | 必需 | 必需 |
| `armodel` | 解析和写出 ARXML、访问 AUTOSAR 模型 | 必需 | 必需 |
| `packaging` | 按版本规范检查 armodel 版本范围 | 必需 | 必需 |
| `tomli` | Python 3.11 以下读取 TOML 配置（3.11 起使用标准库） | Python < 3.11 必需 | Python < 3.11 必需 |
| `PrefSystemImporter` | 从 tresos preference XDM 生成 lst | 不检查、不调用 | 必须在 `PATH` 中 |
| XDM 及 EB 运行环境 | EB 导入器的输入和运行条件 | 全部忽略 | 必需 |

执行时先初始化日志，再实际导入依赖并检查 armodel 版本满足 `>=1.8.7,<=1.9.8,!=1.9.1`，不是只检查包名是否安装。缺失、导入损坏或版本不在范围内会在控制台及日志中记录 `ERROR`，包含具体原因和当前解释器，并返回 `1`。该检查不自动安装软件。

`--help` 不加载第三方转换依赖，不要求 EB。EB 模式若找不到或无法启动 `PrefSystemImporter`，错误也会写入日志；其内部的许可证、运行时等问题以实际启动结果为准。配置可读取且日志目录可写，文件日志才能建立。

排查当前解释器可使用 `python -m pip show openpyxl armodel` 和 `python -m pip check`。不要仅依据其他终端的 `pip` 结果判断本次解释器已经安装依赖。

## 运行方式

必须用 `--config` 指定至少一个配置文件，不读取任何默认配置；产物和日志目录也必须显式指定：在配置中提供 `outputDir`、`logDir`，或传入 `--output-dir`、`--log-dir`。缺少任一项就打印用法并以退出码 `2` 结束，不创建默认目录。

退出码：`0` 成功并已发布；`1` 运行失败（输入表、ARXML、转换或写出错误），不发布本次产物；`2` 命令行或配置错误（含未指定 `--config`、配置文件缺失、语法错误、未知键、必填项缺失），打印用法，不创建日志。

```powershell
# 指定配置文件。
table-to-system-arxml --config ./config/system.toml

# 指定配置和变体。
table-to-system-arxml --config ./config/system.toml --variant variantA

# 多个配置一次执行，只加载一次 ARXML；按给定顺序生成。
table-to-system-arxml --config ./config/a.toml --config ./config/b.toml --output-dir ./output/ab --log-dir ./log/ab

# 复用已有 lst，跳过 EB 清单生成。
table-to-system-arxml --config ./config/system.toml --arxml-file-list ./cfg/variantA.lst

# 直接由白表生成 ARXML。
table-to-system-arxml --config ./config/system.toml --white-table ./tables/BcmWhiteTable.xlsx

# 查看参数或输出更详细的诊断。
table-to-system-arxml --help
table-to-system-arxml --config ./config/system.toml --log-level DEBUG
```

命令不在 `PATH` 时，可用同一虚拟环境的 `python -m table_to_arxml.system`，参数相同。
配置文件中的相对路径始终基于配置文件所在目录；命令行传入的相对路径基于启动时的工作目录。

白表模式无需在配置中填写 `[general]`，Composition、System、根组合和 ECU 引用直接读取白表 General 页。

config 写了 `arxmlFileList` 就只读该清单，`tresosPreferenceFiles` 可以省略，保留时也不会检查；清单不存在直接失败，不回退 EB。只有删除 `arxmlFileList` 且未传 `--arxml-file-list` 时才使用 `tresosPreferenceFiles`。写成空字符串不是切换到 XDM 的方式。

可在命令行覆盖的配置项采用统一命名（如 `outputDir` 对应 `--output-dir`）；并非所有配置项都有 CLI 参数，接口映射等只能写在配置文件中。参数名须完整书写，不接受缩写。

| 参数 | 作用 |
| --- | --- |
| `--config` | **必填**，指定 TOML 配置文件，相对路径基于启动时的工作目录；可重复指定多个，见下文“多配置” |
| `--variant` | 覆盖 `defaultVariant`，用于展开 `${variant}` |
| `--project-root` | 覆盖 `projectRoot`，影响 lst 内相对路径解析和默认构建目录，不改变配置文件中的路径基准；与 `projectRoot` 至少提供一个 |
| `--arxml-file-list` | 覆盖 `arxmlFileList`，使用已有清单 |
| `--strict-list` | 覆盖 `strictList`：清单中有缺失、非 `.arxml` 或无匹配的条目时直接失败，而不是警告后跳过 |
| `--exclude-arxml` | 排除 ARXML 文件名或相对 `projectRoot` 的路径，可重复；提供时覆盖 `excludeArxml` |
| `--output-dir` | 指定产物目录，覆盖 `outputDir`；参数或配置必须提供，无默认值，不改变日志位置 |
| `--log-dir` | 指定日志目录，覆盖 `logDir`；参数或配置必须提供，无默认值；不能与产物目录相同 |
| `--log-level` | 覆盖 `logLevel`，可选 `DEBUG`、`INFO`、`WARNING`、`ERROR`，不区分大小写 |
| `--white-table` | 强制白表模式并使用该白表，忽略 config 的 `greenTables`/`whiteTable`；未传时按 config 中写了哪个字段自动选择模式；只能配合单个 `--config` |

多配置：重复 `--config` 时各配置共用一次 ARXML 加载，避免重复解析工程 ARXML。

- 加载集合是各配置清单的并集，并排除所有配置本次要生成的 `<outputPrefix>System.arxml`。
- 按 `--config` 顺序依次生成；前一个配置新生成的 ARXML 会追加到模型中，因此被引用的配置要排在前面（例如 B 配置引用 A 配置新增的实例时，A 须在前）。
- 各配置的 `outputPrefix` 不能重复或仅大小写不同，开始生成前会检查最终输出路径；未传 `--log-level` 时，各配置写了的 `logLevel` 必须一致，否则报错。
- 未传 `--output-dir` 或 `--log-dir` 时，每份配置必须提供对应的 `outputDir` 或 `logDir`，且各自解析后的目录必须一致。
- 任一配置失败则全部不发布。

输入表格在加载 ARXML 前校验：绿表须有 R-Port/P-Port 页（仅兼容旧 INPUT/OUTPUT 工作簿；RECEIVE/SEND 不兼容），表头 B、C 非空，I 含 `Component`、J 含 `port`；白表须有 General/Components 页且各页表头与工具布局一致、General 四项齐全。R-Port/P-Port 分别对应当前模块的接收端口和发送端口。全部输入表检查完后再报错退出，一次列出所有问题；后续的绿表转换和白表引用解析同样逐行记录错误，处理完再失败。armodel 加载 ARXML 时的报错按 ERROR 打印，但不影响退出码。

接口策略仅在 config 中配置：`"interfaceMismatch": "error"`，也可选 `skip`、`map`；没有配置该字段时默认 `error`，不提供命令行覆盖。完整映射配置见 [configuration.md](configuration.md)。

## 产物与发布

以 `outputPrefix=Bcm`、变体 `variantA` 为例：

| 产物名 | 用途 |
| --- | --- |
| `BcmWhiteTable.xlsx` | 白表：General、组件、装配连接和信号映射，也是生成 ARXML 的中间输入；白表输入模式不生成 |
| `BcmGreenTables.xlsx` | 合并本次所有绿表的 R-Port/P-Port 页，便于检索；白表输入模式不生成 |
| `BcmSystem.arxml` | 本次生成的 Composition、System 及映射，不是全工程模型的完整导出 |
| `variantA.lst` | 仅 EB 模式在产物目录生成；复用已有 lst 时不会复制或重新生成此文件 |
| `table_to_system_arxml_<时间含微秒>_<进程号>.log` | 在显式指定的 `--log-dir` 或 `logDir` 下记录本次运行日志 |

只有整次转换成功才覆盖同名产物，不会自动清理其他旧文件。工具自身的 ERROR 或运行异常阻止本次发布；armodel 解析器诊断不单独影响退出码，但仍需检查是否影响工程所需内容。失败时清理临时文件，发布失败尝试回滚，日志保留。不同变体使用相同 `outputPrefix` 时仍需分次运行并用 `--output-dir` 分目录；不要并发发布到同一个产物目录。

工具本身不把产物复制回工程；需要分发时由调用方的构建流程复制，并仅在退出码为 0 时复制。

转换错误由工具自身阻断，不需要另写脚本扫描 ERROR。退出码 0 表示转换和发布成功，但不替代工程集成及完整 AUTOSAR XSD/语义验证。具体边界见 [arxml-and-errors.md](arxml-and-errors.md)。

普通连接两端接口不同时，先复用已有的唯一有效映射，不受 `interfaceMismatch` 策略影响；没有可复用映射时，默认报错，`skip` 警告并跳过连接，`map` 允许新建映射。接口映射目前只支持 SenderReceiverInterface，并校验数据元素存在且配对完整、一一对应，不检查类型或结构兼容性。信号映射只检查目标 ECU、接口、数据元素和信号引用存在，以及表格方向对应的 R/P 方向，不追踪总线实际方向；组成员单独映射只警告，组映射不检查数据类型。生成成功不代表两端数据语义或 RTE 运行时兼容。
