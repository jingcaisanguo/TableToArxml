# table-to-swc-arxml 使用说明

返回 [../README.md](../README.md)。根据 Excel 工作簿，向已有 ARXML 中的软件组件**增量**添加端口、接口、数据类型及 RTE 访问对象。
工具只新增，不修改、不删除已有内容；已存在的同名对象按下文“同名对象检查”处理。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [configuration.md](configuration.md) | TOML 配置、命令行参数、路径规则、ARXML 清单、命令示例 |
| [workbook-format.md](workbook-format.md) | 工作簿各工作表、列定义、取值范围及生成的 XML 示例 |
| [SwcTemplate.xlsx](SwcTemplate.xlsx) | 不含测试数据的空白模板，自带填写流程、字段批注、默认值和输入验证 |
| 本文 | 处理流程、检查规则、生成规则、写出与退出码 |

## 安装与运行

安装方式见 [../README.md](../README.md)，安装后提供 `table-to-swc-arxml` 命令（等同 `python -m table_to_arxml.swc`）：

```bash
table-to-swc-arxml --config ./config/swc.toml
```

- 依赖为 `armodel>=1.8.7,<=1.9.8,!=1.9.1`、`openpyxl>=3.1.2`、`packaging>=21.3`，Python 3.11 以下另需 `tomli>=1.1.0`（读取 TOML 配置），安装 wheel 时自动安装。armodel 上限固定在已验证的 `1.9.8`，新版本通过兼容测试后才更新上限；安装了范围外的版本时工具启动即报版本错误。
- 配置、工作簿、清单和日志由使用工具的项目提供。

参数和路径规则见 [configuration.md](configuration.md)。

## 处理流程

```text
1. 解析参数        → 缺少必填项时退出码 2，不创建日志
2. 创建日志，检查工程目录、工作簿、ARXML 清单
3. 读取并校验全部工作表
4. 读取 ARXML 清单
5. 新建数据类型    Enumerations → Arrays / Structures
6. 新建接口        SR-Interfaces → CS-Interfaces
7. 定位组件和端口接口，生成端口及 ComSpec
8. 生成 CS 对象（runnable → operation 事件 → 服务器调用点），只处理上一步成功或已存在的端口
9. 生成 SR 数据访问点（Create RTE Artifact 展开 + Data Accesses），可使用上一步新建的 runnable
10. 输出汇总；无 ERROR 且指定了写出方式时写文件
```

所有修改先在内存中进行，后面的步骤读取的是前面步骤修改后的内容。因此同一次运行中，
端口可以引用本次新建的接口，接口可以引用本次新建的数据类型，访问点和服务器调用点可以位于本次新建的 CS runnable 中，
`--dry-run` 也能完整校验整条链路。
汇总中的“to create”仅表示内存中准备的新对象，不表示已经写盘；实际写出成功后另行报告文件数量。

## 检查规则

工具只做“浅校验”，不识别、不匹配 AUTOSAR 版本（各版本 schema 的 SHORT-NAME 语法一致），完整的 AUTOSAR 规则校验由 EB 导入工程时完成。
工具只检查必填项、SHORT-NAME/C 标识符语法、引用目标存在且元类正确、端口方向与访问类别、
同路径对象（区分大小写）一致和输出可被重新解析；不检查类型兼容、初值、时序、E2E 配置等深层语义。
ARXML 无效、有歧义或引用错误时为 `ERROR`；允许但可疑的配置，以及工具不强制的 AUTOSAR 约束
（SHORT-NAME 仅大小写不同、访问类别与队列策略/ENABLE-UPDATE 的组合、不适用的 ComSpec 列）为 `WARNING`，照常生成，留给 EB 校验。

工具采用“收集全部错误”方式：单条记录出错时记录 `ERROR` 并跳过该记录，其余记录继续处理，
以便一次报告所有问题。只要本次运行出现任一 `ERROR`，就不写任何 ARXML，退出码为 `1`。
`WARNING` 不影响写出。

### 启动检查

| 检查项 | 不满足时 |
| --- | --- |
| `projectRoot`、`arxmlFileList`、`workbook`、`logDir` 都已提供 | 参数错误，退出码 `2` |
| 配置文件可读、语法正确、没有未知键；路径占位符和通配符唯一匹配 | 参数错误，退出码 `2` |
| `inPlace`、`outputDir`、`dryRun` 最多选一个；布尔项为 `true`/`false` | 参数错误，退出码 `2` |
| 工程目录存在，工作簿和清单文件存在 | `ERROR`，退出码 `1` |
| 清单条目扩展名为 `.arxml` 且文件存在，通配符条目至少匹配一个文件 | 默认 `WARNING` 后跳过该条目；`strictList=true` 时直接失败 |
| 清单中同一文件重复出现 | 只保留第一次，`INFO` 记录 |
| 多个 ARXML 中存在同一完整 AUTOSAR 路径的组件、接口或类型定义 | 按清单顺序采用第一个定义并记录 `WARNING`；不比较后续副本，由 EB 导入工程时检查冲突 |
| 清单或生成目标中存在 XML 语法损坏、无法读取的文件 | `ERROR`，阻止写出；即使已找到首个定义，也不忽略后续文件的解析失败。首文件优先不表示允许损坏输入 |
| 清单中至少有一个可用文件 | `ERROR`，退出码 `1` |

### 工作簿检查

结构检查（详见 [workbook-format.md](workbook-format.md#通用规则)）：

| 检查项 | 不满足时 |
| --- | --- |
| 工作簿文件是有效的 `.xlsx` | 损坏或格式不符时 `ERROR`（报告文件名），退出码 `1` |
| 未使用的工作表可省略；端口/访问点引用的 `SW-C` 必须有 Components 映射 | 缺少映射记录 `ERROR` |
| 整本工作簿至少有一条有效记录（端口、类型、接口、访问点任一种） | `ERROR`，退出码 `1` |
| 工作表名属于支持的数据页或 README，表头无未知列、无重复列、必填列齐全（页名、列名不区分大小写） | `ERROR`，跳过该表并阻止整次写出 |
| 单元格为文本、数字或布尔值，不是公式（需粘贴为值） | `ERROR`，跳过该行 |
| 单元格不含换行、制表符等控制字符（`\x00`–`\x1f`） | `ERROR`，报告工作表、列名和行号，跳过该行 |
| 必填单元格非空 | `ERROR`，跳过该行 |

取值检查：

| 检查项 | 适用 |
| --- | --- |
| `... Path`、`... Ref` 以 `/` 开头 | 全部路径和引用列 |
| `Type Path`、`Interface Path` 至少两级（包/名称） | 类型页、接口页 |
| `Target ARXML` 为相对路径且文件存在 | 类型页、接口页 |
| 布尔列为 `Y/N/TRUE/FALSE/YES/NO/1/0` 或空 | 所有布尔列 |
| `Value` 为整数；`Array Size` 为正整数；`Queue Length` 为非负整数 | 对应列 |
| `Minimum Start Interval`、`Transmission Acknowledge Timeout` 为非负数 | `P-Ports` |
| `Direction` 为 `IN`/`OUT`/`INOUT`；`Access Kind` 为 `Send`/`ReceiveByArgument`/`ReceiveByValue`/`Read`/`Write`；`Array Size Semantics` 为 `FIXED-SIZE`/`VARIABLE-SIZE` | 对应列 |
| 枚举 `Label` 不是 C 标识符时记录 `WARNING`（允许写入 VT，但 RTE 可能需要另配 `SYMBOL`） | `Enumerations` |
| `Create RTE Artifact=Y` 时，SR P-Port 和所有 R-Port 填写 `Runnable Name` | 端口页 |
| 端口页 `Access Kind` 与端口方向匹配（P：`Send`/`Write`；R：`ReceiveByArgument`/`ReceiveByValue`/`Read`） | 端口页 |
| 端口页 `Create RTE Artifact` 未启用时的 Runnable/RTE 列 | `WARNING` 后忽略，不校验这些不生效列的值；开关本身仍须合法 |
| `Data Accesses` 的 `Runnable Name` | 始终按 SHORT-NAME 校验，不受端口页的 RTE 开关影响 |
| CS 参数名、方向、类型全填或全空（全空代表无参数 operation） | `CS-Interfaces` |

唯一性和一致性检查（SHORT-NAME 及路径**区分大小写**比较；仅大小写不同的名称视为不同对象，记录 `WARNING` 后照常生成，留给 EB 校验）：

| 范围 | 不能重复 / 必须一致 |
| --- | --- |
| `Components` | `SW-C` 不重复，`Component Path` 不重复（工作簿标识，区分大小写） |
| 端口页 | 同一组件的端口名在 `P-Ports`、`R-Ports` 两页之间不重复；`SW-C` 必须在 `Components` 中定义 |
| 新建对象路径 | 各页新建的类型、计算方法、数据约束和接口路径之间仅大小写不同时 `WARNING` |
| `Enumerations` | 同一枚举的 `Value`、`Label` 不重复；各行的 `Target ARXML`、底层类型、计算方法和约束路径一致 |
| `Arrays` | `Type Path` 不重复 |
| `Structures` | 同一结构体的成员名不重复，`Target ARXML` 一致 |
| `SR-Interfaces` | 同一接口的数据元素名不重复，`Target ARXML`、`Is Service` 一致 |
| `CS-Interfaces` | 同一接口的 operation 名、同一 operation 的参数名不重复；无参数声明不能重复或与参数行混用；同一接口的 `Target ARXML`、`Is Service` 一致 |
| `Data Accesses` | `SW-C` + `Runnable Name` + `Port Name` + `Data Element Name` + `Access Kind` 组合不重复；手写的 `Access Name` 在同一 runnable 内不重复 |

### 模型定位检查

| 检查项 | 不满足时 |
| --- | --- |
| 组件路径在 ARXML 清单中找到定义 | 找不到记录 `ERROR`；多个文件中出现同一完整路径时采用清单中的第一个定义 |
| 端口接口路径在清单中找到定义，且是支持的接口类型 | 找不到或类型不支持时记录 `ERROR`，跳过该端口；多个文件重复时采用第一个定义 |
| 需要生成 ComSpec 时，接口至少有一个数据元素 | `ERROR`，跳过该端口 |
| 显式填写的 ComSpec 列至少适用于一个数据元素：`Queue Length` 为正数时须有队列元素（`SW-IMPL-POLICY=QUEUED`），`Enable Update=Y` 时须有非队列元素 | `WARNING`，忽略不适用的列，端口和其他适用的 ComSpec 照常生成；混合接口中各列只作用于适用的元素 |
| 组件中已有 `PORTS` 容器 | `ERROR`，跳过该端口 |
| 新建对象（类型、接口、端口、访问点、runnable、事件、调用点）与同一命名空间中的已有对象仅 SHORT-NAME 大小写不同 | `WARNING`，同时列出两者路径，按不同对象生成 |
| 新建类型或接口的完整路径已存在 | 采用清单顺序中第一个定义，与工作簿定义比较；一致时复用，不一致时 `ERROR` |
| 需要创建的新类型或接口所在的包已存在于 `Target ARXML` | 不存在时 `ERROR`，跳过该对象 |
| 新建对象写出的类型引用（底层类型、数组元素、结构体成员、数据元素和参数类型）在清单或目标文件中存在时，元类必须适用 | 元类不适用（如引用 `SW-BASE-TYPE`）时 `ERROR`；找不到时 `WARNING`，按未解析引用写出 |
| 要插入片段的 ARXML 为 UTF-8（或未声明编码）或兼容 ASCII 的单字节编码 | UTF-16/UTF-32 或 GBK 等多字节编码时 `ERROR`，不修改该文件 |
| 访问点引用的 runnable 存在于组件内部行为中（含本次新建的 CS runnable） | `ERROR`，跳过该访问点 |
| 访问点引用的端口是本次成功准备或已有的 P/R 端口，类别与端口方向匹配 | `ERROR`，跳过该访问点；`PR-PORT-PROTOTYPE` 不支持 |
| 访问点的数据元素存在于接口中；接口有多个元素时已填写 `Data Element Name` | `ERROR`，跳过该访问点 |
| 队列元素不使用 `Read` 或 `ReceiveByValue`；接收端 ComSpec `ENABLE-UPDATE=true` 的元素不使用 `Read`（AUTOSAR 约束） | `WARNING`，照常生成该访问点，留给 EB 校验 |
| 同一需求（runnable、类别、端口、数据元素相同）的多行填写了不同的 `Access Name`（仅大小写不同也算不同） | `ERROR`；其余重复请求合并为一个，`INFO` 记录 |
| CS 端口的接口至少有一个 operation，组件有内部行为 | `ERROR`，跳过该端口的 CS 对象 |
| 同一组件的 CS runnable `SYMBOL` 与已有或本次新建 runnable 重复 | `WARNING` |
| 自动命名在 100 个候选名内找到可用名称 | `ERROR`，跳过该访问点 |

定位只在 ARXML 清单列出的文件中进行。支持的接口类型及其 `DEST`：`SENDER-RECEIVER-INTERFACE`、
`CLIENT-SERVER-INTERFACE`、`MODE-SWITCH-INTERFACE`、`NV-DATA-INTERFACE`、`PARAMETER-INTERFACE`、`TRIGGER-INTERFACE`。
端口可引用任意一种，但 ComSpec 和 `Create RTE Artifact` 只对 SR、CS 接口有实际内容。

### 同名对象检查

新增对象与同一父对象下已有对象 `SHORT-NAME` 相同时，统一按以下规则处理：

- **定义相同**：记录 `WARNING` 并跳过，不修改已有内容。因此同一工作簿可以重复执行。
- **定义不同或对象类型不同**：记录 `ERROR` 并跳过，提示为新对象换名，已有内容保持不变。

新类型、接口、计算方法和约束还会检查清单及所有 `Target ARXML` 中的同路径对象。
若定义位于其他文件，报错而不是在目标文件复制一份；应指向已有定义文件或更换完整路径。
候选 ARXML 无法解析时也会报错，不在遗漏候选的情况下宣称定义唯一。

只比较对象自身的属性和内部结构；引用只比较路径，不追踪被引用对象的内容：

| 对象 | 比较内容 |
| --- | --- |
| 端口 | P/R 方向、接口路径及 `DEST`；ComSpec 只比较工作簿中**非空**的列：`ENABLE-UPDATE` 元素集合、`QUEUE-LENGTH` 及其元素、发送端超时、E2E 标志及其元素 |
| SR 接口 | 接口类型、数据元素名称与顺序、类型引用、队列属性、`IS-SERVICE` |
| CS 接口 | 接口类型、operation 与参数的名称与顺序、方向、类型引用、`IS-SERVICE` |
| 数组 / 结构体 | 类别、元素或成员的名称与顺序、类型引用、数组长度；长度语义仅在填写 `Array Size Semantics` 时比较 |
| 枚举实现类型 | 类别、底层类型、计算方法和约束的引用路径 |
| 计算方法 / 数据约束 | 取值区间与标签 / 上下限；上下限按数值比较（`1.0` 等于 `1`），`INF` 等非数值按原文比较 |
| 数据访问点 | 端口引用和数据元素引用；同一 runnable 中已有同类别、引用相同端口和数据元素的访问点时，不论名称直接复用（`WARNING ... already exists as <名称>; skipped`） |
| 服务器调用点 | 端口引用、operation 引用、超时 |
| runnable | `SYMBOL`、最小启动间隔、并发标志 |
| operation 事件 | 端口、operation、runnable 引用 |

补充说明：

- 端口比较的 ComSpec 只包括工具能生成的字段，`INIT-VALUE`、`HANDLE-OUT-OF-RANGE` 等不参与比较。
  留空的 ComSpec 列不声明该项，不检查已有端口上对应的 ComSpec；显式填写 `N` 或 `Queue Length=0`
  则要求对应 ComSpec 不存在。ComSpec 种类（队列/非队列）不作为比较项。
- 同一次运行中多个 CS P-Port 使用同一 runnable 名称时，第一次创建，后续定义相同则复用（`INFO`），不同则 `ERROR`。
- 枚举的计算方法、实现类型、数据约束分别检查，已存在的部分跳过，缺失的部分仍会创建，可用于补齐。
- 工作簿同时声明了被引用的接口或类型时，它们作为独立对象各自检查。例如端口一致得到 `WARNING`，
  而单独声明的接口定义冲突仍产生 `ERROR`。

### 生成后校验

每个文件插入端口和访问点后，工具把结果写入临时文件，用 `armodel` 重新解析，
再逐一读回本次处理的端口和访问点，确认内容与工作簿一致；解析诊断记录为 `WARNING`。
插入 CS 对象后同样重新解析一次，解析诊断同样记录为 `WARNING`。读回校验失败时整次运行失败，退出码 `1`，不写任何文件。
单个 CS operation 或插入组失败只记录 `ERROR` 并跳过，其余对象和汇总照常输出。

## 生成规则

### 生成内容

| 工作表 | 生成的 AUTOSAR 元素 | 写入位置 |
| --- | --- | --- |
| `Enumerations` | `COMPU-METHOD`（`TEXTTABLE`）、`IMPLEMENTATION-DATA-TYPE`（`TYPE_REFERENCE`）、`DATA-CONSTR` | 各自路径所在包的 `ELEMENTS` |
| `Arrays` | `IMPLEMENTATION-DATA-TYPE`（`ARRAY`），含一个元素 | `Type Path` 所在包的 `ELEMENTS` |
| `Structures` | `IMPLEMENTATION-DATA-TYPE`（`STRUCTURE`），按行顺序含多个成员 | 同上 |
| `SR-Interfaces` | `SENDER-RECEIVER-INTERFACE` 及 `VARIABLE-DATA-PROTOTYPE` | `Interface Path` 所在包的 `ELEMENTS` |
| `CS-Interfaces` | `CLIENT-SERVER-INTERFACE`、`CLIENT-SERVER-OPERATION`、`ARGUMENT-DATA-PROTOTYPE` | 同上 |
| `P-Ports` / `R-Ports` | `P-PORT-PROTOTYPE` / `R-PORT-PROTOTYPE` 及可选 ComSpec；ComSpec 种类按数据元素的 `SW-IMPL-POLICY` 选择（队列元素用 `QUEUED-*`，其余用 `NONQUEUED-*`） | 组件 `PORTS` 容器末尾 |
| 端口 `Create RTE Artifact=Y`（SR） | 每个数据元素一个 `VARIABLE-ACCESS` | runnable 中对应的访问点容器末尾 |
| 端口 `Create RTE Artifact=Y`（CS R-Port） | 每个 operation 一个 `SYNCHRONOUS-SERVER-CALL-POINT` | runnable 的 `SERVER-CALL-POINTS` |
| 端口 `Create RTE Artifact=Y`（CS P-Port） | 每个 operation 一个 `RUNNABLE-ENTITY` 和一个 `OPERATION-INVOKED-EVENT` | 内部行为的 `RUNNABLES`、`EVENTS` |
| `Data Accesses` | 每行一个 `VARIABLE-ACCESS` | runnable 中对应的访问点容器末尾 |

各元素的完整 XML 结构见 [workbook-format.md](workbook-format.md)。

### 容器规则

| 容器 | 缺失时 |
| --- | --- |
| 包的 `ELEMENTS` | 自动创建（包本身必须已存在） |
| 组件的 `PORTS` | 报 `ERROR`，不创建 |
| runnable 的访问点容器（`DATA-SEND-POINTS`、`DATA-RECEIVE-POINT-BY-ARGUMENTS`、`DATA-RECEIVE-POINT-BY-VALUES`、`DATA-READ-ACCESSS`、`DATA-WRITE-ACCESSS`） | 自动创建 |
| runnable 的 `SERVER-CALL-POINTS` | 自动创建 |
| 内部行为的 `EVENTS` | 自动创建 |
| 内部行为的 `RUNNABLES` | 自动创建 |

自动创建的容器按 AUTOSAR schema（R4.3.1 `AUTOSAR_00044.xsd`）规定的子元素顺序插入：例如包的 `ELEMENTS` 位于 `SHORT-NAME-PATTERN`、`REFERENCE-BASES` 之后、`AR-PACKAGES` 和 `VARIATION-POINT` 之前，`EVENTS` 位于 `EXCLUSIVE-AREAS` 之后、`HANDLE-TERMINATION-AND-RESTART` 之前，`RUNNABLES` 位于 `PORT-API-OPTIONS` 之后、`SERVICE-DEPENDENCYS` 和 `SUPPORTS-MULTIPLE-INSTANTIATION` 之前，访问点容器和 `SERVER-CALL-POINTS` 位于 `SYMBOL` 之前。

容器写成自闭合形式（如 `<PORTS/>`）时无法追加内容，报 `ERROR`，需改写为 `<PORTS></PORTS>`。

### 默认值和固定值

| 对象 | 值 |
| --- | --- |
| 接口 `IS-SERVICE` | 总是写出，默认 `false` |
| 数据元素 `SW-IMPL-POLICY` | 仅 `Is Queued=Y` 时写 `QUEUED`；否则不生成 `SW-DATA-DEF-PROPS` |
| 发送端 ComSpec `HANDLE-OUT-OF-RANGE` | 固定 `NONE` |
| 发送端 ComSpec `TRANSMISSION-ACKNOWLEDGE` | 仅填写超时时生成 |
| 服务器调用点 `TIMEOUT` | 固定 `0.0`，不可配置 |
| 新建 runnable | `MINIMUM-START-INTERVAL` 默认 `0.0`，`CAN-BE-INVOKED-CONCURRENTLY` 默认 `false`，`SYMBOL` 默认与名称相同 |
| 数组 `ARRAY-SIZE-SEMANTICS` | 默认 `FIXED-SIZE` |
| 数据约束上下限 | 取枚举全部 `Value` 的最小值和最大值 |
| 引用的 `DEST` | 按被引用对象的实际元类推导；类型引用找不到被引用对象时写 `IMPLEMENTATION-DATA-TYPE` 并记录 `WARNING` |

### 命名规则

数据访问点 `Access Name` 留空时，在该 runnable 内依次尝试以下名称，取第一个可用的：

1. `<前缀><端口名>`，例如 `dsp_p_LIT_Status`；
2. `<前缀><端口名>_<数据元素>`，例如 `dsp_p_LIT_Status_value`；
3. `<前缀><端口名>_<数据元素>2` 到 `...99`。

前缀：`Send`→`dsp_`，`ReceiveByArgument`→`drparg_`，`ReceiveByValue`→`drpval_`，`Read`→`dra_`，`Write`→`dwa_`。
runnable 是其全部子对象（各类访问点、服务器调用点等）的命名空间，候选名与其中任一 SHORT-NAME 或本次已分配的名称
仅大小写相同也视为占用，自动尝试下一个候选名。已有同类别访问点引用相同端口和数据元素时，不论名称直接复用并 `WARNING` 跳过，
因此重复执行不会生成新的名称。手写的 `Access Name` 与已有对象或本次已分配的名称完全相同时报 `ERROR`，不自动避让；
仅大小写不同时记录 `WARNING` 并照常生成。

新建 SHORT-NAME 及路径各段使用 ASCII 字母开头、字母/数字/下划线组成的名称，长度为 1～128。
手填名称不合法时报错；自动生成的长访问点、CS runnable、事件和调用点名称截断并附加 8 位哈希，仍检查冲突。
`Runnable Symbol` 必须是 C 标识符，可以下划线开头。

| 对象 | 名称 |
| --- | --- |
| 服务器调用点 | `scp_<端口名>_<operation>` |
| operation 事件 | `oie_<端口名>_<operation>` |
| CS P-Port runnable | 未填 `Runnable Name` 时为 `RE_<端口名>_<operation>`；填写时用该名，接口有多个 operation 时追加 `_<operation>`；`SYMBOL` 同理 |
| 枚举计算方法（默认） | `<类型包的上级>/CompuMethods/<类型名>` |
| 枚举数据约束（默认） | `<类型包的上级>/DataConstrs/dc_<类型名>` |

### 格式保持

`armodel` 整文件重新序列化会改写原有 ARXML 的格式，产生大量无关差异。工具因此只把新元素的 XML 片段
插入原文件字节，其余字节保持不变：

- 插入位置按完整 AUTOSAR 路径（各级 `SHORT-NAME`）定位，同一文件中不同组件下同名的 runnable、内部行为互不影响。
- 片段插入到目标容器结束标签之前，缩进取该行缩进再加两个空格。
- 换行符沿用原文件（`CRLF` 或 `LF`）；`<TAG></TAG>` 写在同一行的空容器会被展开成多行。
- 片段按目标文件的编码写入：支持 UTF-8（或未声明编码）以及兼容 ASCII 的单字节编码（ISO-8859-x、Windows-125x 等），该编码无法表示的字符（如 ISO-8859-1 文件中的中文）写成 `&#20013;` 这类字符引用，文件编码不变。UTF-16/UTF-32 无法按字节定位，GBK 等多字节编码 armodel 无法解析，这些文件报 `ERROR`、不修改。枚举标签等文本允许中文，但不是 C 标识符的
  `Label` 会记录 `WARNING`：RTE 通常用 VT 生成枚举常量名，此时可能需要在工程中另配 `SYMBOL`。SHORT-NAME 和路径各段遵循上述名称规则。
- 单元格中的换行、制表符等控制字符会被缩进改写，因此工作簿中不允许出现。
- 从生成文件中删除新增片段后，与源文件逐字节相同。

### 写出规则

| 写出方式 | 行为 |
| --- | --- |
| `dryRun` 或不指定 | 完成全部检查和生成，不写任何文件 |
| `inPlace` | 覆盖源 ARXML |
| `outputDir` | 按文件在 `projectRoot` 内的相对路径写到输出目录；文件不在 `projectRoot` 内时报错 |

- 只要本次运行有 `ERROR`，三种方式都不写任何 ARXML。
- 只写出内容确实变化的文件。
- `outputDir` 模式的最终文件路径不得与任何输入 ARXML 重合，包括符号链接、硬链接等别名；否则整次失败。需要覆盖源文件时必须显式使用 `inPlace`。
- 每个文件先写临时文件再原子替换；多个文件写出中途失败时，尝试把已替换的文件恢复原样。
- 恢复某个文件再次失败时，仍继续恢复其他文件，并在最终错误中汇总未恢复路径和最初的写出错误；应检查这些路径后再重试，不保证断电或存储故障下的跨文件事务。
- `armodel` 使用全局模型单例，不要在同一进程中并行执行多个任务。

## 日志与退出码

日志写入 `logDir` 下的 `table_to_swc_arxml_<时间含微秒>_<进程号>.log`，同时输出到控制台：
普通信息到 stdout，`WARNING`、`ERROR` 到 stderr。控制台与日志文件格式相同，每行为 `<时间戳> <等级>: <消息>`。结束时输出汇总，例如：

```text
2026-01-01 10:00:00,000 INFO: Validated 4 of 4 requested ports: 4 to create, 0 already valid, 0 skipped
2026-01-01 10:00:00,000 INFO: Enumerations: 0 to create, 0 skipped
2026-01-01 10:00:00,000 INFO: Array/structure types: 0 to create, 0 skipped
2026-01-01 10:00:00,000 INFO: SR interfaces: 0 to create, 0 skipped
2026-01-01 10:00:00,000 INFO: CS interfaces: 0 to create, 0 skipped
2026-01-01 10:00:00,000 INFO: CS artifacts: 0 to create, 0 skipped
2026-01-01 10:00:00,000 INFO: Data accesses: 0 of 0 to create, 0 skipped
2026-01-01 10:00:00,000 INFO: All counts describe in-memory changes, not files written
2026-01-01 10:00:00,000 INFO: 2 ARXML file(s) affected
```

`Data accesses` 的请求数已扣除合并的重复请求；复用的已有访问点既不计入新建，也不计入跳过。

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部检查通过；指定了写出方式时已写出 |
| `1` | 存在 `ERROR`、输入文件缺失、生成后校验失败或运行异常；未写任何 ARXML |
| `2` | 命令行或配置错误（缺少必填项、未知配置键、写出方式冲突等）；打印用法，不创建日志 |

## 限制

- 只新增元素，不修改或删除已有元素；已有对象定义不同时只报错。
- 组件、`PORTS` 容器和数据访问点所在的 runnable 必须事先存在（或由本次 CS P-Port 新建）；访问点容器缺失时自动创建。
- 不支持 `PR-PORT-PROTOTYPE`：不创建 PR-Port，也不为其生成访问点。
- 只做浅校验：不检查类型兼容、初值、时序、E2E 配置和 RTE 契约。
- CS 接口不生成 `POSSIBLE-ERRORS`、`POSSIBLE-ERROR-REFS`、`ERROR-CODE`；服务器调用点超时固定为 `0.0`。
- ModeSwitch、NvData、Parameter、Trigger 接口的端口可以创建，但不生成 ComSpec 以外的 RTE 访问对象。
- 不执行 RTE 代码生成，ARXML 更新后需运行项目自己的生成命令。
