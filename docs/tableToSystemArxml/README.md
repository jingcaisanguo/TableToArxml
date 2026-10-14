# 表格转 System ARXML 工具（table_to_system_arxml）

本工具在同一进程内完成：收集 ARXML 文件、加载一次模型、根据绿表生成白表（或直接输入白表）、复用模型生成 Composition/System ARXML。

## 说明索引

| 文档 | 内容 |
| --- | --- |
| [configuration.md](configuration.md) | JSON 参数、路径基准、优先级、General 与 AUTOSAR 引用 |
| [green-table-rules.md](green-table-rules.md) | 绿表命名、列定义、连接方向、CAN/LIN、去重、转换示例 |
| [white-table-format.md](white-table-format.md) | 白表页签、列定义、取值规则及生成语义 |
| [arxml-and-errors.md](arxml-and-errors.md) | EB 清单生成、lst 读取、排除规则、日志、退出码及排错 |
| [packaging.md](packaging.md) | wheel 构建、GitHub Release 分发、安装命令与路径约定 |

本文档描述当前实现。工具约定（例如 `its` 前缀、信号路径拼接方式）不是 AUTOSAR 标准规定的命名方式。

## 目录与职责

```text
table_to_system_arxml/
    table_to_system_arxml.py     薄启动入口，可直接运行
    table_to_system_arxml-<版本>-py3-none-any.whl  本地构建产物，不提交
    pyproject.toml              wheel 构建配置
    requirements.txt            Python 依赖
    table_to_system_arxml/       可安装的 Python 包
        __init__.py             包初始化
        __main__.py             python -m 模块入口
        cli.py                  参数解析和转换编排
        lib/                    内部转换实现
```

[../../table_to_system_arxml/table_to_system_arxml.py](../../table_to_system_arxml/table_to_system_arxml.py) 只负责启动；[../../table_to_system_arxml/table_to_system_arxml/cli.py](../../table_to_system_arxml/table_to_system_arxml/cli.py) 编排执行流程；[../../table_to_system_arxml/table_to_system_arxml/lib/file_list.py](../../table_to_system_arxml/table_to_system_arxml/lib/file_list.py) 处理 EB 清单；[../../table_to_system_arxml/table_to_system_arxml/lib/arxml_model.py](../../table_to_system_arxml/table_to_system_arxml/lib/arxml_model.py) 封装模型访问；[../../table_to_system_arxml/table_to_system_arxml/lib/green_to_white.py](../../table_to_system_arxml/table_to_system_arxml/lib/green_to_white.py) 绿表转白表；[../../table_to_system_arxml/table_to_system_arxml/lib/white_table_reader.py](../../table_to_system_arxml/table_to_system_arxml/lib/white_table_reader.py) 读取白表；[../../table_to_system_arxml/table_to_system_arxml/lib/arxml_generator.py](../../table_to_system_arxml/table_to_system_arxml/lib/arxml_generator.py) 解析引用并用 armodel 生成输出。

工程配置、输入、产物和日志位于包目录之外，由调用工程提供；本说明位于仓库的 `docs/tableToSystemArxml/`。

## 环境与输入

- 使用 Python 3.9 或更新版本。安装 wheel 会自动安装依赖；源码运行按 [../../table_to_system_arxml/requirements.txt](../../table_to_system_arxml/requirements.txt) 安装依赖：`python -m pip install -r requirements.txt`。发行依赖为 `armodel>=1.8.7,<1.9.4`、`openpyxl>=3.1.2`、`packaging>=21.3`；实测 1.8.7 和 1.9.2 可用，1.9.4 及以后缺少 `ISignalPort` 导出，不能使用。
- 转换器在包内的 `lib/` 中实现，不再依赖或导入 `pya4a`，也不启动 `xlsx-composition-sw`。白表格式和有效输入的输出约定保持兼容；升级 armodel 后应重新验证生成结果。
- 使用 EB 模式时，`PrefSystemImporter` 必须能从当前进程的 `PATH` 找到；使用已有 lst 时不需要启动该命令。
- 输入应包含绿表引用的组件类型、端口、接口、实现、信号，以及 General 引用的系统和拓扑定义。lst 只是路径清单，不能代替缺失的定义。
- 工具不负责生成 BSW/SWC 的前置 ARXML；先用工程已有流程准备这些输入。

### 依赖清单与检查

| 依赖 | 用途 | 使用已有 lst | 使用 EB 生成 lst |
| --- | --- | --- | --- |
| Python 运行环境 | 执行脚本及标准库 | 必需 | 必需 |
| `openpyxl` | 读绿表、写白表及合并工作簿 | 必需 | 必需 |
| `armodel` | 解析和写出 ARXML、访问 AUTOSAR 模型 | 必需 | 必需 |
| `packaging` | 按 Python 版本规范检查 armodel 最低版本 | 必需 | 必需 |
| `PrefSystemImporter` | 从 tresos preference XDM 生成 lst | 不检查、不调用 | 必须在 `PATH` 中 |
| XDM 及 EB 运行环境 | EB 导入器的输入和运行条件 | 全部忽略 | 必需 |

已核实的环境为 `openpyxl 3.1.2`，以及 `armodel 1.8.7`、`1.9.2`（两者生成的 ARXML 完全一致）。其发行包元数据声明的下一层依赖包括：`openpyxl` 的 `et-xmlfile`；`armodel` 的 `colorama`、`openpyxl`、`lxml`。本工具不再要求安装 `pya4a` 或为它引入 `toml`、`py-eb-model`、`py-ea-model`。其他工程工具可能仍需要这些库，因此本次改造不会卸载已有包。

执行时先初始化日志，再实际导入依赖并检查 armodel 版本满足 `>=1.8.7,<1.9.4`；不是只检查包名是否安装。缺失、导入损坏或版本不在范围内会在控制台及日志中记录 `ERROR`，包含具体原因和当前解释器，并返回 `1`。该检查不代替完整的依赖版本兼容性测试，也不自动安装软件。

`--help` 不加载第三方转换依赖，不要求 EB。EB 模式若找不到或无法启动 `PrefSystemImporter`，错误也经主入口写入日志；其内部的许可证、运行时等问题以实际启动结果为准。入口和 Python 自身必须能启动，配置可读取且日志目录可写，文件日志才能建立。

排查当前解释器可使用 `python -m pip show openpyxl armodel` 和 `python -m pip check`。不要仅依据其他终端的 `pip` 结果判断本次解释器已经安装依赖。`xlsx-composition-sw`、Excel 程序本身不是本工具的外部运行命令依赖；CMB 的 `cp` 是工程发布步骤，不属于工具内部依赖。

## 运行方式

以下命令从**工具根目录**执行，`python` 需指向已安装上述依赖的解释器。

产物和日志目录均必须显式指定。以下源码示例须在配置中提供 `"outputDir": "output"`、`"logDir": "log"`，或同时传入 `--output-dir ./output --log-dir ./log`；缺少任一目录就报错退出，不创建默认目录。

```powershell
# 默认读取工具项目目录的 config.json，需要自行提供。
python ./table_to_system_arxml.py

# 指定配置和变体。
python ./table_to_system_arxml.py --config ./config.json --variant zcud_m7

# 多个配置一次执行，只加载一次 ARXML；按给定顺序生成。
python ./table_to_system_arxml.py --config ../test/tableToSystemArxml/McTableToSystemArxmlConfig.json --config ../test/tableToSystemArxml/BcmTableToSystemArxmlConfig.json --output-dir ../test/tableToSystemArxml/output/BcmMcTest --log-dir ../test/tableToSystemArxml/log/BcmMcTest

# 复用已有 lst，跳过 EB 清单生成。
python ./table_to_system_arxml.py --arxml-file-list ../arxml_integration/cfg/zcud_m7.lst

# 直接由白表生成 ARXML。
python ./table_to_system_arxml.py --white-table ../test/tableToSystemArxml/WhiteTable/BcmWhiteTable.xlsx

# 查看参数或输出更详细的诊断。
python ./table_to_system_arxml.py --help
python ./table_to_system_arxml.py --log-level DEBUG

# 日志和产物分别指定目录。
python ./table_to_system_arxml.py --output-dir ./results/zcud_m7 --log-dir ./run-logs

```

也可从任意目录通过绝对路径启动入口。JSON 中的相对路径始终基于 JSON 所在目录；命令行传入的相对路径基于启动时的工作目录。

config 写了 `arxmlFileList` 就只读该清单，`tresosPreferenceFiles` 可以省略，保留时也不会检查；清单不存在直接失败，不回退 EB。只有删除 `arxmlFileList` 且未传 `--arxml-file-list` 时才使用 `tresosPreferenceFiles`。空字符串或 `null` 不是切换到 XDM 的方式。

命令行参数与 JSON 键一一对应（如 `outputDir` 对应 `--output-dir`），命名与 `table-to-swc-arxml` 统一，参数名须完整书写，不接受缩写。

| 参数 | 作用 |
| --- | --- |
| `--config` | 指定配置文件，源码脚本默认是工具根目录的配置文件；安装命令默认是当前目录的配置文件；可重复指定多个，见下文“多配置” |
| `--variant` | 覆盖 `defaultVariant`，用于展开 `${variant}` |
| `--project-root` | 覆盖 `projectRoot`，影响 lst 内相对路径解析和默认构建目录，不改变 JSON 路径基准 |
| `--arxml-file-list` | 覆盖 `arxmlFileList`，使用已有清单 |
| `--output-dir` | 指定产物目录，覆盖 `outputDir`；参数或配置必须提供，无默认值，不改变日志位置 |
| `--log-dir` | 指定日志目录，覆盖 `logDir`；参数或配置必须提供，无默认值；不能与产物目录相同 |
| `--log-level` | 覆盖 `logLevel`，可选 `DEBUG`、`INFO`、`WARNING`、`ERROR`，不区分大小写 |
| `--white-table` | 强制白表模式并使用该白表，忽略 config 的 `greenTables`/`whiteTable`；未传时按 config 中写了哪个字段自动选择模式；只能配合单个 `--config` |

多配置：重复 `--config` 时各配置共用一次 ARXML 加载，避免重复解析工程 ARXML。

- 加载集合是各配置清单的并集，并排除所有配置本次要生成的 `<outputPrefix>System.arxml`。
- 按 `--config` 顺序依次生成；前一个配置新生成的 ARXML 会追加到模型中，因此被引用的配置要排在前面（如 Cem 引用 Mc 的 `itsMirrorWrapper`，须 Mc 在前）。
- 各配置的 `outputPrefix` 不能重复；未传 `--log-level` 时，各配置写了的 `logLevel` 必须一致，否则报错。
- 未传 `--output-dir` 或 `--log-dir` 时，每份配置必须提供对应的 `outputDir` 或 `logDir`，且各自解析后的目录必须一致。
- 任一配置失败则全部不发布。

输入表格在加载 ARXML 前校验：绿表须有 INPUT/OUTPUT 页，表头 B、C 非空，I 含 `Component`、J 含 `port`；白表须有 General/Components 页且各页表头与工具布局一致、General 四项齐全。不符合即报错退出。armodel 加载 ARXML 时的报错按 ERROR 打印，但不影响退出码。

接口策略仅在 config 中配置：`"interfaceMismatch": "error"`，也可选 `skip`、`map`；没有配置该字段时默认 `error`，不提供命令行覆盖。完整映射配置见 [configuration.md](configuration.md)。

## 产物与发布

以 `outputPrefix=Bcm`、变体 `zcud_m7` 为例：

| 产物名 | 用途 |
| --- | --- |
| `BcmWhiteTable.xlsx` | 白表：General、组件、装配连接和信号映射，也是本地生成器的中间输入；白表输入模式不生成 |
| `BcmGreenTables.xlsx` | 合并本次所有绿表的 INPUT/OUTPUT 页，便于检索；白表输入模式不生成 |
| `BcmSystem.arxml` | 本次生成的 Composition、System 及映射，不是全工程模型的完整导出 |
| `zcud_m7.lst` | 仅 EB 模式在产物目录生成；复用已有 lst 时不会复制或重新生成此文件 |
| `table_to_system_arxml_<时间含微秒>_<进程号>.log` | 在显式指定的 `--log-dir` 或 `logDir` 下记录本次运行日志 |

只有整次转换成功才覆盖同名产物，不会自动清理其他旧文件。任何 ERROR 或异常都阻止本次发布，临时文件清理、原有产物保留不变；发布失败会尝试回滚，日志保留。不同变体使用相同 `outputPrefix` 时仍需用 `--output-dir` 分目录；不要并发发布到同一个产物目录。

工具本身不把产物复制回工程。[../../../cmb_config/cmb_swp_config.yaml](../../../cmb_config/cmb_swp_config.yaml) 的动作通过 profiles 配置 EB 工具环境，当前复制命令为注释状态。启用复制时，修改 `outputPrefix` 或 `--output-dir` 后须同步源路径，并仅在退出码为 0 时发布。

转换错误由工具自身阻断，不需要另写脚本扫描 ERROR。退出码 0 表示转换和发布成功，但不替代工程集成及完整 AUTOSAR XSD/语义验证。具体边界见 [arxml-and-errors.md](arxml-and-errors.md)。

普通连接默认要求两端接口一致；CAN/LIN 映射检查目标 ECU、选定通道的实际信号方向以及应用端 R/P 方向，组成员不允许直接映射。显式 `interfaceMismatch=skip` 只将接口不一致的连接作为 WARNING 跳过，不绕过其他错误；`map` 目前只支持数据元素一一对应且最终数据类型一致的 SenderReceiverInterface。最终位宽、编码/符号性和数组/结构形状必须匹配，uint8 不能连接到 uint16 或 sint8。