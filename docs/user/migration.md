# 版本升级与迁移

返回 [使用说明](README.md)。升级时安装新的 wheel，并使用同版本的文档和模板。
不要假定任意版本升级都无需修改配置；先在隔离目录运行并检查日志和生成差异。

## 0.3.0 → 0.3.1

命令名、JSON 键名、工作簿列名和正常样例的生成结果保持兼容。本版本修复了此前被错误接受的输入：

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

对外资料目录统一为 `system`、`swc`；旧文档入口和 `tableToSwcArxml/SwcTemplate.xlsx` 下载路径继续保留。
仓库回归样例移到 `test/samples/system`、`test/samples/swc`，共享模型移到 `test/data/common`；这些路径不属于 wheel 接口。

## 0.2.x → 0.3.0

- 命令 `table-to-system-arxml`、`table-to-swc-arxml` 不变。
- 模块调用改为 `python -m table_to_arxml.system`、`python -m table_to_arxml.swc`。
- System 配置错误退出码改为 `2`，普通日志改走 stdout；运行失败仍为 `1`。
- SWC 的 `backend` 已废弃，只接受 `armodel` 并给出警告，可从配置删除。
- 命令行选择 SWC 输出方式时整体替换 JSON 中的输出方式。
- System 委托连接名称改为 `dlc_<外端口>_<实例>_<内端口>`；连接器名称上限为 128 字符。
- 工作簿当前列名为 `Runnable Name`、`Create RTE Artifact`、`Access Kind`、`SW-C`，
  旧列名兼容关系见 [工作簿格式](swc/workbook-format.md)。

## 更早版本

旧的两个独立发行包已合并为 `table-to-arxml`，标签采用 `x.y.z`，不再按工具分别发布。
SWC 的 `project_dir`、`list_file`、`log_file` 等旧键已分别改为 `projectRoot`、`arxmlFileList`、`logDir`；
`workbook`、`logDir` 必须显式提供。System 的旧 `name`、`xdm`、`arxml`、`output`、`generatedComponents`
不再支持，不能直接沿用这些旧配置。
