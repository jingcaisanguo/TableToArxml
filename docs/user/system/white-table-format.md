# 白表文件格式

返回 [README.md](README.md)。

白表（`<outputPrefix>WhiteTable.xlsx`）是 Composition/System ARXML 的直接输入：绿表模式下由工具生成，白表模式（config 写 `whiteTable` 或命令行传 `--white-table`）下由用户提供。本文描述两种模式共用的格式与语义。

## 通用规则

- 文件为 `.xlsx`，页签名区分大小写，必须与下表一致。
- 每页第 1 行是表头，按**表头文字**识别列（不按列号），列顺序可调整、可增加其他列；必需列缺失或重复时报错。
- 从第 2 行开始为数据，所有单元格读取后去除首尾空白；整行必需列都为空的行忽略。
- 引用的类型、端口、接口、信号等必须能在本次加载的 ARXML（lst）中找到。

| 页签 | 是否必需 | 必需列 |
| --- | --- | --- |
| `General` | 必需 | `Key`、`Value` |
| `Components` | 必需 | `Prototype`、`SW-C`、`Type`、`Action` |
| `AssemblySwConnection` | 可省略 | `Provider SW-C`、`PPort`、`Requester SW-C`、`RPort` |
| `DelegationSwConnection` | 可省略 | `Inner SW-C`、`Inner Port`、`Outer Port` |
| `SenderReceiverToSignal` | 可省略 | `Signal`、`Direction`、`SW-C`、`Port`、`DataElement` |
| `SenderReceiverToSignalGroup` | 可省略 | `SignalGroup`、`SW-C`、`Port`、`DataElement` |

页签缺失、表头不符或 General 不完整时，工具在加载 ARXML 前即报错退出。数据行的引用错误逐行记录 ERROR，全部处理完后再退出。

连接名由工具自动生成，白表无需填写连接名称列。

## General

键值对，以下 4 个键必须存在且非空；同一键出现多次时值必须相同。

| Key | 值 | 说明 |
| --- | --- | --- |
| `Composition` | 完整路径，如 `/SwComponentTypes/TopComposition` | 目标 `COMPOSITION-SW-COMPONENT-TYPE`，新组件实例和连接写入其中 |
| `System` | 完整路径或短名，如 `/VehicleProject/MySystem` | 已加载的 SYSTEM，短名须唯一；生成到同一完整路径并沿用其 CATEGORY、SYSTEM-VERSION，按拆分合并到该 System；启用 `generateSystem` 时不存在即报错 |
| `RootSwCompositionPrototype` | 完整路径，如 `/VehicleProject/MySystem/TopLevelComposition` | System 的根组合原型；`Composition` 必须在其下被唯一地逐层实例化 |
| `EcuInstance` | 完整路径，如 `/EcuInstances/MyEcu` | SwcToEcuMapping 及信号映射使用的 ECU |

绿表模式下这些值来自 config `general`（未填的项按 [configuration.md](configuration.md) 推导）；白表模式下只使用白表中的值，不读取 config `general` 的这 4 项（`systemMapping` 仍取自 config）。

## Components

声明本次涉及的组件实例。所有列必填。

| 列 | 说明 |
| --- | --- |
| `Prototype` | 实例 SHORT-NAME，如 `itsLampCtrl`；`Reference` 时也可写目标 Composition 下的完整路径 |
| `SW-C` | 组件别名，供连接页的 `SW-C` 列引用；通常与 `Prototype` 相同 |
| `Type` | 组件类型完整路径，如 `/LampCtrl/LampCtrl`；支持 Application、ComplexDeviceDriver、EcuAbstraction、Service、SensorActuator、NvBlock、ServiceProxy、Parameter、Composition 类型 |
| `Action` | `Add` 或 `Reference`（不区分大小写） |

- `Add`：在 `Composition` 中新建该实例，并生成 SwcToEcuMapping；原子组件能唯一找到其 SWC-IMPLEMENTATION 时生成 SwcToImplMapping，找不到时只警告。实例已存在时要求类型一致，不重复写出实例，只生成 System 映射。
- `Reference`：引用 `Composition` 中已有的实例，只用于连线，不重复创建；实例不存在或类型不一致时报错。
- 同名 `Prototype` 不能对应不同定义；新实例与目标 Composition 中的实例、端口、连接器不能重名，比较不区分大小写。连接页中的组件可用 `SW-C`、`Prototype`、实例完整路径，或在不歧义时用类型路径/类型短名引用。

## AssemblySwConnection

组件之间的装配连接，生成 `ASSEMBLY-SW-CONNECTOR`，连接名为 `ac_<提供方实例>_<PPort>_<请求方实例>_<RPort>`；超过 AUTOSAR SHORT-NAME 上限 128 字符时截断并以 8 位哈希结尾。不同连接因拼接得到同名时追加结构化哈希并警告；与目标 Composition 中已有对象重名时报错，已有同名同端点的连接器直接复用，已有连接器连接同一对端口但名称不同时警告并不重复生成。

| 列 | 说明 |
| --- | --- |
| `Provider SW-C` | 提供方组件（见 Components 引用规则） |
| `PPort` | 提供方 P-Port 短名 |
| `Requester SW-C` | 请求方组件 |
| `RPort` | 请求方 R-Port 短名 |

两端口接口不同时按 config `interfaceMismatch` 处理：`error` 报错、`skip` 跳过、`map` 生成接口映射（见 [configuration.md](configuration.md)）。接口相同时不检查数据元素及其类型。

## DelegationSwConnection

内部端口委托到 `Composition` 外部端口，生成 `DELEGATION-SW-CONNECTOR`，连接名为 `dlc_<Outer Port>_<内实例>_<Inner Port>`（长度规则同装配连接）；外部端口在已加载 ARXML 中不存在时才一并生成。

| 列 | 说明 |
| --- | --- |
| `Inner SW-C` | 内部组件 |
| `Inner Port` | 内部端口短名 |
| `Outer Port` | `Composition` 外部端口短名。已存在（如 SDB 中已定义）时直接引用，其方向和接口须与内部端口一致，否则报错；不存在时按内部端口的方向和接口创建；同名外部端口必须一致 |

## SenderReceiverToSignal

端口数据元素到系统信号的映射，生成 `SENDER-RECEIVER-TO-SIGNAL-MAPPING`，要求 `generateSystem` 为 `true`。

| 列 | 说明 |
| --- | --- |
| `Signal` | SystemSignal 完整路径，如 `/<Channel>/Signal/<Signal>` |
| `Direction` | `IN`（端口须为 R-Port）或 `OUT`（端口须为 P-Port） |
| `SW-C` | 组件 |
| `Port` | 端口短名，接口须为 SenderReceiverInterface |
| `DataElement` | 接口中的数据元素短名 |

仅校验表格方向与 R/P 端口方向一致，以及接口、数据元素、SystemSignal 和 ECU 引用存在；不追踪 I-SIGNAL-PORT 实际通信方向。组成员单独映射仅记录 WARNING；完全相同的映射行只生成一条并警告。

## SenderReceiverToSignalGroup

端口数据元素到信号组的映射，生成 `SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING`，要求 `generateSystem` 为 `true`。

| 列 | 说明 |
| --- | --- |
| `SignalGroup` | SystemSignalGroup 完整路径 |
| `SW-C` | 组件 |
| `Port` | 端口短名，接口须为 SenderReceiverInterface |
| `DataElement` | 接口中已存在的数据元素短名，不检查数据类型 |

不自动展开字段级类型映射，也不生成 AUTOSAR 要求的 `TYPE-MAPPING`（1..1）；每行记录 WARNING，需在集成时补齐。

## 示例

`General`：

| Key | Value |
| --- | --- |
| Composition | /SwComponentTypes/TopComposition |
| System | /VehicleProject/MySystem |
| RootSwCompositionPrototype | /VehicleProject/MySystem/TopLevelComposition |
| EcuInstance | /EcuInstances/MyEcu |

`Components`：

| Prototype | SW-C | Type | Action |
| --- | --- | --- | --- |
| itsLampCtrl | itsLampCtrl | /LampCtrl/LampCtrl | Add |
| itsIoHwAb | itsIoHwAb | /IoHwAb/SwComponentTypes/IoHwAb | Reference |

`AssemblySwConnection`：

| Provider SW-C | PPort | Requester SW-C | RPort |
| --- | --- | --- | --- |
| itsIoHwAb | AI_LAMP_DIAG | itsLampCtrl | R_AI_LAMP_DIAG |

生成结果：在 `/SwComponentTypes/TopComposition` 中新增 `itsLampCtrl` 实例及连接 `ac_itsIoHwAb_AI_LAMP_DIAG_itsLampCtrl_R_AI_LAMP_DIAG`，并在 System `MySystem` 中生成 `itsLampCtrl` 的 SwcToEcuMapping 与 SwcToImplMapping。
