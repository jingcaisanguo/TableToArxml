# 版本升级与迁移

返回 [使用说明](README.md)。升级时安装新的 wheel，并使用同版本的文档和模板。
不要假定任意版本升级都无需修改配置；先在隔离目录运行并检查日志和生成差异。

## 本次审核修复

- openpyxl 最低版本提高到 **3.1.5**。3.1.2 读取数据验证时可能把 `allowBlank=true` 错误改为 false；
  为避免合并绿表改变输入规则，不继续兼容该旧版本。正常安装新版 wheel 会升级依赖，源码运行也会在启动时检查并拒绝低版本。
- System 发布前拒绝产物覆盖本次输入文件（含配置、表格、清单、ARXML、EB 配置及其文件别名），请使用独立输出目录。
- 绿表 glob 无匹配不再静默跳过，改为 `WARNING`；还有其他有效输入时仍继续转换。
- 合并绿表中对两个端口页的直接公式引用随最终页名更新；不计算公式，文本间接引用等边界见[绿表规则](system/green-table-rules.md)。
- SWC 插入和类型 DEST 推导支持 SHORT-NAME 的合法 XML 等价写法（字符引用、CDATA 和标签空白）。
  工作表按稀疏行读取，远处非空单元格不再扩展整页矩阵，错误仍报告原始 Excel 行号。

## System 输出模式

新增顶层配置 `outputMode` 和命令行 `--output-mode`。不填写时仍为 `replace`，现有脚本及样例行为不变。
显式选择 `merge` 时，从 lst 中匹配唯一的 `<outputPrefix>System.arxml`，
以完整 base 为输出起点，仅追加表格请求且尚不存在的 SWC 实例、连线、信号/信号组映射及必要依赖，仍写到 output，不修改 base。
与早期“重新生成后只保留 base 连线和信号映射”的 merge 实现不同，现在保留全部 base 内容，不删除或覆盖已有对象；
相同对象复用，类型、连线或映射冲突时报错。`generateSystem = false` 不再移除 base 中已有的 System 内容。
找不到唯一 base、引用缺失或合并冲突时阻止写出。TOML 和兼容的 JSON 配置使用相同规则。
完整边界见 [System 输出模式](system/README.md#输出模式)。

## 兼容性与稳健性修复

全盘审查后修复了一批与运行环境、Python/armodel 版本和输入格式相关的问题。正常输入的生成结果不变；以下为可见的行为变化。

路径与清单（两个工具）：

- 输出目录所在盘符或网络共享不存在、映射盘断开时立即报错退出，不再在发布阶段卡死。
- Python 3.9 + Windows 下，命令行中尚不存在的相对路径（如新的 `--output-dir`）现在按当前工作目录解析；以前会被静默解析到配置文件目录。
  不展开通配符的选项含 `*`、`?` 时给出明确报错，不再出现 `WinError 123`。
- 命令行接受 `\\?\` 长路径前缀；Linux 上 `//opt/x`、`a:b/x` 按普通路径处理，只拒绝 `D:\`、`D:/`、`\\` 开头的 Windows 路径。
- ARXML 清单：另一平台的绝对路径、无权限访问的条目按普通无效条目警告跳过（`strictList` 时失败），不再中止整次运行；
  非 UTF-8（如 GBK）清单按系统 ANSI 编码读取，带 BOM 的 UTF-16 清单也可读取，日志记录所用编码。
- `excludeArxml` 中以 `./` 开头的值按相对 `projectRoot` 的路径匹配，不再当作文件名排除所有同名文件。
- 清单条目经过符号链接或目录联接时，`--output-dir` 按清单中的路径布局写出，不再报 `outside projectRoot`。
- 写出时保留原文件的权限位以及 Windows 隐藏/系统属性；不保留 ACL 和属主。仅大小写不同的输出路径只在 Windows 上视为冲突。
- 变体名不能是 `NUL`、`CON`、`COM1` 等 Windows 设备名（会生成 `NUL.lst` 这类设备文件名）；`outputPrefix` 只有在生成的产物文件名本身是设备名时（如 `Aux.v1` 得到 `Aux.v1System.arxml`）才报错，`Aux`、`Con` 等前缀仍可使用。

退出码与报错：

- System 的 `--config` 值无效、两个工具在解析路径参数时出错，退出码均为 `2`（以前 System 为 `1`）。
- 加密/IRM 保护或 `.xls` 格式的表格、损坏的 ARXML 报出文件名并以退出码 `1` 结束，不再打印程序堆栈；绿表预检会汇总全部表格的问题。
- 产物发布后清理临时目录失败只记 `WARNING`，不再误报失败。
- 控制台无法编码的字符改为转义输出，不再出现 `--- Logging error ---`；日志文件仍为 UTF-8。Git Bash/MINGW64 中显示中文乱码时可设置 `PYTHONUTF8=1`。
- 源码运行时无法取得版本（无 `git`、无 setuptools-scm、GitHub 源码 zip）不再导致导入失败，版本显示为 `0+unknown`。

System 工具：

- 非 Excel 工具生成、`<dimension>` 声明不全的表格不再静默丢行。
- 绿表文件为符号链接时，模块名取链接文件名。
- `SenderReceiverToSignalGroup` 页只接受 SYSTEM-SIGNAL-GROUP，`SenderReceiverToSignal` 页只接受 SYSTEM-SIGNAL，类型不符时报错。
- 多个 SWC-IMPLEMENTATION 引用同一内部行为时，按路径排序取第一个（仍记录 `WARNING`），结果不再依赖清单顺序。
- 复用已有连接器但它没有引用所需接口映射时，记录 `WARNING` 且不再写出无人引用的接口映射；replace 与 merge 行为一致。
- 自我实例化的组合只警告一次并跳过，不再导致搜索耗时剧增。
- merge：
  - base 中缺少 `COMMUNICATION-DIRECTION` 的信号映射、带 `TYPE-MAPPING`/`VARIATION-POINT`/`ADMIN-DATA` 的映射或连线现在被识别为同一对象并复用，不再报冲突或重复追加。
  - 引用 PR 端口的 `PR-PORT-PROTOTYPE` 不再被误判为 DEST 错误；变体引用不再因 armodel 版本不同而失败；带 `BASE` 属性的相对引用可以解析。
  - base 的 System 没有 FIBEX-ELEMENTS 时，与 replace 一样写入目标 ECU。
  - 新建容器按 AUTOSAR schema 顺序插入；同名但带不同 VARIATION-POINT 的元素不再报路径重复。
  - 输出保留 base 的 XML 声明、文件头注释、编码、换行风格和未改动部分的空白；沿用 base 的 schema 声明，`arRelease` 不生效。
  - `generateSystem = false` 时不再要求 System 存在，也不检查 base 中的 System 映射。
  - 大量连线和映射的去重性能显著提升。

SWC 工具：

- 类型和接口改为按目标文件批量插入并只重新解析一次，大文件中新建大量对象的耗时大幅下降；生成结果不变。日志中 `Prepared …` 改在每个步骤结束时输出，只对实际改动的文件记录 `Re-parsing`。
- 工作表只读取到最后一个有值的行和列，远处带格式的空单元格（WPS、LibreOffice 常见）不再导致长时间加载和大量内存占用。
- 单元格含 U+2028、U+2029 或 C1 控制字符（`\x7f`–`\x9f`）时记录 `ERROR`；以前会被静默改写为换行，重跑时报定义不一致。
- `N ARXML file(s) affected` 只统计内容实际变化的文件。
- 两个不同端口/operation 的 CS 请求生成相同的 runnable、事件或调用点名称时记录 `ERROR` 并列出两者；以前会静默共用并给出误导性的 `already exists`。手写的同名 `Runnable Name` 仍可共用。
- 根元素带命名空间前缀（如 `<ar:AUTOSAR>`）的 ARXML 需要修改时记录 `ERROR`；只引用其中类型时可正常定位并使用正确的 DEST。
- 其他工具生成、缺少 `SYMBOL`、`MINIMUM-START-INTERVAL` 或 `CAN-BE-INVOKED-CONCURRENTLY` 的已有 runnable 按默认值比较，可以复用。
- `Runnable Symbol`（或由名称得到的 SYMBOL）为 C 关键字时记录 `WARNING`。
- 写出前若源 ARXML 已被其他程序修改，报错退出码 `1`，不写任何文件。
- `outputDir` 位于 `projectRoot` 内时，不再把上次产物当作清单输入。
- `Target ARXML` 位于符号链接或目录联接下时，`--output-dir` 保持其路径布局；与清单条目写法不同但实为同一文件时按同一文件处理。
- 配置 `workbook = "*.xlsx"` 时忽略 Excel 打开工作簿时生成的 `~$` 锁文件。

## 配置路径通配符修复

修复部分 Windows 驱动器把 `greenTable/${variant}/*.xlsx` 这类通配符路径误报为已存在，导致工具按字面路径处理并报 `WinError 123` 的问题。
现在只有目录中确实存在完全同名的条目时才按字面路径使用，其余一律按通配符展开。
两个工具的配置路径（如 System 的 `greenTables`、SWC 的 `workbook`）和 ARXML 清单条目均适用；配置无需修改。
此前为规避该问题而逐个列出文件的配置可恢复使用通配符。

绿表文件名现在必须符合 AUTOSAR SHORT-NAME 规则（字母开头，只含 ASCII 字母、数字和下划线，最长 128 字符），因为文件名即组件名；
`greenTables` 文件名部分的通配符总是展开，不再把含 `*`、`[` 等字符的文件名当作字面文件。
以前可被读取、但含 `-`、空格、中文或以数字开头的绿表文件名会报错，请改名，必要时用 `componentAliases` 映射到目标组件。

## 绿表页签命名调整

新建绿表请使用 `R-Port` 和 `P-Port` 页签，直接对应当前模块的接收端口和发送端口。
旧工作簿仍可使用完整的 `INPUT`/`OUTPUT` 组合，无需批量改名；中间方案 `RECEIVE`/`SEND` 不作为兼容别名。
同一工作簿必须完整使用 `R-Port`/`P-Port` 或旧 `INPUT`/`OUTPUT`，不能混用或同时保留两套。合并绿表产物统一将页签命名为 `<模块>_R-Port` 和 `<模块>_P-Port`。

## AUTOSAR 规则浅校验调整

两个工具按 AUTOSAR 元模型（多重性、不可拆分聚合、引用元类型）调整了输出和校验，仍只做浅校验：不区分或比对 AUTOSAR 版本，不检查数据类型兼容、总线拓扑或 RTE 语义；只影响生成正确性的问题报 ERROR，其余规范细则只记录 WARNING，由后续 EB 等工具链校验。升级后请对比生成差异并关注新增 WARNING。

System 工具：

- 已有 System 已引用目标 ECU 时，输出不再重复写 `FIBEX-ELEMENTS`（该聚合不可拆分）；引用了其他元素但不含目标 ECU 时只警告，需在原 System 中补齐。基线样例因此减少了 FIBEX-ELEMENTS 片段。
- `general.System` 必须是已加载的 System，短名会解析为其完整路径；根原型必须属于该 System，ECU-INSTANCE 必须存在且类型正确。以前短名可能在 Composition 包下另建 System，现在会报错或合并到已有 System。
- 同名 SystemMapping 或需要追加映射的 `<Composition>_InterfaceMappings` 已存在于输入时报错；请改用其他 `general.systemMapping`，或在原文件中维护接口映射。
- 目标 Composition 内新实例、外部端口和连接器与已有或本次其他对象同名时报错，仅大小写不同只警告；已有同名同端点的连接器直接复用。
- 新增原子组件缺少 SWC-IMPLEMENTATION 改为 WARNING 并跳过 SwcToImplMapping；没有 `Add` 组件时不再生成空 SwcToEcuMapping。
- 放宽：接口映射未覆盖的元素只警告；CAN/LIN 物理通道不存在或在多个簇中同名不再报错，只以最终 SystemSignal 是否存在为准。
- 新增 WARNING：信号组映射缺少 TYPE-MAPPING、已有 System 缺少 SYSTEM-VERSION、接口 DEST 与对象类型不一致、重复的信号映射行。

SWC 工具：

- ComSpec 按数据元素的 `SW-IMPL-POLICY` 选择：队列元素的 P-Port 生成 `QUEUED-SENDER-COM-SPEC`（以前统一为 `NONQUEUED-SENDER-COM-SPEC`）；R-Port 的 `Enable Update` 只作用于非队列元素、`Queue Length` 只作用于队列元素，两列不再互斥；填写的值不适用于任何元素时记录 WARNING 并忽略该列。
- 访问类别与 AUTOSAR 约束冲突（队列元素使用 `Read` 或 `ReceiveByValue`、启用 update 的元素使用 `Read`）只记录 WARNING，访问点照常生成，由 EB 校验。
- SHORT-NAME 或路径仅大小写不同（工作簿内部或与已有 ARXML 相比）只记录 WARNING，按不同对象生成；完全同名仍按原规则报错或复用。访问点去重和 `Data Accesses` 重复判定区分大小写，并加入 `Access Kind`。
- 新增 ERROR：类型引用的元类型错误；含公式或控制字符（含换行）的单元格；目标 ARXML 为 UTF-16/UTF-32 或 GBK 等多字节编码；对 PR-Port 创建访问点；同一访问点的多行填写了不同的 `Access Name`（包括仅大小写不同，以前会静默合并）。
- 目标 ARXML 为 ISO-8859-1、Windows-1252 等单字节编码时，片段按该编码写入，无法表示的字符写成字符引用；以前片段固定按 UTF-8 写入，非 ASCII 字符会与文件声明的编码不一致。
- 确定数据元素后合并重复访问请求，已有同类访问点引用相同端口和元素时直接复用（不论名称）。自动命名避让 runnable 下全部子对象名称。
- 同次运行新建的 runnable 可被服务器调用点和数据访问点引用；单个 CS 对象失败只记 ERROR，不中断汇总。
- 放宽：计算方法与数据约束的上下限按数值比较（`1.0` 与 `1` 相同）；`Array Size Semantics` 留空时不与已有数组比较。
- 新增 WARNING：枚举 Label 不是 C 标识符、同一组件内 runnable `SYMBOL` 重复、类型引用在已加载 ARXML 中找不到。

## 0.3.1 → 0.4.0

两个工具的配置文件改用 TOML（可写 `#` 注释），格式见 [配置文件格式](README.md#配置文件格式)。配置字段、工作簿格式和生成结果均不变。

- 旧的 JSON 配置文件仍兼容，可继续使用，无需修改。
- `table-to-system-arxml` 必须用 `--config` 指定至少一个配置文件，不再默认读取启动目录中的 `config.json`。以前依赖该默认值、直接运行 `table-to-system-arxml` 的脚本，需补上 `--config <配置文件路径>`；否则打印用法并返回 `2`。
- armodel 依赖限定为 `>=1.8.7,<=1.9.8,!=1.9.1`，避免自动安装尚未验证的新版本；后续版本通过兼容测试后才更新上限。已有范围内版本可继续使用，升级到兼容最新版的安装方式见 [使用说明](README.md#安装)。
- Python 3.11 以下新增依赖 `tomli>=1.1.0`，安装 wheel 时自动安装；离线环境需同时准备该依赖包。
- 仓库回归样例配置已改为 TOML：`test/samples/system/*TableToSystemArxmlConfig.toml`、`test/samples/swc/TableToSwcArxml.toml`。

## 0.3.0 → 0.3.1

命令名和当前规范输入的生成结果保持不变；本版本移除了废弃兼容写法，并修复了此前被错误接受的输入。
升级前请完成以下清理：

- 从 SWC 配置中删除 `backend`，命令行不再接受 `--backend`；解析库固定为 armodel。
- 从 System 配置中删除未参与运行的 `project` 字段。未知配置项和命令行参数退出码为 `2`。
- 工作簿只接受当前规范名称，不再识别旧列名、旧页名和旧枚举值。请使用 [当前模板](swc/SwcTemplate.xlsx)，
  将 `SWC` 改为 `SW-C`、`Runnable`/`Runnable Ref` 改为 `Runnable Name`、
  `Create Access` 改为 `Create RTE Artifact`、`Receive Mode` 改为 `Access Kind`、
  `Base Type Ref` 改为 `Implementation Data Type Ref`、`Transmission Acknowledge` 改为
  `Transmission Acknowledge Timeout`、`QueueLength` 改为 `Queue Length`；
  `Data Element`、`Operation`、`Argument` 分别使用对应的 `... Name` 列。
  页名使用 `Data Accesses`，不再使用 `Data-Accesses`；接收方式使用 `ReceiveByArgument`、`ReceiveByValue`，
  不再使用 `Argument`、`Value`。未知名称或取值记录 `ERROR`，阻止写出并返回 `1`。
- 页名、列名和枚举值仍按各自规则不区分大小写；必填项、可省略表、合并单元格和布尔值规则没有改变。
- 开发环境脚本只通过 `TTA_PYTHON` 指定解释器，移除旧变量回退。

本版本的其他输入与输出修复：

- **输出保护**：SWC 的 `outputDir` 不得覆盖任何输入 ARXML，包括路径别名、符号链接或硬链接。
  修改源文件必须显式选择 `inPlace`。System 多配置的输出前缀不得仅大小写不同，所有平台统一拒绝输出冲突。
- **表头**：SWC 工作簿的中间空表头、无表头数据明确报错，不再错位读取或丢弃选项。
- **模型唯一性**：候选文件解析失败会阻止写出；新类型、接口、计算方法和约束不得重复定义其他输入或生成目标中的同路径对象。
  若对象已存在于其他文件，请将 `Target ARXML` 指向已有定义的文件，或改用新的完整路径。
- **类型检查**：System 装配连接即使两端使用同一 SR 接口，缺失类型、错误 DEST、无法解析的位宽/编码或不支持的形状也会失败。
  这类工具校验错误不属于可以忽略的 armodel 解析器诊断；应补齐或修正模型，不提供静默放行。
- **名称**：新建 SHORT-NAME 及 AUTOSAR 路径的各段必须以 ASCII 字母开头，后续只含字母、数字、下划线，最长 128 字符。
  不能填写 `/`、空格或中文名称；枚举标签等文本仍可使用中文。`Runnable Symbol` 使用 C 标识符规则。
  自动生成的长名称截断并附加 8 位哈希，仍会检查冲突；手填名称不自动截断。
- **配置优先级**：System 先应用命令行覆盖，再验证生效值；清单模式不再检查未使用的 EB 配置。
  两工具优先识别已存在的字面路径，目录或文件名中的方括号不会误选其他文件。

对外资料目录仅保留 `system`、`swc`；旧文档入口和模板副本已删除，请更新书签与下载路径。
仓库回归样例移到 `test/samples/system`、`test/samples/swc`，共享模型移到 `test/data/common`；这些路径不属于 wheel 接口。

从更早版本升级时，按 [System 配置](system/configuration.md) 或 [SWC 配置](swc/configuration.md)
重新核对全部参数，不提供旧参数兼容层。模块调用统一为
`python -m table_to_arxml.system` 和 `python -m table_to_arxml.swc`。
