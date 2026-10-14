# 配置参数

返回 [README.md](README.md)。

配置使用 UTF-8 JSON 对象，兼容 UTF-8 BOM，字段名区分大小写，不支持 JSON 注释。下例路径和 AUTOSAR 标识均为示意，使用时替换为工程实际值。

## 完整示例

以下为使用 XDM 的 EB 模式示例，不能包含 `arxmlFileList` 字段。

```json
{
    "project": "MY_PROJECT",
    "defaultVariant": "variantA",
    "buildType": "Debug",
    "projectRoot": "..",
    "logLevel": "INFO",
    "outputDir": "output",
    "logDir": "log",
    "outputPrefix": "Bcm",
    "interfaceMismatch": "error",
    "greenTables": [
        "tables/common/Bcm/*.xlsx",
        "tables/${variant}/Bcm/*.xlsx"
    ],
    "tresosPreferenceFiles": [
        "../app/variants/${variant}/tresos/.prefs/pref_imp_exp_Bsw__Importer.xdm",
        "../app/variants/${variant}/tresos/.prefs/pref_imp_exp_SysD.xdm"
    ],
    "general": {
        "Composition": "/ARDesign/SystemDesign/TopLevelComposition",
        "System": "PrototypeSystem",
        "EcuInstance": "/ARDesign/TopologyDesign/FLZCU"
    }
}
```

## 顶层参数

| 参数                      | 类型               | 必填与默认行为                                                                                                                                                                                                                                                                                                      |
| ------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project`               | 字符串             | 可选说明标签；当前代码不读取，不参与路径或命名                                                                                                                                                                                                                                                                      |
| `defaultVariant`        | 字符串             | 与`--variant` 至少提供一个非空值；命令行优先                                                                                                                                                                                                                                                                      |
| `buildType`             | 字符串             | EB 模式可选，缺省或空值取`Debug`；复用 lst 时忽略                                                                                                                                                                                                                                                                 |
| `projectRoot`           | 路径字符串         | 与`--project-root` 至少提供一个，命令行优先；相对路径基于配置文件所在目录                                                                                                                                                                                                                                                             |
| `logLevel`              | 字符串             | 可选，默认`INFO`；`--log-level` 优先；多配置运行且未传 `--log-level` 时，各配置写了的值必须一致                                                                                                                                                                                                                                                |
| `outputDir`             | 路径字符串         | 与`--output-dir` 至少提供一个非空目录；命令行优先，无默认值；相对路径基于配置文件所在目录                                                                                                                                                                                                                         |
| `logDir`                | 路径字符串         | 与`--log-dir` 至少提供一个非空目录；命令行优先，无默认值；相对路径基于配置文件所在目录；不能与产物目录相同                                                                                                                                                                                                        |
| `outputPrefix`          | 字符串             | 必填非空值，例如`Bcm`；应仅填写名称前缀，不含目录、扩展名或 `System` 后缀                                                                                                                                                                                                                                       |
| `greenTables`           | 字符串或字符串数组 | 与`whiteTable` 二选一且不能同时存在；写则为绿表模式。具体`.xlsx` 文件或 glob，最终必须找到至少一个绿表                                                                                                                                                                                                          |
| `whiteTable`            | 路径字符串         | 与`greenTables` 二选一且不能同时存在；写则为白表模式，直接由该白表生成并只发布 ARXML。基于配置目录，支持 `${variant}`；`--white-table` 可强制白表模式                                                                                                                                                         |
| `tresosPreferenceFiles` | 字符串或字符串数组 | 仅 EB 模式必填；指向具体 XDM 文件，不展开 glob；有`arxmlFileList` 时可省略，即使保留也完全忽略                                                                                                                                                                                                                    |
| `arxmlFileList`         | 路径字符串         | 写了该字段就选择清单模式，必须是非空字符串且文件存在；空值、类型错误或文件不存在直接报错，不回退 EB；使用 XDM 时须删除该字段                                                                                                                                                                                        |
| `tresosOutputBaseDir`   | 路径字符串         | EB 模式可选，显式值基于配置目录；未填时按`projectRoot/build/${variant}.${buildType}` 计算；复用 lst 时忽略                                                                                                                                                                                                        |
| `excludeArxmlPatterns`  | 字符串或字符串数组 | 可选，追加 ARXML 排除正则；不替换内置排除规则                                                                                                                                                                                                                                                                       |
| `excludeArxml` | 字符串数组 | 可选，默认 `[]`；排除文件名或相对 `projectRoot` 的路径；`--exclude-arxml` 可重复，提供时覆盖配置列表 |
| `general`               | 对象               | 必填，成员说明见下表                                                                                                                                                                                                                                                                                                |
| `componentAliases`      | 对象               | 可选，绿表文件名/组件别名到组件类型完整路径或目标 Composition 内 prototype 完整路径的映射                                                                                                                                                                                                                           |
| `prototypePrefix`       | 字符串             | 可选，默认`its`；用于没有已有目标实例的组件，允许空字符串                                                                                                                                                                                                                                                         |
| `e2eWrapperComponent`   | 字符串             | 可选，默认`E2EWrapper`；该名称按普通组件处理，空字符串禁用特殊分支                                                                                                                                                                                                                                                |
| `signalPathTemplate`    | 字符串             | 可选，默认`/${channel}/Signal/${signal}`；必须是绝对 AUTOSAR 路径并含 `${signal}`，只支持 `${channel}`、`${signal}`                                                                                                                                                                                         |
| `arRelease`             | 字符串             | 可选，默认`R19-11`；必须是当前 armodel 支持的 release，否则失败且不发布产物                                                                                                                                                                                                                                       |
| `interfaceMismatch`     | 字符串             | 仅支持 JSON 配置；缺省为`error`，可选 `error`、`skip`、`map`；不提供命令行覆盖                                                                                                                                                                                                                              |
| `interfaceMappings`     | 对象数组           | 可选，在`map` 模式下为不同名称的数据元素显式配对，格式见下文                                                                                                                                                                                                                                                      |
| `generateSystem`        | 布尔               | 可选，默认`true`，输出 SYSTEM（SwcToEcu/SwcToImpl/信号映射）。`general.System` 在已加载 ARXML 中存在时，生成到同一完整路径并沿用其 CATEGORY、SYSTEM-VERSION 和根组合，实例引用自动补全根组合到 `general.Composition` 的中间实例；为 `false` 时只输出 Composition，存在 CAN/LIN 信号映射时不允许为 `false` |

字符串数组参数也可写单个字符串；空字符串条目会被忽略。配置中出现上表以外的键，或 `general` 中出现下文 General 表以外的键时，直接报错退出并列出支持的键，避免拼错的字段被静默忽略。

排除示例：`"excludeArxml": ["Unused.arxml", "arxml/Bcm/TUR.arxml"]`。
命令行写法：`--exclude-arxml Unused.arxml --exclude-arxml arxml/Bcm/TUR.arxml`。
文件名匹配所有同名文件（不区分大小写）；带目录的值精确匹配工程根目录下的路径，兼容 `/` 和 `\`，不支持通配符或正则。
该规则与 `excludeArxmlPatterns`、内置排除规则叠加，日志记录 `Exclude ARXML file`，不修改 lst。
全部排除时报错退出；被排除模型中的组件、接口等不能参与生成。多配置运行时命令行列表对所有配置生效，配置列表只对各自清单生效，最终仍合并各清单的保留文件。

产物名称统一由 `outputPrefix` 推导，要创建的组件由绿表文件名确定。

## 路径基准与占位符

| 项目                        | 相对路径基准                                                | 可替换内容                                 |
| --------------------------- | ----------------------------------------------------------- | ------------------------------------------ |
| `projectRoot`             | 当前配置文件所在目录                                        | 不展开占位符                               |
| `outputDir`、`logDir`   | 当前配置文件所在目录                                        | 不展开占位符                               |
| `greenTables`             | 当前配置文件所在目录                                        | `${variant}`，支持 `*`、`**` 等 glob |
| `tresosPreferenceFiles`   | 当前配置文件所在目录                                        | `${variant}`                             |
| `arxmlFileList`           | 当前配置文件所在目录                                        | `${variant}`                             |
| 显式`tresosOutputBaseDir` | 当前配置文件所在目录                                        | `${variant}`、`${buildType}`           |
| lst**内部**的路径     | 复用 lst 时仅`projectRoot`；EB 模式另尝试 tresos 工程目录 | 不展开 JSON 占位符；支持 glob              |
| 命令行路径参数              | 启动时工作目录                                              | 不自动展开`${variant}`                   |

上述文件系统路径均可使用本机绝对路径，相对路径兼容 `/` 和 `\`。JSON 推荐使用 `/`；Windows 的 `\` 在 JSON 字符串中需要写成 `\\`。POSIX 平台拒绝 Windows 盘符/UNC 路径，迁移时应使用相对路径或本机绝对路径。未展开的 `${...}` 会报错；不自动展开 `~`、`%ENV%` 或任意环境变量。`general` 中 `/ARDesign/...` 是 AUTOSAR 模型引用路径，不做文件系统分隔符替换。

例如清单位于配置目录上一级的 `cfg/` 下时，可将 `arxmlFileList` 设置为 `../cfg/${variant}.lst`。此路径指向 lst 本身；lst 内部条目不会因此改成相对 lst 所在目录解析。

优先级为：`--arxml-file-list` > config 中存在的 `arxmlFileList` > EB 生成 `output/<variant>.lst`。选定的 lst 不存在时直接报错，不检查 XDM，也不回退 EB。CLI `--arxml-file-list` 的已有覆盖行为保持不变；要使用 XDM，既不能传该参数，也不能在 config 中写 `arxmlFileList`。

使用清单模式只需：

```json
{
    "arxmlFileList": "../cfg/${variant}.lst"
}
```

以上为输入模式相关字段片段，其他必填配置保留。`tresosPreferenceFiles` 可省略；同时写了两个字段也不是冲突，而是只使用清单，忽略 XDM。要切换到 XDM 模式，删除 `arxmlFileList` 并提供 `tresosPreferenceFiles`，不能将前者设为 `""` 或 `null` 来切换。

**复用模式的注意事项：** 只要选中了已有 lst，就完全忽略 `tresosPreferenceFiles`、`tresosOutputBaseDir` 和 `buildType`，不检查 XDM 路径或 EB 命令，即使这些配置无效也不影响复用。lst 内部必须是有效的绝对路径或相对 `projectRoot` 的路径；不再借助 XDM 推导基准，也不自动补齐 EB 构建目录中的 `tresos` 层。Python 转换依赖及同名 ARXML 排除仍然生效。

## General 与 AUTOSAR

| 参数                           | 规则     | 语义                                                                                                    |
| ------------------------------ | -------- | ------------------------------------------------------------------------------------------------------- |
| `Composition`                | 必填     | 目标`COMPOSITION-SW-COMPONENT-TYPE` 完整路径；包含包路径和类型短名                                    |
| `System`                     | 可选     | 目标`SYSTEM` 的短名或完整路径；未填时取根组合逐层包含 `Composition` 的唯一 System，0 个或多个时报错 |
| `EcuInstance`                | 可选     | `ECU-INSTANCE` 完整引用路径；未填时取该 System FIBEX-ELEMENTS 中唯一的 ECU-INSTANCE，否则报错         |
| `RootSwCompositionPrototype` | 条件可选 | 显式填写完整引用路径；未填时从输入模型中配置的 System 读取其根组合原型                                  |
| `systemMapping`              | 可选     | 映射命名值；未填或空值时取`<outputPrefix>SystemImpl`，例如 `BcmSystemImpl`                          |

只有能唯一确定时才自动推导，存在歧义时必须显式配置；目标 Composition 属于工程设计选择，始终必填。

单个 System 的 `ROOT-SOFTWARE-COMPOSITION` 为可选单值关系，可以在明确选定 System 后读取。工具按 `System` 短名或完整路径查找；不存在、没有根原型或存在多个同名 System 时失败。遇到同名歧义须配置完整路径或显式配置 `RootSwCompositionPrototype`。

显式根原型路径会直接传递，工具不会自动创建或完整校验它。应确保它以及 ECU、组件实现等引用能在后续整合的模型中正确解析；目标文件不是自包含的全工程 ARXML。

白表 General 页仅写 `Composition`、`System`、`RootSwCompositionPrototype`、`EcuInstance` 四项；`systemMapping` 单独传给生成器，不写入此页。生成器加上 `SystemMapping_` 前缀，所以默认示例对应的映射短名为 `SystemMapping_BcmSystemImpl`。

## 同名组件与别名

AUTOSAR 允许不同包下存在相同 SHORT-NAME 的组件类型，它们是独立对象，不应合并。工具按完整路径保留类型，短名只有唯一时可用。绿表文件名不能包含完整 AUTOSAR 路径，可用别名消歧：

```json
{
    "prototypePrefix": "its",
    "componentAliases": {
        "LampFront": "/Front/Swc/Lamp",
        "LampRear": "/Rear/Swc/Lamp",
        "ExistingDriver": "/ARDesign/SystemDesign/TopLevelComposition/itsDriver"
    }
}
```

对应绿表可以命名为 `LampFront.xlsx`、`LampRear.xlsx`，I 列也可以使用这些别名。没有目标 Composition 内的已有实例时，类型别名生成 `itsLampFront`、`itsLampRear`；有已有实例时复用其短名。一个类型有多个目标实例时，别名必须指向具体 prototype 路径。多个别名指向同一无实例类型时，第一个别名确定新实例名，其他别名为同义名，不创建多实例。

新实例名不想带 `prototypePrefix` 时，用对象形式显式指定实例名；Composition 类型（如 `Conti_SWC_FDLib`）不参与短名匹配，只能通过别名引用：

```json
"componentAliases": {
    "Conti_SWC_FDLib": {"type": "/ContiPackage/Conti_SWC_FDLib", "prototype": "Conti_SWC_FDLib"}
}
```

该类型在目标 Composition 中已有其他名称的实例时报错。

工具不借用其他 Composition 的 prototype。`Reference` 必须已存在于目标 Composition，且类型一致；仅有组件类型定义不等于已有实例。需要新增的实例应由本次绿表声明为 `Add`。新实例名称冲突时失败，不会按类型短名丢弃其中一个。

## 接口不一致策略

普通装配连接必须为 P-port 到 R-port。所有使用的端口必须引用已加载的 PortInterface，缺失接口、未知端口或错误端口方向始终报错，不能通过以下参数绕过。

| config 中的`interfaceMismatch` | 两端 interface 完整引用不一致时                                                     |
| -------------------------------- | ----------------------------------------------------------------------------------- |
| `error`                        | 默认；报错，整次失败，不发布任何本次产物                                            |
| `skip`                         | 记录 WARNING，包含白表位置及两端端口/接口路径，不生成这条装配连接，其他有效记录继续 |
| `map`                          | 创建接口映射并在装配连接上写入`MAPPING-REF`；无法确定有效映射时仍报错             |

`skip` 是用户显式选择的过滤行为，不是忽略 ERROR；它只处理接口不一致，不能跳过信号方向、信号组或其他错误。白表保留原始连接请求，因此 skip 模式下白表连接数可大于最终 ARXML 连接数。

当前 `map` 支持两端均为 SenderReceiverInterface 的情况：默认按数据元素短名配对，要求两侧非空、元素名集合一致，并解析到最终数据类型后检查一致性。它不按元素顺序或“只有一个元素”猜测对应关系，不进行数值/类型转换。元素名不同须显式指定：

```json
{
    "interfaceMismatch": "map",
    "interfaceMappings": [
        {
            "providerInterface": "/Interfaces/ProviderStatus",
            "requesterInterface": "/Interfaces/RequesterStatus",
            "dataElements": {
                "OutputValue": "InputValue"
            }
        }
    ]
}
```

`dataElements` 的键是 provider 元素短名，值是 requester 元素短名；必须完整覆盖两侧元素且一一对应。最终类型不一致、缺失定义、不完整配对或非 SenderReceiverInterface 的自动映射均报错，不降级为直接连接。ClientServer、ModeSwitch 等接口仍可在两端引用相同时正常连接，但当前不自动生成它们的跨接口映射。

### 最终数据类型检查

- 不通过类型短名猜测类型，也不只比较引用路径。沿 `TYPE_REFERENCE`、`IMPLEMENTATION-DATA-TYPE-REF`、`BASE-TYPE-REF` 解析到基础类型，要求 `BASE-TYPE-SIZE` 和 `BASE-TYPE-ENCODING` 一致；声明的 byte order 也参与比较。
- 例如 uint8/uint16 位宽不同、uint8/sint8 符号编码不同，均拒绝。相同位宽的整数和浮点也不能配对。不同类型路径但最终均为相同表示的 uint8，可以配对。
- 当前支持的标量编码为 `NONE`（无符号）、`2C`、`1C`、`SM`、`IEEE754`、`BOOLEAN`，编码须精确一致，不做符号或数值转换。缺失位宽、缺失/未知编码、错误 DEST、缺失或循环类型引用均报错，不因两端恰好引用同一路径而放行。
- ApplicationDataType 优先按各端组件 InternalBehavior 引用的 DataTypeMappingSet 解析；多个不同实现目标为歧义错误。没有组件级映射的 ApplicationPrimitiveDataType 仅在有明确 implementation/base type 属性时继续解析，应用数组/记录必须有组件级实现映射。
- 固定大小数组递归比较长度和元素最终类型；结构递归比较字段名称、顺序及最终类型。动态数组、可选成员、指针、位域和元素级类型覆盖目前无法可靠自动匹配，明确报错。
- **相同 SenderReceiverInterface 的装配连接也执行最终类型检查**：同一应用类型在两个组件中分别实现为 uint8 和 uint16 时仍会失败。`skip` 只是不生成接口不同的连接，不会放行已连接的数据类型错误。

此处保证已支持类型的最终表示和形状一致，不代替数据约束、CompuMethod、单位/缩放、服务语义或完整 RTE 兼容性验证。

映射写在目标 Composition 同包的 `<Composition短名>_InterfaceMappings` 下，为 `PORT-INTERFACE-MAPPING-SET` / `VARIABLE-AND-PARAMETER-INTERFACE-MAPPING`。同一元素配对集合复用一个稳定命名的映射，实际元素类型/接口仍由输入模型提供。相同完整路径的已有映射不会被静默覆盖。

## 输出与选项

- `outputPrefix=Bcm` 推导 `BcmWhiteTable.xlsx`、`BcmGreenTables.xlsx`、`BcmSystem.arxml`，与变体名称无关。
- 产物目录必须通过 `--output-dir` 或 `outputDir` 指定；日志目录必须通过 `--log-dir` 或 `logDir` 指定。命令行优先，脚本和安装命令都没有默认目录；缺失、空值或类型错误在创建日志前报错并退出 1。日志目录与产物目录不能相同。
- 多配置仍共用一组产物和日志目录。未传对应命令行参数时，每份配置必须提供对应目录，且相对各自配置目录解析后必须一致；有冲突时可通过命令行统一覆盖。
- 连接名由工具按 prototype 和端口固定生成（见 [white-table-format.md](white-table-format.md)）；输出 release 由 `arRelease` 配置，默认 `R19-11`。
- 转换及写出先在临时目录完成，全部成功后才发布 Excel、ARXML 及 EB 生成的 lst。任何 ERROR/异常均退出 1，不发布本次产物；原有产物保留不变，失败日志保留。发布时普通 I/O 异常会回滚已替换文件；断电、进程强制终止或回滚期间存储故障不属于可保证的事务范围。
- `outputPrefix` 不会展开到 `greenTables` 或 `general` 中。改变产物前缀不会自动改变 AUTOSAR 包路径、System 或 ECU。
- `buildType` 没有单独的命令行覆盖选项；EB 模式下需在配置中与实际构建目录保持一致。
