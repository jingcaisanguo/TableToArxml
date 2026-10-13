# 白表文件格式

返回 [README.md](README.md)。读取实现见 [../../table_to_system_arxml/table_to_system_arxml/lib/white_table_reader.py](../../table_to_system_arxml/table_to_system_arxml/lib/white_table_reader.py)，生成实现见 [../../table_to_system_arxml/table_to_system_arxml/lib/arxml_generator.py](../../table_to_system_arxml/table_to_system_arxml/lib/arxml_generator.py)。

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
| `AssemblySwConnection` | 可省略 | `Connection`、`Provider SW-C`、`PPort`、`Requester SW-C`、`RPort` |
| `DelegationSwConnection` | 可省略 | `Connection`、`Inner SW-C`、`Inner Port`、`Outer Port` |
| `SenderReceiverToSignal` | 可省略 | `Signal`、`Direction`、`SW-C`、`Port`、`DataElement` |
| `SenderReceiverToSignalGroup` | 可省略 | `SignalGroup`、`SW-C`、`Port`、`DataElement` |

页签缺失、表头不符或 General 不完整时，工具在加载 ARXML 前即报错退出。

## General

键值对，以下 4 个键必须存在且非空；同一键出现多次时值必须相同。

| Key | 值 | 说明 |
| --- | --- | --- |
| `Composition` | 完整路径，如 `/SwComponentType/ZCUP5` | 目标 `COMPOSITION-SW-COMPONENT-TYPE`，新组件实例和连接写入其中 |
| `System` | 完整路径或短名，如 `/ECUExtractZCUP/VehicleProject/SDB326300` | 生成的 SYSTEM；与已加载 System 同路径时沿用其 CATEGORY、SYSTEM-VERSION，并按拆分合并到该 System |
| `RootSwCompositionPrototype` | 完整路径，如 `.../SDB326300/TopLevelComposition` | System 的根组合原型；`Composition` 必须在其下被唯一地逐层实例化 |
| `EcuInstance` | 完整路径，如 `/EcuInstances/ZCUP5` | SwcToEcuMapping 及信号方向校验使用的 ECU |

绿表模式下这些值来自 config `general`（未填的项按 [configuration.md](configuration.md) 推导）；白表模式下只使用白表中的值，不读取 config `general` 的这 4 项（`systemMapping` 仍取自 config）。

## Components

声明本次涉及的组件实例。所有列必填。

| 列 | 说明 |
| --- | --- |
| `Prototype` | 实例 SHORT-NAME，如 `itsCCMWrapper`；`Reference` 时也可写目标 Composition 下的完整路径 |
| `SW-C` | 组件别名，供连接页的 `SW-C` 列引用；通常与 `Prototype` 相同 |
| `Type` | 组件类型完整路径，如 `/CCMWrapper/CCMWrapper`；支持 Application、ComplexDeviceDriver、EcuAbstraction、Service、Composition 类型 |
| `Action` | `Add` 或 `Reference`（不区分大小写） |

- `Add`：在 `Composition` 中新建该实例，并生成 SwcToEcuMapping；原子组件还需能找到其实现，生成 SwcToImplMapping。实例已存在时要求类型一致。
- `Reference`：引用 `Composition` 中已有的实例，只用于连线，不重复创建；实例不存在或类型不一致时报错。
- 同名 `Prototype` 不能对应不同定义；连接页中的组件可用 `SW-C`、`Prototype`、实例完整路径，或在不歧义时用类型路径/类型短名引用。

## AssemblySwConnection

组件之间的装配连接，生成 `ASSEMBLY-SW-CONNECTOR`。

| 列 | 说明 |
| --- | --- |
| `Connection` | 当前不使用，连接名固定自动生成为 `ac_<提供方实例>_<PPort>_<请求方实例>_<RPort>`（最长 255 字符），可留空 |
| `Provider SW-C` | 提供方组件（见 Components 引用规则） |
| `PPort` | 提供方 P-Port 短名 |
| `Requester SW-C` | 请求方组件 |
| `RPort` | 请求方 R-Port 短名 |

两端口接口不同时按 config `interfaceMismatch` 处理：`error` 报错、`skip` 跳过、`map` 生成接口映射（见 [configuration.md](configuration.md)）。接口相同时还会校验 SenderReceiver 数据元素类型是否一致。

## DelegationSwConnection

内部端口委托到 `Composition` 外部端口，生成 `DELEGATION-SW-CONNECTOR`；外部端口在已加载 ARXML 中不存在时才一并生成。

| 列 | 说明 |
| --- | --- |
| `Connection` | 当前不使用，连接名自动生成为 `dc_<Outer Port>_<内实例><Inner Port>` |
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

会校验信号在 `EcuInstance` 上的 I-SIGNAL-PORT 通信方向与 `Direction` 一致。

## SenderReceiverToSignalGroup

端口数据元素到信号组的映射，生成 `SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING`，要求 `generateSystem` 为 `true`。

| 列 | 说明 |
| --- | --- |
| `SignalGroup` | SystemSignalGroup 完整路径 |
| `SW-C` | 组件 |
| `Port` | 端口短名，接口须为 SenderReceiverInterface |
| `DataElement` | 数据元素短名，其实现数据类型须为 `STRUCTURE` |

不自动展开字段级类型映射。

## 示例

`General`：

| Key | Value |
| --- | --- |
| Composition | /SwComponentType/ZCUP5 |
| System | /ECUExtractZCUP/VehicleProject/SDB326300 |
| RootSwCompositionPrototype | /ECUExtractZCUP/VehicleProject/SDB326300/TopLevelComposition |
| EcuInstance | /EcuInstances/ZCUP5 |

`Components`：

| Prototype | SW-C | Type | Action |
| --- | --- | --- | --- |
| itsCCMWrapper | itsCCMWrapper | /CCMWrapper/CCMWrapper | Add |
| itsIoHwAb_ProxyMgr_OsApplication_C1_P0 | itsIoHwAb_ProxyMgr_OsApplication_C1_P0 | /IoHwAb_ProxyMgr_OsApplication_C1_P0/SwComponentTypes/IoHwAb_ProxyMgr_OsApplication_C1_P0 | Reference |

`AssemblySwConnection`：

| Connection | Provider SW-C | PPort | Requester SW-C | RPort |
| --- | --- | --- | --- | --- |
| | itsIoHwAb_ProxyMgr_OsApplication_C1_P0 | AI_FBLW_LPM_DIAG | itsCCMWrapper | R_AI_FBLW_LPM_DIAG |

生成结果：在 `/SwComponentType/ZCUP5` 中新增 `itsCCMWrapper` 实例及连接 `ac_itsIoHwAb_ProxyMgr_OsApplication_C1_P0_AI_FBLW_LPM_DIAG_itsCCMWrapper_R_AI_FBLW_LPM_DIAG`，并在 System `SDB326300` 中生成 `itsCCMWrapper` 的 SwcToEcuMapping 与 SwcToImplMapping。
