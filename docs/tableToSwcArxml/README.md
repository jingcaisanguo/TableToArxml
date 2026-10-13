# 表格转 SWC ARXML 工具（table_to_swc_arxml）

根据 Excel 工作簿，向已有 ARXML 中的软件组件**增量**添加端口、接口、数据类型及 RTE 访问对象。
工具只新增，不修改、不删除已有内容；已存在的同名对象按下文“同名对象检查”处理。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [configuration.md](configuration.md) | JSON 配置、命令行参数、路径规则、ARXML 清单、命令示例 |
| [workbook-format.md](workbook-format.md) | 工作簿各工作表、列定义、取值范围及生成的 XML 示例 |
| [swc_arxml_template.xlsx](swc_arxml_template.xlsx) | 工作簿模板，首个工作表为填写说明；复制到项目中填写后使用 |
| 本文 | 处理流程、检查规则、生成规则、写出与退出码 |

源码：[table_to_swc_arxml.py](../../table_to_swc_arxml/table_to_swc_arxml.py) 为源码启动脚本；
[cli.py](../../table_to_swc_arxml/table_to_swc_arxml/cli.py) 负责读取工作簿、校验和编排修改；
[lib/arxml_backend.py](../../table_to_swc_arxml/table_to_swc_arxml/lib/arxml_backend.py) 封装 `armodel` 的解析和片段生成。

## 安装与运行

在仓库根目录打包并安装，安装后提供 `table-to-swc-arxml` 命令（等同 `python -m table_to_swc_arxml`）：

```bash
python -m pip wheel --no-deps --wheel-dir ./table_to_swc_arxml ./table_to_swc_arxml
python -m pip install ./table_to_swc_arxml/table_to_swc_arxml-0.1.0-py3-none-any.whl
table-to-swc-arxml --project-root . --config test/tableToSwcArxml/TableToSwcArxml.json
```

- wheel 只包含 Python 代码，不含模板、配置或日志，这些由使用工具的项目提供。
- 依赖为 `armodel>=1.8.7`、`openpyxl>=3.1.2`，安装 wheel 时自动安装。实测 armodel 1.8.7 和 1.9.2 可用；1.9.4 及以后缺少 `ARBoolean` 等导出，不能使用，应先安装兼容版本再安装本工具。
- 与 `table-to-system-arxml` 共用同一 `armodel` 版本，两个工具可装在同一环境。
- 发布新版本前修改 `table_to_swc_arxml/pyproject.toml` 中的 `version`。

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
8. 生成 SR 数据访问点（Create RTE Artifact 展开 + Data-Accesses）
9. 生成 CS 对象（服务器调用点、runnable、operation 事件）
10. 输出汇总；无 ERROR 且指定了写出方式时写文件
```

所有修改先在内存中进行，后面的步骤读取的是前面步骤修改后的内容。因此同一次运行中，
端口可以引用本次新建的接口，接口可以引用本次新建的数据类型，`--dry-run` 也能完整校验整条链路。

## 检查规则

工具采用“收集全部错误”方式：单条记录出错时记录 `ERROR` 并跳过该记录，其余记录继续处理，
以便一次报告所有问题。只要本次运行出现任一 `ERROR`，就不写任何 ARXML，退出码为 `1`。
`WARNING` 不影响写出。

### 启动检查

| 检查项 | 不满足时 |
| --- | --- |
| `projectRoot`、`arxmlFileList`、`workbook`、`logDir` 都已提供 | 参数错误，退出码 `2` |
| JSON 配置可读、是对象、没有未知键；路径占位符和通配符唯一匹配 | 参数错误，退出码 `2` |
| `inPlace`、`outputDir`、`dryRun` 最多选一个；布尔项为 `true`/`false` | 参数错误，退出码 `2` |
| 工程目录存在，工作簿和清单文件存在 | `ERROR`，退出码 `1` |
| 清单条目扩展名为 `.arxml` 且文件存在 | 默认 `WARNING` 后跳过该条目；`strictList=true` 时直接失败 |
| 清单中至少有一个可用文件 | `ERROR`，退出码 `1` |

### 工作簿检查

结构检查（详见 [workbook-format.md](workbook-format.md#通用规则)）：

| 检查项 | 不满足时 |
| --- | --- |
| 9 个工作表都存在（可以只有表头） | `ERROR`，该表按空表处理 |
| 表头无未知列、无重复列、必填列齐全（列名不区分大小写，旧列名可识别） | `ERROR`，跳过整张表 |
| 单元格为文本、数字或布尔值 | `ERROR`，跳过该行 |
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
| `Direction` 为 `IN`/`OUT`/`INOUT`；`Receive Mode` 为 `Argument`/`Value`；`Array Size Semantics` 为 `FIXED-SIZE`/`VARIABLE-SIZE`；`Access Kind` 为五种类别之一 | 对应列 |
| `Enable Update=Y` 与正数 `Queue Length` 不同时出现 | `R-Ports` |
| SR P-Port 在 `Create RTE Artifact=Y` 时填写 `Runnable Name`；R-Port 填写 `Runnable Ref` | 端口页 |

唯一性和一致性检查：

| 范围 | 不能重复 / 必须一致 |
| --- | --- |
| `Components` | `SWC` 不重复，`Component Path` 不重复 |
| 端口页 | 同一组件的端口名在 `P-Ports`、`R-Ports` 两页之间不重复；`SWC` 必须在 `Components` 中定义 |
| `Enumerations` | 同一枚举的 `Value`、`Label` 不重复；各行的 `Target ARXML`、底层类型、计算方法和约束路径一致 |
| `Arrays` | `Type Path` 不重复 |
| `Structures` | 同一结构体的成员名不重复，`Target ARXML` 一致 |
| `SR-Interfaces` | 同一接口的数据元素名不重复，`Target ARXML`、`Is Service` 一致 |
| `CS-Interfaces` | 同一 operation 的参数名不重复，同一接口的 `Target ARXML`、`Is Service` 一致 |
| `Data-Accesses` | `SWC` + `Runnable Ref` + `Port Name` + `Data Element Name` 组合不重复；手写的 `Access Name` 在同一 runnable 内不重复 |

### 模型定位检查

| 检查项 | 不满足时 |
| --- | --- |
| 组件路径在 ARXML 清单中**恰好**找到一个定义 | 找不到或找到多个都记录 `ERROR`，跳过该组件的全部端口和访问点 |
| 端口接口路径在清单中恰好找到一个定义，且是支持的接口类型 | `ERROR`，跳过该端口 |
| 需要生成 ComSpec 时，接口至少有一个数据元素 | `ERROR`，跳过该端口 |
| 组件中已有 `PORTS` 容器 | `ERROR`，跳过该端口 |
| 新建类型或接口所在的包已存在于 `Target ARXML` | `ERROR`，跳过该对象 |
| 访问点引用的 runnable 存在于组件内部行为中 | `ERROR`，跳过该访问点 |
| 访问点引用的端口是本次新建或已有的端口，类别与端口方向匹配 | `ERROR`，跳过该访问点 |
| 访问点的数据元素存在于接口中；接口有多个元素时已填写 `Data Element Name` | `ERROR`，跳过该访问点 |
| runnable 中已有对应的访问点容器（如 `DATA-SEND-POINTS`） | `ERROR`，跳过该访问点 |
| CS 端口的接口至少有一个 operation，组件有内部行为 | `ERROR`，跳过该端口的 CS 对象 |
| 自动命名在 100 个候选名内找到可用名称 | `ERROR`，跳过该访问点 |

定位只在 ARXML 清单列出的文件中进行。支持的接口类型及其 `DEST`：`SENDER-RECEIVER-INTERFACE`、
`CLIENT-SERVER-INTERFACE`、`MODE-SWITCH-INTERFACE`、`NV-DATA-INTERFACE`、`PARAMETER-INTERFACE`、`TRIGGER-INTERFACE`。
端口可引用任意一种，但 ComSpec 和 `Create RTE Artifact` 只对 SR、CS 接口有实际内容。

### 同名对象检查

新增对象与同一父对象下已有对象 `SHORT-NAME` 相同时，统一按以下规则处理：

- **定义相同**：记录 `WARNING` 并跳过，不修改已有内容。因此同一工作簿可以重复执行。
- **定义不同或对象类型不同**：记录 `ERROR` 并跳过，提示为新对象换名，已有内容保持不变。

只比较对象自身的属性和内部结构；引用只比较路径，不追踪被引用对象的内容：

| 对象 | 比较内容 |
| --- | --- |
| 端口 | P/R 方向、接口路径及 `DEST`；ComSpec 中的 `ENABLE-UPDATE` 元素集合、`QUEUE-LENGTH` 及其元素、发送端超时和 E2E 标志及其元素 |
| SR 接口 | 接口类型、数据元素名称与顺序、类型引用、队列属性、`IS-SERVICE` |
| CS 接口 | 接口类型、operation 与参数的名称与顺序、方向、类型引用、`IS-SERVICE` |
| 数组 / 结构体 | 类别、元素或成员的名称与顺序、类型引用、数组长度与长度语义 |
| 枚举实现类型 | 类别、底层类型、计算方法和约束的引用路径 |
| 计算方法 / 数据约束 | 取值区间与标签 / 上下限 |
| 数据访问点 | 端口引用和数据元素引用 |
| 服务器调用点 | 端口引用、operation 引用、超时 |
| runnable | `SYMBOL`、最小启动间隔、并发标志 |
| operation 事件 | 端口、operation、runnable 引用 |

补充说明：

- 端口比较的 ComSpec 只包括工具能生成的字段，`INIT-VALUE`、`HANDLE-OUT-OF-RANGE` 等不参与比较。
  工作簿未填 ComSpec 列而已有端口带有这些 ComSpec，或相反，都算定义不同。
- 枚举的计算方法、实现类型、数据约束分别检查，已存在的部分跳过，缺失的部分仍会创建，可用于补齐。
- 工作簿同时声明了被引用的接口或类型时，它们作为独立对象各自检查。例如端口一致得到 `WARNING`，
  而单独声明的接口定义冲突仍产生 `ERROR`。

### 生成后校验

每个文件插入端口和访问点后，工具把结果写入临时文件，用 `armodel` 重新解析，
再逐一读回本次处理的端口和访问点，确认内容与工作簿一致；解析诊断记录为 `WARNING`。
插入 CS 对象后同样重新解析一次。校验失败时整次运行失败，退出码 `1`，不写任何文件。

## 生成规则

### 生成内容

| 工作表 | 生成的 AUTOSAR 元素 | 写入位置 |
| --- | --- | --- |
| `Enumerations` | `COMPU-METHOD`（`TEXTTABLE`）、`IMPLEMENTATION-DATA-TYPE`（`TYPE_REFERENCE`）、`DATA-CONSTR` | 各自路径所在包的 `ELEMENTS` |
| `Arrays` | `IMPLEMENTATION-DATA-TYPE`（`ARRAY`），含一个元素 | `Type Path` 所在包的 `ELEMENTS` |
| `Structures` | `IMPLEMENTATION-DATA-TYPE`（`STRUCTURE`），按行顺序含多个成员 | 同上 |
| `SR-Interfaces` | `SENDER-RECEIVER-INTERFACE` 及 `VARIABLE-DATA-PROTOTYPE` | `Interface Path` 所在包的 `ELEMENTS` |
| `CS-Interfaces` | `CLIENT-SERVER-INTERFACE`、`CLIENT-SERVER-OPERATION`、`ARGUMENT-DATA-PROTOTYPE` | 同上 |
| `P-Ports` / `R-Ports` | `P-PORT-PROTOTYPE` / `R-PORT-PROTOTYPE` 及可选 ComSpec | 组件 `PORTS` 容器末尾 |
| 端口 `Create RTE Artifact=Y`（SR） | 每个数据元素一个 `VARIABLE-ACCESS` | runnable 中对应的访问点容器末尾 |
| 端口 `Create RTE Artifact=Y`（CS R-Port） | 每个 operation 一个 `SYNCHRONOUS-SERVER-CALL-POINT` | runnable 的 `SERVER-CALL-POINTS` |
| 端口 `Create RTE Artifact=Y`（CS P-Port） | 每个 operation 一个 `RUNNABLE-ENTITY` 和一个 `OPERATION-INVOKED-EVENT` | 内部行为的 `RUNNABLES`、`EVENTS` |
| `Data-Accesses` | 每行一个 `VARIABLE-ACCESS` | runnable 中对应的访问点容器末尾 |

各元素的完整 XML 结构见 [workbook-format.md](workbook-format.md)。

### 容器规则

| 容器 | 缺失时 |
| --- | --- |
| 包的 `ELEMENTS` | 自动创建（包本身必须已存在） |
| 组件的 `PORTS` | 报 `ERROR`，不创建 |
| runnable 的访问点容器（`DATA-SEND-POINTS`、`DATA-RECEIVE-POINT-BY-ARGUMENTS`、`DATA-RECEIVE-POINT-BY-VALUES`、`DATA-READ-ACCESSS`、`DATA-WRITE-ACCESSS`） | 报 `ERROR`，不创建 |
| runnable 的 `SERVER-CALL-POINTS` | 自动创建，放在 `SYMBOL` 之前 |
| 内部行为的 `EVENTS` | 自动创建，放在 `EXCLUSIVE-AREAS` 或 `RUNNABLES` 之前 |
| 内部行为的 `RUNNABLES` | 自动创建，放在 `SERVICE-DEPENDENCIES` 之前 |

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
| 引用的 `DEST` | 按被引用对象的实际类型推导 |

### 命名规则

数据访问点 `Access Name` 留空时，在该 runnable 内依次尝试以下名称，取第一个可用的：

1. `<前缀><端口名>`，例如 `dsp_p_LIT_Status`；
2. `<前缀><端口名>_<数据元素>`，例如 `dsp_p_LIT_Status_value`；
3. `<前缀><端口名>_<数据元素>2` 到 `...99`。

前缀：`Send`→`dsp_`，`ReceiveByArgument`→`drparg_`，`ReceiveByValue`→`drpval_`，`Read`→`dra_`，`Write`→`dwa_`。
候选名已被同一 runnable 中引用相同端口和数据元素的访问点使用时，直接复用该名称（随后按同名对象规则 `WARNING` 跳过），
因此重复执行不会生成新的名称。手写的 `Access Name` 不参与上述自动避让。

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

- 片段插入到目标容器结束标签之前，缩进取该行缩进再加两个空格。
- 换行符沿用原文件（`CRLF` 或 `LF`）；`<TAG></TAG>` 写在同一行的空容器会被展开成多行。
- 片段按 UTF-8 编码，名称和标签可以使用中文。
- 从生成文件中删除新增片段后，与源文件逐字节相同。

### 写出规则

| 写出方式 | 行为 |
| --- | --- |
| `dryRun` 或不指定 | 完成全部检查和生成，不写任何文件 |
| `inPlace` | 覆盖源 ARXML |
| `outputDir` | 按文件在 `projectRoot` 内的相对路径写到输出目录；文件不在 `projectRoot` 内时报错 |

- 只要本次运行有 `ERROR`，三种方式都不写任何 ARXML。
- 只写出内容确实变化的文件。
- 每个文件先写临时文件再原子替换；多个文件写出中途失败时，尝试把已替换的文件恢复原样。
- `armodel` 使用全局模型单例，不要在同一进程中并行执行多个任务。

## 日志与退出码

日志写入 `logDir` 下的 `table_to_swc_arxml_<时间含微秒>_<进程号>.log`，同时输出到控制台：
普通信息到 stdout，`WARNING`、`ERROR` 到 stderr。结束时输出汇总，例如：

```text
Validated 4 of 4 requested ports: 4 to create, 0 already valid, 0 skipped; 0 enumeration(s) to create, 0 skipped;
0 array/structure type(s) to create, 0 skipped; 0 SR interface(s) to create, 0 skipped; 0 CS interface(s) to create,
0 skipped; 0 of 0 data access(es) to create; 0 CS artifact(s) to create, 0 skipped; 2 ARXML file(s) affected.
```

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部检查通过；指定了写出方式时已写出 |
| `1` | 存在 `ERROR`、输入文件缺失、生成后校验失败或运行异常；未写任何 ARXML |
| `2` | 参数错误（缺少必填项、未知配置键、写出方式冲突等） |

## 限制

- 只新增元素，不修改或删除已有元素；已有对象定义不同时只报错。
- 组件、`PORTS` 容器、runnable 及其访问点容器必须事先存在。
- CS 接口不生成 `POSSIBLE-ERRORS`、`POSSIBLE-ERROR-REFS`、`ERROR-CODE`；服务器调用点超时固定为 `0.0`。
- ModeSwitch、NvData、Parameter、Trigger 接口的端口可以创建，但不生成 ComSpec 以外的 RTE 访问对象。
- 不执行 RTE 代码生成，ARXML 更新后需运行项目自己的生成命令。

## 更换解析库

[lib/arxml_backend.py](../../table_to_swc_arxml/table_to_swc_arxml/lib/arxml_backend.py) 集中封装了 `armodel` 的全部用法，
`cli.py` 只依赖与库无关的抽象：`PortInfo`、`AccessInfo` 描述端口和访问点，`ArxmlDocument` 提供查询和片段生成，
`ArxmlBackend` 负责加载文件并返回文档和解析诊断。

更换解析库时：在该模块中新增实现 `ArxmlDocument` 的文档类和提供 `name`、`load()` 的后端类，
注册到 `BACKENDS`，运行时用 `--backend <名称>` 选择；`cli.py` 无需改动。
