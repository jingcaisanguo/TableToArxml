# 工作簿（Excel）格式

返回 [README.md](README.md)。运行参数见 [configuration.md](configuration.md)。

## 从模板开始

下载 [SwcTemplate.xlsx](SwcTemplate.xlsx)，填写后另存为项目工作簿，并修改配置中的 `workbook`。
模板没有测试数据；仓库的 `SwcTestPorts.xlsx` 是含 LIT/Horn 数据的回归样例，不应直接用于其他工程。

- **只加端口**：填写 `Components` 和 `P-Ports` / `R-Ports`；`Create RTE Artifact` 留空或 `N`。
- **新类型、接口和端口**：按“类型 → 接口 → 端口”填写，后面的对象可以引用本次新建对象。
- **数据访问点**：端口页 `Create RTE Artifact=Y` 自动处理全部元素；`Data Accesses` 用于部分元素或自定义名称。
  同一访问需求不要同时填两处。

模板 README 包含上述流程、SR/CS × P/R 条件说明、默认值、限制及逐字段说明。
点击 README 中的工作表标题跳转输入页；点击输入页 `A1` 返回对应说明。

### 阅读与填写辅助

- 深蓝表头为必填，棕色为条件必填，灰蓝为可选；精确条件和默认值见表头批注。不要给列名添加 `*`。
- P/R 页前六列统一为 `SW-C`、`Port Name`、`Port Interface Ref`、`Create RTE Artifact`、`Runnable Name`、`Access Kind`；
  后面为 ComSpec，P 页最后三列为 CS 服务端配置。冻结第一行和左侧两列。
- 每页使用 Excel 表格。通过表格末尾 `Tab` 追加行可自动扩展筛选；在表外粘贴后应“调整表格大小”。
  工具读取整页，并非只读筛选范围，隐藏/筛选掉的行也会处理。
- 下拉框包含支持的布尔值，数值列提供整数/非负数提示；端口与访问点的 `SW-C` 可从 Components 选择。
  数据验证允许留空，条件必填和引用一致性仍由工具校验。粘贴可能绕过 Excel 验证。
- 列顺序可以改变，但移动后应检查 Excel 数据验证是否仍对应正确列；不要插入空表头的分隔列。
- 模板只为实际数据和一个空输入行设置格式，避免给百万行创建单元格。新增行沿用 Excel 表格格式。

## 通用规则

### 工作表与表头

- 下表列出支持的 9 个数据工作表，名称不区分大小写。未使用的表可以省略，也可保留表头而不填数据。
- 除 `README` 说明页外，不接受其他工作表名；未知工作表记录 `ERROR` 并阻止写出，不静默忽略。
- 填写端口或访问点时，所用 `SW-C` 必须在 `Components` 中定义；否则记录 `ERROR`，不是静默跳过。
- 可以只新建类型或接口而不填端口；整本工作簿没有任何有效记录时记录 `ERROR`。
- 每页第 1 行是表头，按名称识别列，**不区分大小写**，列顺序可任意调整。
- 未知列、重复列或缺少必填列时，整张工作表记录 `ERROR` 并跳过。
- 可选列允许整列不存在，也允许单元格留空。

| 工作表 | 用途 | 一行代表 |
| --- | --- | --- |
| `Components` | 组件简称与 AUTOSAR 路径 | 一个组件 |
| `Enumerations` | 新建枚举实现数据类型 | 一个枚举取值 |
| `Arrays` | 新建数组实现数据类型 | 一个数组 |
| `Structures` | 新建结构体实现数据类型 | 一个结构体成员 |
| `SR-Interfaces` | 新建 Sender-Receiver 接口 | 一个数据元素 |
| `CS-Interfaces` | 新建 Client-Server 接口 | 一个 operation 参数，或一个无参数 operation |
| `P-Ports` | 新建提供端口 | 一个端口 |
| `R-Ports` | 新建请求端口 | 一个端口 |
| `Data Accesses` | 逐元素新建数据访问点（高级用法） | 一个访问点 |

工作表和列名只接受本文规定的名称，不识别别名。请从当前模板开始填写。

### 单元格

- 单元格可以是文本、数字或 Excel 布尔值；首尾空白会去掉。数字按原值转为文本，整数形式的浮点数（如 `8.0`）按整数处理。
- 合并单元格会展开为逐行值；模板使用 Excel 表格，建议逐行重复公共字段，避免合并妨碍排序和筛选。
- 所有单元格都为空的行被忽略。必填单元格为空时，该行记录 `ERROR` 并跳过。
- 布尔列接受 `TRUE`/`YES`/`Y`/`1` 和 `FALSE`/`NO`/`N`/`0`（不区分大小写），留空视为否；其他值记录 `ERROR` 并跳过该行。

### 命名约定

| 列名后缀 | 含义 |
| --- | --- |
| `... Path` | 对象自身的完整 AUTOSAR 路径；`Component Path` 指已有组件，其余类型/接口路径用于新建对象 |
| `... Ref` | 写入 ARXML 引用的目标路径 |
| `... Name` | AUTOSAR `SHORT-NAME` |
| `Target ARXML` | 插入新对象的已有文件，相对 `projectRoot`，允许 `..`；不是最终导出文件名 |

- `... Path`、`... Ref` 必须以 `/` 开头；`Type Path`、`Interface Path` 还必须至少包含“包/名称”两级。
- 每个路径段和 `... Name` 都必须是合法 SHORT-NAME：ASCII 字母开头，只含字母、数字和下划线，最长 128 字符；不允许空段、末尾 `/` 或中文名称。`SW-C` 是工作簿标识，不套用此规则。
- `Target ARXML` 必须是相对路径且文件已存在；写绝对路径或文件不存在都记录 `ERROR`。
- 工作簿不填写 `DEST`。端口接口引用按查到的接口类型推导；新类型和 SR/CS 数据类型列明确引用 `IMPLEMENTATION-DATA-TYPE`。这不是完整的跨文件类型语义校验。

## Components

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `SW-C` | 是 | 工作簿内使用的唯一标识，供端口页和 `Data Accesses` 引用；不要求等于组件 SHORT-NAME |
| `Component Path` | 是 | 组件类型的完整 AUTOSAR 路径 |

`SW-C` 和 `Component Path` 在本页内都不能重复。组件不在本页新建，必须已存在于 ARXML 清单中。

```text
SW-C | Component Path
LIT | /BcmSystem/SwComponentTypes/LIT
```

## Enumerations

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `Type Path` | 是 | 枚举 `IMPLEMENTATION-DATA-TYPE` 的路径 |
| `Value` | 是 | 整数取值 |
| `Label` | 是 | 取值对应的文本 |
| `Implementation Data Type Ref` | 是 | 底层实现数据类型，如 `/AUTOSAR_Platform/ImplementationDataTypes/uint8`，不是 `SW-BASE-TYPE` |
| `Compu Method Ref` | 否 | 计算方法路径，留空时自动推导 |
| `Data Constr Ref` | 否 | 数据约束路径，留空时自动推导 |
| `Target ARXML` | 是 | 写入的文件 |

- `Type Path` 相同的多行组成一个枚举，按行顺序生成取值。
- 同一枚举内 `Value`、`Label` 都不能重复。
- 同一枚举的各行，`Target ARXML`、`Implementation Data Type Ref`、`Compu Method Ref`、`Data Constr Ref` 必须一致。
- 默认路径取数据类型所在包的同级包：类型 `/BcmSystem/ImplementationDataTypes/Mode` 的计算方法为
  `/BcmSystem/CompuMethods/Mode`，数据约束为 `/BcmSystem/DataConstrs/dc_Mode`。

每个枚举生成三个元素：

```xml
<COMPU-METHOD>
  <SHORT-NAME>Mode</SHORT-NAME>
  <CATEGORY>TEXTTABLE</CATEGORY>
  <COMPU-INTERNAL-TO-PHYS>
    <COMPU-SCALES>
      <COMPU-SCALE>
        <LOWER-LIMIT>0</LOWER-LIMIT>
        <UPPER-LIMIT>0</UPPER-LIMIT>
        <COMPU-CONST><VT>Mode_Off</VT></COMPU-CONST>
      </COMPU-SCALE>
    </COMPU-SCALES>
  </COMPU-INTERNAL-TO-PHYS>
</COMPU-METHOD>

<IMPLEMENTATION-DATA-TYPE>
  <SHORT-NAME>Mode</SHORT-NAME>
  <CATEGORY>TYPE_REFERENCE</CATEGORY>
  <SW-DATA-DEF-PROPS>
    <SW-DATA-DEF-PROPS-VARIANTS>
      <SW-DATA-DEF-PROPS-CONDITIONAL>
        <COMPU-METHOD-REF DEST="COMPU-METHOD">/BcmSystem/CompuMethods/Mode</COMPU-METHOD-REF>
        <DATA-CONSTR-REF DEST="DATA-CONSTR">/BcmSystem/DataConstrs/dc_Mode</DATA-CONSTR-REF>
        <IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">/AUTOSAR_Platform/ImplementationDataTypes/uint8</IMPLEMENTATION-DATA-TYPE-REF>
      </SW-DATA-DEF-PROPS-CONDITIONAL>
    </SW-DATA-DEF-PROPS-VARIANTS>
  </SW-DATA-DEF-PROPS>
</IMPLEMENTATION-DATA-TYPE>

<DATA-CONSTR>
  <SHORT-NAME>dc_Mode</SHORT-NAME>
  <DATA-CONSTR-RULES>
    <DATA-CONSTR-RULE>
      <PHYS-CONSTRS>
        <LOWER-LIMIT>0</LOWER-LIMIT>
        <UPPER-LIMIT>2</UPPER-LIMIT>
      </PHYS-CONSTRS>
    </DATA-CONSTR-RULE>
  </DATA-CONSTR-RULES>
</DATA-CONSTR>
```

数据约束的上下限取全部 `Value` 的最小值和最大值。三个元素所在的包都必须已存在。

## Arrays

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `Type Path` | 是 | 数组 `IMPLEMENTATION-DATA-TYPE` 的路径，本页内不能重复 |
| `Element Name` | 是 | 数组元素的 `SHORT-NAME` |
| `Element Type Ref` | 是 | 元素的实现数据类型 |
| `Array Size` | 是 | 正整数 |
| `Array Size Semantics` | 否 | `FIXED-SIZE`（默认）或 `VARIABLE-SIZE` |
| `Target ARXML` | 是 | 写入的文件 |

生成 `CATEGORY=ARRAY` 的实现数据类型，含一个 `CATEGORY=TYPE_REFERENCE` 的元素，元素带 `ARRAY-SIZE`、
`ARRAY-SIZE-SEMANTICS` 及指向 `Element Type Ref` 的 `IMPLEMENTATION-DATA-TYPE-REF`。

## Structures

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `Type Path` | 是 | 结构体 `IMPLEMENTATION-DATA-TYPE` 的路径 |
| `Member Name` | 是 | 成员 `SHORT-NAME`，同一结构体内不能重复 |
| `Member Type Ref` | 是 | 成员的实现数据类型，可引用数组、枚举、结构体或已有类型 |
| `Target ARXML` | 是 | 写入的文件，同一结构体的各行必须一致 |

`Type Path` 相同的多行按行顺序组成一个 `CATEGORY=STRUCTURE` 的结构体，每个成员为 `CATEGORY=TYPE_REFERENCE`。

## SR-Interfaces

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `Interface Path` | 是 | `SENDER-RECEIVER-INTERFACE` 的路径 |
| `Data Element Name` | 是 | 数据元素 `SHORT-NAME`，同一接口内不能重复 |
| `Data Type Ref` | 是 | 写入 `TYPE-TREF` 的实现数据类型 |
| `Is Queued` | 否 | 布尔，默认 `N`；`Y` 时生成 `SW-IMPL-POLICY=QUEUED` |
| `Is Service` | 否 | 布尔，默认 `N`，写入接口的 `IS-SERVICE` |
| `Target ARXML` | 是 | 写入的文件 |

`Interface Path` 相同的多行组成一个接口，按行顺序生成数据元素；各行的 `Target ARXML`、`Is Service` 必须一致。

```xml
<SENDER-RECEIVER-INTERFACE>
  <SHORT-NAME>ExampleSrIf</SHORT-NAME>
  <IS-SERVICE>false</IS-SERVICE>
  <DATA-ELEMENTS>
    <VARIABLE-DATA-PROTOTYPE>
      <SHORT-NAME>value</SHORT-NAME>
      <SW-DATA-DEF-PROPS>
        <SW-DATA-DEF-PROPS-VARIANTS>
          <SW-DATA-DEF-PROPS-CONDITIONAL>
            <SW-IMPL-POLICY>QUEUED</SW-IMPL-POLICY>
          </SW-DATA-DEF-PROPS-CONDITIONAL>
        </SW-DATA-DEF-PROPS-VARIANTS>
      </SW-DATA-DEF-PROPS>
      <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/AUTOSAR_Platform/ImplementationDataTypes/uint8</TYPE-TREF>
    </VARIABLE-DATA-PROTOTYPE>
    <VARIABLE-DATA-PROTOTYPE>
      <SHORT-NAME>status</SHORT-NAME>
      <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/AUTOSAR_Platform/ImplementationDataTypes/uint8</TYPE-TREF>
    </VARIABLE-DATA-PROTOTYPE>
  </DATA-ELEMENTS>
</SENDER-RECEIVER-INTERFACE>
```

非队列元素不生成空的 `SW-DATA-DEF-PROPS`。队列元素通常应与 `R-Ports` 的正整数 `Queue Length` 配合使用。

## CS-Interfaces

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `Interface Path` | 是 | `CLIENT-SERVER-INTERFACE` 的路径 |
| `Operation Name` | 是 | operation `SHORT-NAME` |
| `Argument Name` | 有参数时 | 参数 `SHORT-NAME`，同一 operation 内不能重复 |
| `Direction` | 有参数时 | `IN`、`OUT`、`INOUT`（不区分大小写） |
| `Data Type Ref` | 有参数时 | 参数的实现数据类型 |
| `Is Service` | 否 | 布尔，默认 `N` |
| `Target ARXML` | 是 | 写入的文件 |

一行一个参数。同一 `Interface Path` + `Operation Name` 的多行组成一个 operation，同一接口可有多个 operation；
各行的 `Target ARXML`、`Is Service` 必须一致。当前不生成 `POSSIBLE-ERRORS`、`POSSIBLE-ERROR-REFS` 和 `ERROR-CODE`。

无参数 operation 只填一行，`Argument Name`、`Direction`、`Data Type Ref` 全空（也允许省略这三列）。
参数三列只填一部分时记录 `ERROR`；同一 operation 不能重复声明无参数，也不能混用无参数声明和参数行。

## P-Ports 和 R-Ports

两页只有前三列必填，其余列都是可选的，可以整列不存在。
部分可选列在 `Create RTE Artifact=Y` 时条件必填，详见下表。
同一组件的端口名在两页之间也不能重复，因为它们都写入同一个 `PORTS` 容器。

公共列：

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `SW-C` | 是 | 必须是 `Components` 中的简称 |
| `Port Name` | 是 | 端口 `SHORT-NAME` |
| `Port Interface Ref` | 是 | 端口接口的完整路径，可以是本次新建的接口 |
| `Create RTE Artifact` | 否 | 布尔，默认 `N`；`Y` 时为接口全部元素或 operation 生成 RTE 访问对象，见下文 |
| `Runnable Name` | 条件必填 | 只填 SHORT-NAME，不填路径；SR P/R 和 CS R 指已有 runnable，CS P 指新建名称，具体规则见下文 |
| `Access Kind` | 否 | 仅 SR 接口：访问点类别，取值同 [Data Accesses](#data-accesses)，须与端口方向匹配；留空时 P-Port 取 `Send`、R-Port 取 `ReceiveByArgument` |

`P-Ports` 专有列：

| 列 | 说明 |
| --- | --- |
| `Runnable Name` | SR 接口：已有 runnable 的简称，`Create RTE Artifact=Y` 时必填；CS 接口：新建 runnable 的名称，可留空 |
| `Runnable Symbol` | CS 接口新建 runnable 的 `SYMBOL`，留空时与 runnable 名相同 |
| `Minimum Start Interval` | CS 新建 runnable 的最小启动间隔，单位秒，非负数，默认 `0.0` |
| `Can Be Invoked Concurrently` | CS 新建 runnable 是否可并发，布尔，默认 `N` |
| `Transmission Acknowledge Timeout` | 发送确认超时（秒），非负数，留空不生成 |
| `Uses End To End Protection` | 布尔，默认 `N` |

`R-Ports` 专有列：

| 列 | 说明 |
| --- | --- |
| `Runnable Name` | 已有 runnable 的简称，`Create RTE Artifact=Y` 时必填 |
| `Enable Update` | 布尔，默认 `N` |
| `Queue Length` | 非负整数，留空或 `0` 表示不生成 |

`Enable Update=Y` 与正数 `Queue Length` 互斥，同时填写记录 `ERROR`。
端口的 Runnable/RTE 配置列仅在相应端口方向、接口类型和 `Create RTE Artifact` 设置下生效；
取值合法但当前不生效的非空列记录 `WARNING` 并忽略；非法取值仍记录 `ERROR`。
例如 `Create RTE Artifact=N` 时填写合法的最小启动间隔会警告，但填 `abc` 会报错。

### ComSpec

新端口在满足下列条件时为接口的**每个**数据元素生成一条 ComSpec。
仅填 `N` 或 `Queue Length=0` 不会触发；超时明确填写 `0` 则会触发。
全部留空不生成 `PROVIDED-COM-SPECS` / `REQUIRED-COM-SPECS`。

| 端口 | 条件 | 生成的 ComSpec |
| --- | --- | --- |
| P-Port | 超时非空（含 `0`），或 `Uses End To End Protection=Y` | `NONQUEUED-SENDER-COM-SPEC`：`DATA-ELEMENT-REF`、`HANDLE-OUT-OF-RANGE=NONE`、`TRANSMISSION-ACKNOWLEDGE/TIMEOUT`（仅超时有值时）、`USES-END-TO-END-PROTECTION` |
| R-Port | `Enable Update=Y` | `NONQUEUED-RECEIVER-COM-SPEC`：`DATA-ELEMENT-REF`、`ENABLE-UPDATE=true` |
| R-Port | `Queue Length` 为正整数 | `QUEUED-RECEIVER-COM-SPEC`：`DATA-ELEMENT-REF`、`QUEUE-LENGTH` |

```xml
<R-PORT-PROTOTYPE>
  <SHORT-NAME>r_ExamplePort</SHORT-NAME>
  <REQUIRED-COM-SPECS>
    <NONQUEUED-RECEIVER-COM-SPEC>
      <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/BcmSystem/PortInterfaces/ExampleSrIf/value</DATA-ELEMENT-REF>
      <ENABLE-UPDATE>true</ENABLE-UPDATE>
    </NONQUEUED-RECEIVER-COM-SPEC>
  </REQUIRED-COM-SPECS>
  <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/BcmSystem/PortInterfaces/ExampleSrIf</REQUIRED-INTERFACE-TREF>
</R-PORT-PROTOTYPE>
```

### Create RTE Artifact

`Create RTE Artifact=Y` 时按接口类型生成：

| 端口 | 生成内容 | 前提 |
| --- | --- | --- |
| SR P-Port，`Access Kind=Send`（默认） | 每个数据元素一个 `DATA-SEND-POINT`（`VARIABLE-ACCESS`） | `Runnable Name` 指向已有 runnable，且其中已有对应容器 |
| SR P-Port，`Access Kind=Write` | 每个数据元素一个 `DATA-WRITE-ACCESS` | 同上 |
| SR R-Port，`Access Kind=ReceiveByArgument`（默认） | 每个数据元素一个 `DATA-RECEIVE-POINT-BY-ARGUMENT` | 同上 |
| SR R-Port，`Access Kind=ReceiveByValue` | 每个数据元素一个 `DATA-RECEIVE-POINT-BY-VALUE` | 同上 |
| SR R-Port，`Access Kind=Read` | 每个数据元素一个 `DATA-READ-ACCESS` | 同上 |
| CS R-Port | 每个 operation 一个 `SYNCHRONOUS-SERVER-CALL-POINT`，`TIMEOUT` 固定为 `0.0` | `Runnable Name` 指向已有 runnable |
| CS P-Port | 每个 operation 一个 `RUNNABLE-ENTITY` 和一个 `OPERATION-INVOKED-EVENT` | 组件有内部行为 |
| 其他接口类型 | 不生成 | — |

CS 生成对象的名称：

| 对象 | 名称 |
| --- | --- |
| 服务器调用点 | `scp_<端口名>_<operation>` |
| operation 事件 | `oie_<端口名>_<operation>` |
| runnable | 留空时 `RE_<端口名>_<operation>`；填写 `Runnable Name` 时用该名，接口有多个 operation 时追加 `_<operation>` |
| runnable `SYMBOL` | 留空时与 runnable 名相同；填写且有多个 operation 时追加 `_<operation>` |

## Data Accesses

常规场景优先使用端口页的 `Create RTE Artifact=Y`。本页用于自定义访问点名称或类别、只处理接口的部分数据元素，
一行一个 `VARIABLE-ACCESS`。

| 列 | 必填 | 说明 |
| --- | --- | --- |
| `SW-C` | 是 | 必须是 `Components` 中的简称 |
| `Runnable Name` | 是 | 组件内部行为中已有 runnable 的简称 |
| `Port Name` | 是 | 本次新建或 ARXML 中已有的端口 |
| `Data Element Name` | 否 | 接口只有一个数据元素时可留空自动选用，多个时必填 |
| `Access Kind` | 否 | 见下表；留空时 P-Port 取 `Send`，R-Port 取 `ReceiveByArgument` |
| `Access Name` | 否 | 留空时自动命名 |

`SW-C` + `Runnable Name` + `Port Name` + `Data Element Name` 组合不能重复。

| Access Kind（不区分大小写） | 容器 | 端口方向 | 自动名称前缀 |
| --- | --- | --- | --- |
| `Send` | `DATA-SEND-POINTS` | P | `dsp_` |
| `ReceiveByArgument` | `DATA-RECEIVE-POINT-BY-ARGUMENTS` | R | `drparg_` |
| `ReceiveByValue` | `DATA-RECEIVE-POINT-BY-VALUES` | R | `drpval_` |
| `Read` | `DATA-READ-ACCESSS` | R | `dra_` |
| `Write` | `DATA-WRITE-ACCESSS` | P | `dwa_` |

类别与端口方向不符时记录 `ERROR`。runnable 中必须已有对应容器，工具不新建访问点容器。

```xml
<VARIABLE-ACCESS>
  <SHORT-NAME>dsp_p_ExamplePort</SHORT-NAME>
  <ACCESSED-VARIABLE>
    <AUTOSAR-VARIABLE-IREF>
      <PORT-PROTOTYPE-REF DEST="P-PORT-PROTOTYPE">/BcmSystem/SwComponentTypes/LIT/p_ExamplePort</PORT-PROTOTYPE-REF>
      <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/BcmSystem/PortInterfaces/ExampleSrIf/value</TARGET-DATA-PROTOTYPE-REF>
    </AUTOSAR-VARIABLE-IREF>
  </ACCESSED-VARIABLE>
</VARIABLE-ACCESS>
```

自动命名规则见 [README.md](README.md#命名规则)。
