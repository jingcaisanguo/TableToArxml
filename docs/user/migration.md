# 版本升级与迁移

返回 [使用说明](README.md)。升级时安装新的 wheel，并使用同版本的文档和模板。
不要假定任意版本升级都无需修改配置；先在隔离目录运行并检查日志和生成差异。

## 绿表页签命名调整

新建绿表请使用 `R-Port` 和 `P-Port` 页签，直接对应当前模块的接收端口和发送端口。
已有工作簿仍可使用完整的 `RECEIVE`/`SEND` 或 `INPUT`/`OUTPUT` 组合，无需批量改名。
同一工作簿必须完整使用其中一套名称，不能混用或同时保留多套。合并绿表产物统一将页签命名为 `<模块>_R-Port` 和 `<模块>_P-Port`。

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
