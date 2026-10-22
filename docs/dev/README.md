# 本工程说明

本目录面向本仓库的维护者，介绍仓库结构、源码、测试、CI 与发布。工具的使用说明在 [../user/README.md](../user/README.md)，
可单独提供给外部；涉及本仓库路径、测试数据、内部工程或历史版本的内容只写在本目录。

| 文档 | 内容 |
| --- | --- |
| 本文 | 仓库结构、开发环境、源码模块、测试与 CI、内部工程集成 |
| [release.md](release.md) | 构建 wheel、按 `x.y.z` 标签发布 |

## 分支与提交流程

- 开发和 PR 分支统一使用 `TTA-数字`，匹配 `^TTA-[0-9]+$`，例如 `TTA-12`；不使用描述性英文分支名。
- 创建分支前先获取远程分支信息，为新任务选取未使用的编号。
- 修改提交并推送到对应任务分支，通过 PR 合入 `main`；不直接向 `main` 提交或推送，不绕过分支保护。
- AI 协作同样遵守上述流程，仓库级指引见 [AGENTS.md](../../AGENTS.md)。

## 仓库结构

```text
pyproject.toml                  打包（table-to-arxml）、pytest 与 ruff 配置
requirements.txt                运行依赖，也是 wheel 的依赖声明
init_venv.sh                    在 Bash 中创建并激活 Python >=3.9 的 .venv
src/table_to_arxml/             唯一的 Python 包，打进 wheel
    common/                     两个工具共用的基础设施
    system/                     table-to-system-arxml
    swc/                        table-to-swc-arxml
tools/                          模板生成、wheel 实测和用户文档打包，不打进 wheel
third_party/                    移植时按需选用的第三方脚本，不归本工程维护，见 third_party/README.md
docs/user/                      对外的使用说明（含 SWC 空白模板）
docs/dev/                       本工程说明（本目录）
test/                           测试代码、测试数据和基线产物，见 test/README.md
.github/workflows/              CI：tests.yml、wheel.yml、release.yml
```

## 开发环境与源码运行

```bash
source ./init_venv.sh
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"     # 运行依赖、开发工具及两个命令统一安装
table-to-system-arxml --help
table-to-swc-arxml --help
```

- 新环境优先 Python 3.13，可在创建环境前设置 `TTA_PYTHON` 指定解释器。已有 `.venv` 只要满足最低版本就直接复用，不自动重建；
  若需要更换解释器，先自行备份并处理旧环境。
- 不安装时可设置 `PYTHONPATH=src`，再用 `python -m table_to_arxml.system`、`python -m table_to_arxml.swc` 运行，参数与命令完全相同。
- 运行依赖只在根目录 [requirements.txt](../../requirements.txt) 中声明，开发依赖在 [pyproject.toml](../../pyproject.toml) 的 `dev` extra 中声明。armodel 的版本范围在代码中只有一处
  （[dependencies.py](../../src/table_to_arxml/common/dependencies.py) 的 `ARMODEL_REQUIREMENT`），测试保证它与 requirements.txt 一致。`tomli` 只在 Python 3.11 以下需要（3.11 起用标准库 `tomllib`），由环境标记控制。
- 版本号由 `setuptools-scm` 从 Git tag 自动推导；源码运行需要开发依赖 `setuptools-scm`，安装后的 wheel 使用构建时生成的版本文件，不需要 Git 或 `setuptools-scm`。不要手改版本常量。
- 源码版本检测同时检查 `src/table_to_arxml` 布局和 Git 标记；安装包即使位于某个 Git 工作区内，也不会误用该工作区的版本。

## 源码模块

### common：共用基础设施

| 模块 | 职责 |
| --- | --- |
| [errors.py](../../src/table_to_arxml/common/errors.py) | `ConfigError`：命令行或配置错误，入口以退出码 2 报告 |
| [config_file.py](../../src/table_to_arxml/common/config_file.py) | 读取 TOML 配置文件（其他扩展名兼容旧版本按 JSON 读取），返回顶层表；字段校验由各工具负责 |
| [messages.py](../../src/table_to_arxml/common/messages.py) | 去除 ANSI 控制码、合并为单行的消息文本 |
| [dependencies.py](../../src/table_to_arxml/common/dependencies.py) | 依赖可导入性与 armodel 版本检查 |
| [logs.py](../../src/table_to_arxml/common/logs.py) | 日志文件与控制台输出、ERROR 收集 |
| [arxml_list.py](../../src/table_to_arxml/common/arxml_list.py) | ARXML 清单读取和 `excludeArxml`/正则排除，两个工具同一套规则 |
| [output.py](../../src/table_to_arxml/common/output.py) | 单文件原子替换；批量失败时尽力回滚并汇总恢复失败路径 |
| [paths.py](../../src/table_to_arxml/common/paths.py) | 字面路径优先的 glob 展开、文件身份与输出冲突检查 |
| [names.py](../../src/table_to_arxml/common/names.py) | SHORT-NAME、AUTOSAR 路径、C 标识符校验及稳定长名称 |
| [armodel_compat.py](../../src/table_to_arxml/common/armodel_compat.py) | armodel 的用法约定与缺陷绕行：全局文档复位、解析器日志接管、片段序列化、writer 修补 |
| [openpyxl_compat.py](../../src/table_to_arxml/common/openpyxl_compat.py) | 跨工作簿复制页签（依赖 openpyxl 私有属性） |

两个工具的入口行为一致：

- 退出码：`0` 成功；`1` 运行失败（输入错误、校验或生成失败），不写任何产物；`2` 命令行或配置错误，打印用法、不创建日志。
- 日志同时写文件和控制台，INFO/DEBUG 到 stdout，WARNING 及以上到 stderr；handler 同时挂在本包 logger 和 root logger 上，
  后者用于收集 armodel writer 直接写 root 的日志。从 armodel 解析器转发的诊断照常输出，但不计入失败。
- 先完成参数解析、创建日志，再检查依赖，最后才导入依赖 armodel 的模块；因此依赖缺失或版本不符时 `--help` 仍可用，
  且报告的是明确的版本错误而不是导入异常。非预期异常记录完整堆栈。
- `main(argv=None)` 可直接传入参数，便于测试。

### system：table-to-system-arxml

| 模块 | 职责 |
| --- | --- |
| [cli.py](../../src/table_to_arxml/system/cli.py) | 执行流程编排、日志、错误处理与产物发布 |
| [options.py](../../src/table_to_arxml/system/options.py) | `RunOptions`、`Job`、CLI 与配置文件合并、模式选择和生效配置校验 |
| [values.py](../../src/table_to_arxml/system/values.py) | 取值函数和默认值，不依赖第三方库 |
| [tresos_list.py](../../src/table_to_arxml/system/tresos_list.py) | 调用 EB 导入器生成清单，按 tresos 工程约定读取 |
| [arxml_model.py](../../src/table_to_arxml/system/arxml_model.py) | 加载 ARXML，查询组件、System、组合实例链、CAN/LIN 通道与信号 |
| [green_table.py](../../src/table_to_arxml/system/green_table.py) | 绿表读取、收集、合并 |
| [component_map.py](../../src/table_to_arxml/system/component_map.py) | 绿表组件名到实例、类型的映射 |
| [general.py](../../src/table_to_arxml/system/general.py) | 推导白表 General 页 |
| [green_to_white.py](../../src/table_to_arxml/system/green_to_white.py) | 绿表转白表 |
| [white_table.py](../../src/table_to_arxml/system/white_table.py) | 白表布局、创建、格式化和读取 |
| [arxml_generator.py](../../src/table_to_arxml/system/arxml_generator.py) | 解析白表引用并用 armodel 生成输出；连接器命名见 `connector_name` |

- 多个配置共用一次 `ArxmlModel.load`；生成的 ARXML 写到独立文档，加载的模型保留给后续配置复用。
- 产物先写到临时目录，没有工具自身 ERROR 或运行异常时才发布到输出目录；armodel 解析器诊断不单独决定失败。
- 组合实例链只在 `ArxmlModel.composition_chains` 中实现，嵌套上限为 `MAX_COMPOSITION_DEPTH`；总线类型统一由 `bus_module` 判断。
- 输出是对已有元素的拆分片段：`_Resolver.check_composition_names` 检查目标 Composition 命名空间（同名报错、仅大小写不同只警告），`resolve_system`
  按 AUTOSAR 元模型的多重性和 `atpSplitable` 决定 FIBEX-ELEMENTS、SystemMapping 等是否写出（依据 4.3.1 XSD 的 `pureMM`
  多重性与 `atpSplitable` 标注）。新增写出内容前先确认对应角色是否可拆分，不可拆分且输入已有内容时不要追加。
- 校验原则：两个工具都不区分或比对 AUTOSAR 版本，只用各版本通用的规则（如 SHORT-NAME 字符集与 128 字符上限，R4.0.3 至 R24-11
  一致）。只有会导致生成结果错误或引用无法解析的问题报 ERROR；规范细则（大小写唯一、访问类别约束等）只记 WARNING，交给 EB 等工具链校验。

### swc：table-to-swc-arxml

| 模块 | 职责 |
| --- | --- |
| [cli.py](../../src/table_to_arxml/swc/cli.py) | 入口、日志、依赖检查与写出 |
| [options.py](../../src/table_to_arxml/swc/options.py) | 合并命令行与配置文件，得到冻结的 `Options` |
| [pipeline.py](../../src/table_to_arxml/swc/pipeline.py) | 处理步骤编排与 `RunSummary` 汇总 |
| [constants.py](../../src/table_to_arxml/swc/constants.py) | 与解析库无关的取值：端口方向、接口 DEST、访问点类别和容器 |
| [schema.py](../../src/table_to_arxml/swc/schema.py) | 解析器与模板共用的工作表、必填/可选表头和枚举取值 |
| [workbook.py](../../src/table_to_arxml/swc/workbook.py) | 一次加载工作簿，逐表校验并转为待创建对象 |
| [resolve.py](../../src/table_to_arxml/swc/resolve.py) | 定位组件、接口、runnable，补全端口和访问点 |
| [apply.py](../../src/table_to_arxml/swc/apply.py) | 生成片段、插入并重新解析验证；各步骤返回 `StepResult` |
| [xml_patch.py](../../src/table_to_arxml/swc/xml_patch.py) | 按 AUTOSAR 路径在原始字节中定位和插入；新建容器按 `CHILD_ORDER` 的 schema 顺序放置 |
| [store.py](../../src/table_to_arxml/swc/store.py) | 缓存待写出的字节和解析结果，映射输出路径并写出 |
| [arxml_model.py](../../src/table_to_arxml/swc/arxml_model.py) | armodel 的加载、查询和片段生成 |

- 新元素只以 XML 片段插入原文件字节，其余字节不变，避免 armodel 整文件重新序列化带来的格式差异。
- armodel 的 `AUTOSAR` 是全局单例，每次加载都会清空；文档对象带加载代次，过期使用会报错。`SourceStore` 按文件版本缓存解析结果。
- 定义查询按清单顺序采用第一个完整路径，缓存键包含查询范围和顺序。SHORT-NAME 预索引由 XML 解析构建，不依赖标签排版；XML 损坏仍阻止输出，包括后续重复候选文件损坏的情况。
- `StepResult.planned`、`FileChange.planned_ports/planned_accesses` 及汇总只描述内存中准备的新增对象；dry-run 不表示已写盘。实际写出成功后另行记录文件数量，端口和访问点的失败数量分别统计。
- 需要新建的父容器（`ELEMENTS`、`EVENTS`、`RUNNABLES`、`SERVER-CALL-POINTS`）按 [xml_patch.py](../../src/table_to_arxml/swc/xml_patch.py)
  中 `CHILD_ORDER` 的顺序插入。新增可创建的容器时须同时补充该表，否则会明确报错而不是插到错误位置。

### armodel 与第三方库

armodel 是两个工具的直接依赖，代码中不再为它做可替换的抽象层。依赖 armodel/openpyxl 内部行为的代码集中在
`common/armodel_compat.py` 和 `common/openpyxl_compat.py`，由 [test/contract](../../test/contract) 中的契约测试覆盖；
armodel 1.9.4 和 1.9.8 先后改名或移动了部分类和方法（如 `ARBoolean` → `Boolean`、`setEnableUpdated` → `setEnableUpdate`），
1.9.8 还删除了 `SenderReceiverToSignalMapping` 的 `COMMUNICATION-DIRECTION`；物理约束上下限的读写兼容旧版属性和新版 getter/setter。
`armodel_compat.py` 按实际版本选择，
其余代码只从这里导入受影响的类和方法，生成结果在各版本下一致。
CI 对 armodel 1.8.7、1.9.2、1.9.8 运行这些测试；依赖上限为 `1.9.8`。armodel 新版本发布后，先补充契约与回归测试并加入 CI 矩阵；
全部验证通过后，在同一提交中同步更新 `requirements.txt`、`dependencies.py` 的版本上限及相关文档，再发布新版 wheel。
若“缺陷仍存在”类断言失败，应调查库行为变化，不能直接认定绕行代码可以删除。

## 测试与 CI

测试结构、数据和运行方式见 [../../test/README.md](../../test/README.md)。pytest 和 ruff 的选项在 [pyproject.toml](../../pyproject.toml) 中配置，
本地与 CI 都只需执行 `python -m ruff check` 和 `python -m pytest`。

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| [tests.yml](../../.github/workflows/tests.yml) | 源码、文档、测试、工具脚本、模板或依赖变更、PR、手动及发布调用 | Ubuntu/Windows × Python 3.9/3.13 × armodel 1.8.7/1.9.2/1.9.8：ruff、全部测试和环境脚本语法检查 |
| [wheel.yml](../../.github/workflows/wheel.yml) | 源码、打包、交付文档或样例变更、PR、手动 | 构建安装 wheel，在仓库外检查命令、依赖、配置错误退出码及 BCM/MC/SWC 基线，打包用户说明并上传 artifact |
| [release.yml](../../.github/workflows/release.yml) | 推送 `x.y.z` 标签、公开发布 Release 或手动补齐附件 | 构建、校验版本、创建 Release 或补齐缺失附件，保留已有发布信息，见 [release.md](release.md) |

## 内部工程集成

- 内部工程的 CMB 动作通过 profiles 配置 EB 工具环境，产物复制命令目前为注释状态。启用复制时，修改 `outputPrefix` 或 `--output-dir` 后须同步源路径，并仅在退出码为 0 时复制。
- [third_party/](../../third_party) 下的 `init_app_env`（安装固定版本 wheel）和 `autosar_builder/gen_ab_project`（生成 Autosar Builder 工程和清单）
  是移植时按需选用的第三方脚本，不在本工程的测试、lint、CI 和发布范围内，由使用它们的工程自行维护，说明见 [third_party/README.md](../../third_party/README.md)。

## 版本与迁移

当前版本由 Git tag 自动推导，不在文档中重复维护固定版本号。对外行为变化和升级要求统一维护在 [迁移说明](../user/migration.md)，
本页不重复维护历史参数表或兼容入口。

用户说明只使用 `docs/user/system`、`docs/user/swc`；样例使用 `test/samples/system`、`test/samples/swc`，
共享模型位于 `test/data/common`。不保留旧目录、模板副本或废弃输入别名。
