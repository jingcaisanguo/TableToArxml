# 绿表转换规则

返回 [README.md](README.md)。以下规则对应 [green_table.py](../../table_to_system_arxml/table_to_system_arxml/lib/green_table.py)（读取）和 [green_to_white.py](../../table_to_system_arxml/table_to_system_arxml/lib/green_to_white.py)（转换）当前实现，不是通用 Excel 或 AUTOSAR 模板规范。

## 文件、模块与页签

1. 一个绿表文件名（去掉 `.xlsx`）代表一个待加入目标 Composition 的模块，例如 `LIT.xlsx` 对应 `LIT`。不是用文件夹名或页签名作为模块名。
2. 必须包含大小写完全一致的 `INPUT`、`OUTPUT` 两个页签，可以只有表头而无数据；其他页签不参与转换和合并。
3. 第 1 行作为表头跳过，从第 2 行读取；不识别表头文字，按固定列位置取值，不能随意移动列。
4. 数据读取使用 `data_only=True`：公式读取 Excel 保存的缓存结果，不在 Python 中计算公式。使用公式时先在 Excel 中计算并保存。
5. 单元格值统一转字符串并去除首尾空白；空值按空串处理，`nan` 等文本原样保留。数字格式不会自动还原成显示文本，含前导零的名称应使用文本单元格。

输入选择按 `greenTables` 配置顺序处理，每个 glob 的结果按路径排序；同一实际文件路径只处理一次，忽略 Excel 的 `~$*.xlsx` 临时锁文件。glob 无匹配可跳过，全部无匹配则失败；直接指定不存在的文件或命中非 `.xlsx` 文件则报错。目录本身不是绿表输入，需写成 `目录/*.xlsx`。

公共目录与变体目录之间**没有同名覆盖规则**。两个不同路径的同名绿表会都被读取并累积数据，再按白表记录去重；建议每个模块只提供一个输入文件。

## 固定列定义

| 列 | INPUT 页 | OUTPUT 页 |
| --- | --- | --- |
| B | 当前模块接收端口 R-port | 当前模块发送端口 P-port |
| C | 上游类型；`CAN`/`LIN` 触发总线映射，其他值按普通组件 | 下游类型；`CAN`/`LIN` 触发总线映射，其他值按普通组件 |
| H | DataElement；仅总线映射使用，空时回退到 B | DataElement；仅总线映射使用，空时回退到 B |
| I | 普通连接：上游组件名；总线映射：物理通道短名 | 普通连接：下游组件名；总线映射：物理通道短名 |
| J | 普通连接：上游 P-port；总线映射：信号名或完整信号路径 | 普通连接：下游 R-port；总线映射：信号名或完整信号路径 |
| K | 备注，用于不完整行的预留判断 | 备注，用于不完整行的预留判断 |
| 其他列 | 不参与转换，可保留业务信息 | 不参与转换，可保留业务信息 |

C 列先转大写，因此 `can`、`CAN` 等价；通道、组件、端口、信号等名称比较不自动忽略大小写。C 为空也会进入普通组件分支，不会自动判断总线。

## 行有效性与预留

| 条件 | 当前行为 |
| --- | --- |
| B/C/I/J 全空 | 跳过该行 |
| I、J 同时为空 | 静默跳过，即使 B 或 C 有内容 |
| I、J 只有一个为空，K 含 `预留`、`保留`、`备用` 任一子串 | 静默跳过 |
| I、J 只有一个为空，K 无上述关键字 | 报错退出，不发布任何本次产物 |
| I、J 都有值，K 含预留关键字 | 正常继续处理；预留备注不是通用禁用开关 |
| I、J 有值但 B 空 | 报错退出，提示原始工作簿、页签和行号 |

## 模块与 Components

在处理连接之前，工具建立组件查询表并检查全部绿表文件名；任一文件名在查询表中无法解析，会一次性列出缺失模块并抛出异常，主入口返回 1。

查询表来自已加载 ARXML：仅使用目标 `general.Composition` 中已有的 prototype 及类型引用，其他 Composition 的实例不参与。类型按完整路径索引，默认 prototype 为 `prototypePrefix` + 类型短名。短名有歧义时失败，可通过 `componentAliases` 指向完整类型路径或目标 prototype 路径消歧，详见 [configuration.md](configuration.md)。

| 白表列 | 来源 |
| --- | --- |
| `Prototype` | 目标 Composition 的已有 prototype，或按配置前缀与模块别名/类型短名生成 |
| `SW-C` | 与 Prototype 相同的实例标识，避免不同路径同名类型冲突 |
| `Type` | ARXML 中的组件类型完整路径 |
| `Action` | 本次选中绿表所代表的组件为 `Add`；仅被连接引用的其他组件为 `Reference` |

`Add` 表示在目标 Composition 中加入组件实例，不是从绿表新建 SW 组件类型、接口、端口或实现定义。`Reference` 不新增实例，必须在输入模型的目标 Composition 中存在且类型一致。先解析所有绿表模块的实例身份再决定 Add/Reference，使用 prototype 别名时也不受处理顺序影响。

外部组件解析失败、短名歧义、prototype 名冲突以及不存在的 Reference 均报错退出，不跳过后继续生成。不同完整路径的同名类型可通过不同模块别名同时生成，不会合并。

## 普通组件连接

当 C 不是 `CAN`/`LIN`，或命中特殊 `E2EWrapper` 条件时，写入 `AssemblySwConnection`。

| 方向 | Provider SW-C | PPort | Requester SW-C | RPort |
| --- | --- | --- | --- | --- |
| INPUT | I：上游组件 | J：上游 P-port | 当前绿表模块 | B：当前 R-port |
| OUTPUT | 当前绿表模块 | B：当前 P-port | I：下游组件 | J：下游 R-port |

最终连接名由本地生成器取 `ac_<provider prototype>_<PPort>_<requester prototype>_<RPort>`，最长 255 字符；同名但端点不同的连接会报错退出，避免截断碰撞。H 和 K 不写入该连接页。不支持用 `#` 在普通组件、端口或信号字段中批量创建连接。

装配连接强制要求 `P-port -> R-port`；端口引用的 interface 必须存在，默认两端必须是同一完整接口引用。`interfaceMismatch` 仅在 config 中设置，可选 `error`（缺省报错）、`skip`（WARNING 后跳过该连接）、`map`（生成明确的接口映射再连接），不提供命令行参数。映射支持 SenderReceiverInterface 的完整一一数据元素配对，并沿类型引用解析最终位宽、编码/符号性与形状；uint8/uint16、uint8/sint8 均拒绝，相同接口也检查组件各自的应用类型实现。详情见 [configuration.md](configuration.md)。委托连接按内端口生成同方向外端口，不套用装配连接的 P/R 两端规则。

## CAN/LIN 信号映射

C 为 `CAN`/`LIN` 且 I 不精确等于 `E2EWrapper` 时，写入 `SenderReceiverToSignal`，不把通道名添加为组件。

- I 必须是对应总线的物理通道短名。支持 `通道A#通道B`，逐个拆分、去除空白并验证；任一通道无效则整次失败，不发布本次产物。
- J 以 `/` 开头时直接作为完整信号路径；否则按 `signalPathTemplate` 展开，默认 `/${channel}/Signal/${signal}`。这是可配置的工具约定，不是 AUTOSAR 固定包结构。
- INPUT 的方向固定为 `IN`，且 B 必须为 R-port；OUTPUT 固定为 `OUT`，且 B 必须为 P-port。`DataElement` 优先 H，H 空时使用 B，端口必须使用已定义的 SenderReceiverInterface。
- `#` 仅用于本分支的 I 列；多个 J 信号需分行填写。即使 J 已是完整路径，也必须提供有效的 I 通道。
- 白表阶段验证通道和信号方向：通过选定通道的 `I-SIGNAL-TRIGGERING -> I-SIGNAL -> SYSTEM-SIGNAL-REF` 关联 J 所指的完整 SystemSignal 路径，再检查属于 `general.EcuInstance` 的 `I-SIGNAL-PORT` 的 `COMMUNICATION-DIRECTION`。INPUT 要求包含 IN，OUTPUT 要求包含 OUT；两者同时存在时允许对应方向。不会用其他 ECU 的方向替代目标 ECU 的方向，缺少/无效方向也报错。
- 同名不同路径的信号不会合并。物理通道短名存在歧义时拒绝，不任取一个；J 即使填写完整路径也必须在 I 指定的通道找到对应方向证据。
- 生成器还检查组件、端口方向、interface、DataElement 和信号引用，并再次检查目标 ECU 的方向与组成员身份。错误整次失败，不发布本次产物；不提供完整的数据类型、位宽、单位或缩放兼容性校验。
- 只有实际出现总线映射行时才要求相应通道存在；仅普通组件连接的模型不需要 CAN/LIN 通道。

### 信号组限制

普通 `SenderReceiverToSignal` 映射禁止使用 `SYSTEM-SIGNAL-GROUP` / `I-SIGNAL-GROUP` 对象，也禁止直接使用它们的成员信号。成员关系按完整引用检查，覆盖 SystemSignalGroup 的成员、ISignalGroup 的 I-Signal 成员及 triggering 的组引用。报错会提示信号及所属组，不能通过 `interfaceMismatch=skip` 或 `map` 绕过。

工具不会自动将组成员提升为整个组映射，也不拆组绕过检查。绿表当前没有完整的组映射输入定义，组信号应走工程明确设计的组映射流程；现有白表生成器的 `SenderReceiverToSignalGroup` 页能力并不意味着绿表可自动创建完整字段级组映射。

### E2EWrapper 特殊分支

I 精确等于配置的 `e2eWrapperComponent` 时，即使 C 为 `CAN`/`LIN`，也按普通组件端口连接处理，J 是 wrapper 的端口，不是信号。默认名称为 `E2EWrapper`，空字符串禁用特殊分支；工具不会自动生成 E2E wrapper 或 E2E 保护逻辑。

## 示例

以下为假设模块 `Lamp` 的绿表行。组件、端口、通道及信号需要事先存在于输入模型；这里只展示被工具读取的列，其余列保持原位置。

| 页签 | B | C | H | I | J | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| INPUT | `RpRequest` | `APP` | 空 | `Switch` | `PpRequest` | `Switch.PpRequest` -> `Lamp.RpRequest` |
| OUTPUT | `PpStatus` | `APP` | 空 | `Display` | `RpStatus` | `Lamp.PpStatus` -> `Display.RpStatus` |
| INPUT | `RpSpeed` | `CAN` | `Speed` | `CAN_A#CAN_B` | `VehicleSpeed` | 两条 `IN` 映射，信号分别为 `/CAN_A/Signal/VehicleSpeed`、`/CAN_B/Signal/VehicleSpeed` |
| OUTPUT | `PpState` | `LIN` | 空 | `LIN_A` | `/Network/Signals/LampState` | 一条 `OUT` 映射，DataElement 为 `PpState` |
| INPUT | `RpSafe` | `CAN` | 空 | `E2EWrapper` | `PpSafe` | 普通连接 `E2EWrapper.PpSafe` -> `Lamp.RpSafe` |

## 去重与输出页签

去重仅针对本次创建的白表，不是将旧白表内容增量合并进来。

| 记录 | 去重依据 |
| --- | --- |
| Components | `(Prototype, SW-C, Type, Action)` 完全相同才去重；同 Prototype 不同定义报错 |
| AssemblySwConnection | `(Provider SW-C, PPort, Requester SW-C, RPort)` |
| SenderReceiverToSignal | `(Signal, Direction, SW-C, Port, DataElement)` |

白表每次重新创建 6 个页签：`General`、`Components`、`AssemblySwConnection`、`DelegationSwConnection`、`SenderReceiverToSignal`、`SenderReceiverToSignalGroup`。其中 `DelegationSwConnection` 和 `SenderReceiverToSignalGroup` 当前只创建表头，绿表转换不填充这两类记录。

本地白表读取器按表头识别列，支持列重排、忽略空数据行；必需列缺失或重复、General 同一键配置不同值时明确报错。生成器也支持委托连接及信号组页中的有效记录：委托按内端口接口生成外端口，命名为 `dc_<外端口>_<内prototype><内端口>`；信号组要求 DataElement 的 ImplementationDataType 为 STRUCTURE，输出组引用，不自动展开字段级类型映射。主入口仍会重新生成白表，不提供手工编辑白表后继续执行的 CLI 模式。

合并绿表输出保留 INPUT/OUTPUT 的单元格内容、主要样式和工作表格式，以 `<模块>_INPUT`、`<模块>_OUTPUT` 命名页签并截断到 31 字符；重复或截断冲突可能由 openpyxl 自动调整名称。它不保证复制图片、图表等所有 Excel 对象，也不改变源绿表。白表则只保留转换所需的值，不复制绿表格式。

## 白表显示格式

- 六个页签均冻结首行（`A2`），打印时重复标题行；表头使用深青色底、白色加粗文字，标题行高为 28。
- 除 General 外，其余页签启用表头筛选，方便按组件、端口或方向查找。
- 显示默认网格线，表头和数据单元格均加四边细框线，确保底色区域也有清晰的横竖分隔；数据区保留浅色隔行底纹，`Action`、`Direction` 居中，其余内容左对齐。
- 列宽在所有数据写完后按内容估算，兼顾中文等宽字符。端口及 DataElement 列最小宽度为 32，路径类列为 40；列宽上限为 100（Excel 列宽单位）。
- 超过上限的长端口名和路径自动换行，行高根据内容估算调整，最大为 Excel 支持的 409 磅。极端超长文本可在编辑栏查看；不同字体环境的实际显示宽度可能略有差异。
- 只改变单元格显示，不截断名称、不合并数据单元格、不改表头或转换值。生成 ARXML 的读取逻辑不受影响；合并绿表仍保留源表样式，不套用此格式。