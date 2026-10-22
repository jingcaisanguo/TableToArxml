# ARXML 清单与排错

返回 [README.md](README.md)。

## 两种输入模式

### EB 生成模式

未指定 `--arxml-file-list` 且 config 中没有 `arxmlFileList` 字段时，读取必填的 `tresosPreferenceFiles`，使用与生成 Autosar Builder 工程相同的 EB 工具调用方式：

```text
PrefSystemImporter --file-list --base-path <tresos工程目录>
    <preference1.xdm> <preference2.xdm> ... <输出目录>/<variant>.lst
    --env TRESOS_OUTPUT_BASE_DIR=<构建输出基目录>
```

上述为换行展示的单条命令。XDM 及其中的 EB 路径表达式由 `PrefSystemImporter` 处理，本工具不再自行解析 XDM 的 `FileName`。

- XDM 路径从配置解析为具体文件，默认假设其布局为 `<tresos工程>/.prefs/<文件>.xdm`。
- `--base-path` 取**第一个** XDM 所在目录的上一级。配置中的 XDM 应属于同一个 tresos 工程；当前不验证多个文件是否来自同一工程。
- 默认 `TRESOS_OUTPUT_BASE_DIR` 是 `projectRoot/build/<variant>.<buildType>`，不是带 `/tresos` 的下一级目录。这是沿用当前构建脚本的适配约定，不是 EB/AUTOSAR 的通用目录标准。
- 可配置 `tresosOutputBaseDir` 适配其他布局，但还需注意下面的路径补齐逻辑。
- 工具只调用 `PrefSystemImporter --file-list`，不执行 `--ab-project`，不创建 Autosar Builder 工程，也不生成缺失的 SWCD。
- EB 非零退出会中止本次流程；找不到命令也会报错，不自动安装或搜索其他安装目录。

### 复用清单模式

指定 `--arxml-file-list` 或在 config 中写了 `arxmlFileList` 后不启动 EB。清单须已存在，读取过程不覆盖、不复制该清单，也不保证在本次 `output/` 下额外出现 lst。config 中的 `arxmlFileList` 必须是非空路径字符串；空值、类型错误或文件不存在直接失败，不回退 XDM。

此模式完全忽略 XDM、EB 命令及构建目录相关配置，`tresosPreferenceFiles` 可有可无，即使保留了无效值也不会检查。lst 相对路径仅从 `projectRoot` 解析。要使用 XDM，必须删除 `arxmlFileList` 字段而不是留空；详见 [configuration.md](configuration.md)。Python 转换库仍是必需依赖。

## lst 格式及路径规则

```text
# 每行一个文件路径，也可以是 glob。
app/common/swc/Lamp.arxml
build/variantA.Debug/tresos/**/swcd/*.arxml
```

1. 使用 UTF-8 文本，去除每行首尾空白及两端引号，跳过空行及以 `#` 开头的行。路径有空格无需加引号；不支持行尾注释、嵌套 `@其他清单` 或在行中展开 `${variant}`。清单规则与 `table-to-swc-arxml` 相同。
2. 规范化目录分隔符；仅 EB 模式下，条目包含构建基目录的末级名称时，若下一层不是 `tresos`，就在其后补一层 `tresos`。例如 `build/variantA.Debug/bswm/swcd/a.arxml` 会补成 `build/variantA.Debug/tresos/bswm/swcd/a.arxml`。复用清单不做此补齐，须直接提供真实路径。
3. 绝对路径直接使用。EB 模式的相对路径分别尝试 `projectRoot` 和从 XDM 推导出的 tresos 工程目录，**如果两处都存在，会都收集，不是只取第一处。** 复用清单的相对路径只基于 `projectRoot`。
4. 支持 `*`、`?`、字符集合和递归 `**` 等 glob。只保留存在且扩展名为 `.arxml`（不区分大小写）的文件；单写目录不会递归扫描，需写 glob。
5. 具体文件不存在、扩展名不是 `.arxml` 或 glob 无匹配时记录 `WARNING` 并跳过；`strictList` / `--strict-list` 为真时直接失败。被排除规则命中的不存在条目只记录 `INFO`。过滤后没有任何 ARXML 会报错。
6. 解析到实际绝对路径后去重。同一物理路径只加载一次，但内容相同的不同文件不会按内容去重。

原始 EB lst 仅在整次成功后发布到输出目录；展开 glob、补齐目录、去重和排除只用于内存中的加载集合，**不会把规范化后的集合写回 lst**。因此 lst 行数与实际加载文件数可能不同。lst 兼容 UTF-8 BOM 和两种目录分隔符，POSIX 不接受 Windows 盘符/UNC 绝对路径。

## 排除规则

排除发生在加载 ARXML 模型前，不在 EB 原始 lst 生成阶段。

| 规则 | 行为 |
| --- | --- |
| 即将生成的同名 ARXML | 自动排除任意目录下 `<outputPrefix>System.arxml`，文件名匹配不区分大小写，避免旧产物重新进入输入模型 |
| 标定目录 | 内置正则排除 `dem/swcd_calibration/`、`dcm/swcd_calibration/` 下文件；这是本工具继承的过滤策略，不应理解为 EB 永远不会列出这些文件 |
| `excludeArxmlPatterns` | 追加用户正则，不能取消内置排除 |

正则以 `re.IGNORECASE` 编译，对规范化后的绝对路径调用 `match`，不是 glob。需要匹配中间路径时通常以 `.*` 开头，例如以下配置可额外排除文件名为 `EcuExtract.arxml` 的输入。正则建议写成单引号字面字符串，反斜杠无需再次转义：

```toml
excludeArxmlPatterns = [
    '.*[\\/]EcuExtract\.arxml$',
]
```

默认不会按旧排除清单自动排除 `EcuExtract.arxml`。是否排除应根据输入模型设计决定，不能仅因文件名称就认定它不需要。

## 模型与生成边界

本次涉及的 ARXML 只加载一次。模型用于解析 General、组件、prototype 和通道；随后读取白表，在同一个模型上解析组件、端口、信号等引用。所有引用解析完成后才创建独立的输出文档并写出 ARXML，不重新加载输入文件。因此不会将输入的全部包和元素原样导出，也不会从绿表生成缺失的 SWC 类型。

生成时支持 Application、ComplexDeviceDriver、EcuAbstraction、Service、SensorActuator、NvBlock、ServiceProxy、Parameter 及 Composition 类型（Parameter 类型需要 armodel 提供对应类）；PR-Port 及其他类型报错并说明是工具不支持。引用组件不新增实例，必须在目标 Composition 中存在且类型一致；`Add` 的实例已存在时不重复写出，只生成 System 映射。新增 Atomic 组件缺少内部行为或 SWC-IMPLEMENTATION 时记录 WARNING 并跳过 SwcToImplMapping（AUTOSAR 中该映射为 0..*）；多个实现引用同一内部行为时也只警告。嵌套 Composition 不生成实现映射，作为 `Add` 写入 SwcToEcuMapping 时记录 WARNING。不提供 Rhapsody XML 等其他格式导出。

### AUTOSAR 输出规则

输出是对已有元素的拆分片段（split），按 AUTOSAR 4.x 元模型的多重性与 `atpSplitable` 规则写出，做浅校验：

| 规则 | 行为 |
| --- | --- |
| 目标 Composition 中 COMPONENTS、PORTS、CONNECTORS 共用命名空间，SHORT-NAME 不区分大小写唯一 | 新对象与已有或本次其他对象重名（含仅大小写不同）时报错；已有同名同端点的连接器直接复用不再写出，连接同一对端口但名称不同的已有连接器记录 WARNING 后复用 |
| `System` 必须是已加载的 System（短名或完整路径） | 不存在或同名歧义时报错；根原型必须是该 System 的子元素，ECU-INSTANCE 必须存在且类型正确（与是否有信号映射无关） |
| `System.fibexElement` 不可拆分 | 已有 System 没有 FIBEX-ELEMENTS 时写入 ECU；已引用该 ECU 时不重复写；引用了其他元素但不含该 ECU 时记录 WARNING，需手工补齐 |
| `SYSTEM-VERSION` 为 1..1 | 沿用输入值；输入缺失时记录 WARNING，不臆造版本 |
| `SystemMapping` 的子聚合不可拆分 | 同名 SystemMapping 已存在时报错，需改用其他 `general.systemMapping` |
| `SwcToEcuMapping.component` 为 1..* | 没有 `Add` 组件时不生成空映射；既无新增组件也无信号映射时不生成 SystemMapping |
| `PortInterfaceMappingSet.portInterfaceMapping` 不可拆分 | 需要新建映射而 `<Composition>_InterfaceMappings` 已存在于输入时报错 |
| `SenderReceiverToSignalGroupMapping.typeMapping` 为 1..1 | 工具不推导记录类型映射，每行记录 WARNING |
| 接口引用的 DEST | 按被引用对象的实际类型写出；输入端口声明的 DEST 不一致时记录 WARNING |
| 重复的信号映射行 | 只生成一条并记录 WARNING |
| 输出 release | 由 `arRelease` 决定；与输入 ARXML 声明的 release 不同时记录 WARNING |

底层 armodel 模型是进程内全局单例，不支持在同一进程中并行运行多个转换。不同配置/变体应串行运行或使用独立进程，并避免写入相同产物路径。

## 日志与退出码

- 同时输出到控制台和日志文件：普通信息到 stdout，`WARNING`、`ERROR` 到 stderr，两者格式相同，每行为 `<时间戳> <等级>: <消息>`；日志等级取 `--log-level`、`logLevel`、默认 `INFO` 中优先级最高者，多配置时各配置的 `logLevel` 须一致。日志目录必须通过 `--log-dir` 或配置 `logDir` 指定，命令行优先，无默认目录，与产物目录分开。缺少日志或产物目录时仅在控制台打印用法和错误，返回 `2`，不创建文件日志。
- `INFO` 可查看选中的绿表数量、加载数量、各表追加数量和产物位置。
- armodel 解析器的 WARNING/ERROR 按原级别输出，不支持的元素跳过，不影响退出码。其他代码记录的 ERROR 先收集，发布产物前统一失败并列出全部消息。
- 日志文件名包含微秒和进程号，降低冲突概率；不支持并发发布到同一产物目录。

| 情况 | 退出行为 |
| --- | --- |
| 全部转换、写出和发布成功，无 ERROR | 返回 `0` |
| armodel 解析器以外的任意 ERROR，或配置、文件、模型、转换、写入异常 | 记录失败原因并返回 `1`，不发布本次 Excel/ARXML/lst；旧产物不变。非预期的程序异常另记录完整堆栈 |
| 命令行或配置错误（参数格式、未指定 `--config`、配置文件缺失或无法解析、未知键、必填项缺失、日志/产物目录缺失等） | 打印用法和错误，返回 `2`，不创建日志 |
| Python 转换依赖缺失或导入不可用 | 日志初始化后检查，在控制台及日志文件记录库名、实际异常和解释器，返回 `1` |

## 常见情况

| 日志/现象 | 含义与检查方向 |
| --- | --- |
| `Python dependencies are missing or unusable` | 使用日志中指定的解释器检查或修复库和传递依赖；完整列表见 [README.md](README.md) |
| `PrefSystemImporter was not found in PATH` | 进入有 EB 工具的构建环境，或用已有 lst；不能以普通终端的 PATH 推断构建环境 |
| `tresos preference file does not exist` | 仅 EB 模式会检查；确认路径相对配置目录。已指定 lst 时不应出现此项检查 |
| `No usable ARXML files found` | 检查 lst 内容、`projectRoot`、构建基目录补齐规则和排除正则 |
| `No green tables were selected` | 检查 glob、变体及相对配置目录的文件位置 |
| `Cannot find the SW component type of green table(s)` | 绿表文件名未能匹配组件查询表；补齐输入类型/引用或修正文件名，任务退出 |
| 纯组件模型没有 CAN/LIN | 允许；不检查物理通道 |
| `System ... was not found` / `System is not defined` 或缺少 `RootSwCompositionPrototype` | System 必须已存在于输入；检查所选 System 的短名和定义，必要时显式填写根原型路径 |
| `does not belong to System` | 根原型必须是所选 System 的 ROOT-SOFTWARE-COMPOSITION |
| `unresolved component` / `Ambiguous component` | 外部组件未知或短名不唯一；补齐输入或配置 `componentAliases` |
| `channel ... is not a CAN/LIN physical channel`（WARNING） | I 列不是相应总线的物理通道名；若随后报信号不存在，检查通道名或 `signalPathTemplate` |
| `interface mismatch` | 装配连接两端接口不同且没有可复用映射；默认失败，可显式选择 `interfaceMismatch=skip` 或允许新建映射的 `map` |
| `Cannot infer interface mapping` | 无法按名称配对任何元素；在 config 中显式配置 `interfaceMappings` |
| `leaves data elements unmapped`（WARNING） | 接口映射未覆盖部分数据元素；确认这些元素确实不需要连接 |
| `ECU instance is not defined` | General 引用的 ECU 不存在或不是 ECU-INSTANCE；不要求提供完整通信拓扑或实际方向 |
| `must be R-PORT-PROTOTYPE` / `must be P-PORT-PROTOTYPE` | 普通装配要求 P 到 R；总线映射中 R-Port 页（IN）须为 R-port，P-Port 页（OUT）须为 P-port |
| `cannot be mapped as an individual signal` | 信号组对象不能当作普通信号；组成员单独映射只记录 WARNING，不阻断生成 |
| `column I or J is empty` / `column B is empty` | 必要字段不完整，按提示位置修正绿表 |
| `<sheet> row <n>: ...` | 无效端口、信号或 DataElement 等引用导致整次失败 |
| `SwcToImplMapping is not generated`（WARNING） | 新增组件缺少内部行为或 SWC-IMPLEMENTATION；集成前补齐 |
| `Reference prototype does not exist` / `outside target composition` | Reference 必须真实存在于目标 Composition，不能把其他组合的实例直接移到目标下 |
| `SHORT-NAMEs must be unique (case-insensitive)` | 新增的实例、外部端口或连接器与目标 Composition 中的其他对象重名（含仅大小写不同） |
| `already exists in the loaded ARXML ... cannot be split` | SystemMapping 或接口映射集已存在，不能在新文件中追加其子元素；改名或在原文件中维护 |
| `Unsupported TransformationDescription <SOMEIP-TRANSFORMATION-DESCRIPTION>` | armodel 不支持该输入元素，按 ERROR 打印后跳过，不影响退出码；生成结果依赖该元素时需改用兼容的解析库或输入 |
| `conflicting connector name` / `conflicting outer port` / `ambiguous component alias` | 白表数据存在不同对象共用名称的冲突；任务在清空模型及写出 ARXML 前中止 |
| Excel `PermissionError` | 检查同名产物是否被 Excel 等程序占用 |
| 白表有连接，最终 ARXML 没有 | 检查日志及端口、接口、信号、DataElement 引用；白表不是最终有效性证明 |

## 交付前检查

1. 确认本次选中变体、绿表数量、实际 ARXML 加载数符合预期，缺失文件未被误当成可忽略项。
2. 工具自身的 ERROR 会阻断写出，不需要另写脚本扫描这类错误。armodel 解析器的 ERROR 诊断是例外，不单独影响退出码，仍须核查其影响；同时关注 WARNING 和预期模块/连接数量。
3. 在白表核对 General、Components 的 `Add/Reference`、装配方向和信号方向，再核对最终 ARXML。
4. 仅在退出码为 0 时发布产物。失败保留之前成功产物，不应把目录中旧文件误认为本次成功输出。转换使用临时目录，发布阶段普通 I/O 异常回滚；不承诺断电、强制终止或回滚存储故障下的跨文件事务原子性。
5. 通过工程自己的导入、引用解析和验证流程确认集成结果，再由外部复制命令分发。工具不提供完整的 AUTOSAR XSD/语义校验替代品。