# 本工程说明

本目录面向本仓库的维护者，介绍仓库结构、源码、测试、CI 与发布。工具的使用说明在 [../user/README.md](../user/README.md)，
可单独提供给外部；涉及本仓库路径、测试数据、内部工程或历史版本的内容只写在本目录。

| 文档 | 内容 |
| --- | --- |
| 本文 | 仓库结构、开发环境、源码模块、测试与 CI、内部工程集成、版本历史 |
| [release.md](release.md) | 构建 wheel、按 `x.y.z` 标签发布、更新 `init_app_env` |

## 仓库结构

```text
pyproject.toml                  两个工具合并打包为一个 wheel（table-to-arxml）
requirements.txt                运行依赖，也是 wheel 的依赖声明
init_venv.sh                    在 Git Bash 中创建并激活 Python 3.9.6 的 .venv
init_app_env/                   内部工程安装工具用的脚本和固定版本 wheel
table_to_system_arxml/
    table_to_system_arxml.py    源码启动脚本，不打进 wheel
    table_to_system_arxml/      Python 包
table_to_swc_arxml/
    table_to_swc_arxml.py       源码启动脚本，不打进 wheel
    table_to_swc_arxml/         Python 包
docs/user/                      对外的使用说明
docs/dev/                       本工程说明（本目录）
test/                           命令测试数据、基线产物和端到端测试，见 test/README.md
.github/workflows/              CI：tests.yml、wheel.yml、release.yml
```

## 开发环境与源码运行

```bash
source ./init_venv.sh
python -m pip install -r requirements.txt
python table_to_system_arxml/table_to_system_arxml.py --help
python table_to_swc_arxml/table_to_swc_arxml.py --help
```

- 源码启动脚本与安装后的命令参数相同。区别只有一处：`table_to_system_arxml.py` 未传 `--config` 时读取脚本同目录的 `config.json`，安装后的命令读取当前目录的 `config.json`。
- 也可以 `python -m pip install -e .` 以可编辑方式安装，直接使用两个命令。
- 依赖只在根目录 [requirements.txt](../../requirements.txt) 中声明；两个工具运行时各自检查 armodel 版本，常量 `ARMODEL_REQUIREMENT` 须与该文件保持一致。

## 源码模块

### table_to_system_arxml

[cli.py](../../table_to_system_arxml/table_to_system_arxml/cli.py) 解析并校验配置、编排执行流程。`lib/` 下各模块：

| 模块 | 职责 |
| --- | --- |
| [common.py](../../table_to_system_arxml/table_to_system_arxml/lib/common.py) | 共用取值函数和默认值，不依赖第三方库 |
| [file_list.py](../../table_to_system_arxml/table_to_system_arxml/lib/file_list.py) | 生成和读取 EB 清单 |
| [arxml_model.py](../../table_to_system_arxml/table_to_system_arxml/lib/arxml_model.py) | 加载 ARXML，封装模型查询 |
| [green_table.py](../../table_to_system_arxml/table_to_system_arxml/lib/green_table.py) | 绿表读取、收集、合并 |
| [component_map.py](../../table_to_system_arxml/table_to_system_arxml/lib/component_map.py) | 绿表组件名到实例、类型的映射 |
| [general.py](../../table_to_system_arxml/table_to_system_arxml/lib/general.py) | 推导白表 General 页 |
| [green_to_white.py](../../table_to_system_arxml/table_to_system_arxml/lib/green_to_white.py) | 绿表转白表 |
| [white_table.py](../../table_to_system_arxml/table_to_system_arxml/lib/white_table.py) | 白表布局、创建、格式化和读取 |
| [type_signature.py](../../table_to_system_arxml/table_to_system_arxml/lib/type_signature.py) | 数据类型最终签名，用于接口两端类型比较 |
| [arxml_generator.py](../../table_to_system_arxml/table_to_system_arxml/lib/arxml_generator.py) | 解析白表引用并用 armodel 生成输出 |

- 多个配置共用一次 `ArxmlModel.load`；生成的 ARXML 写到独立文档，加载的模型保留给后续配置复用。
- 产物先写到临时目录，全部成功后才发布到输出目录，失败时回滚。
- 库代码使用 `logging.getLogger(__name__)`，只在 `cli.py` 配置 handler；运行期间非 armodel 的 ERROR 日志会被收集，发布前统一失败。

### table_to_swc_arxml

| 模块 | 职责 |
| --- | --- |
| [cli.py](../../table_to_swc_arxml/table_to_swc_arxml/cli.py) | 入口、日志和处理流程编排 |
| [options.py](../../table_to_swc_arxml/table_to_swc_arxml/options.py) | 合并命令行与 JSON 配置，得到冻结的 `Options` |
| [workbook.py](../../table_to_swc_arxml/table_to_swc_arxml/workbook.py) | 一次加载工作簿，逐表校验并转为待创建对象 |
| [resolve.py](../../table_to_swc_arxml/table_to_swc_arxml/resolve.py) | 读取清单，定位组件、接口、runnable，补全端口和访问点 |
| [apply.py](../../table_to_swc_arxml/table_to_swc_arxml/apply.py) | 生成片段、插入并重新解析验证 |
| [xml_patch.py](../../table_to_swc_arxml/table_to_swc_arxml/xml_patch.py) | 按 AUTOSAR 路径在原始字节中定位和插入 |
| [store.py](../../table_to_swc_arxml/table_to_swc_arxml/store.py) | 缓存待写出的字节和解析结果 |
| [writer.py](../../table_to_swc_arxml/table_to_swc_arxml/writer.py) | 原子写出 |
| [lib/arxml_backend.py](../../table_to_swc_arxml/table_to_swc_arxml/lib/arxml_backend.py) | 封装 `armodel` 的解析和片段生成 |

- 新元素只以 XML 片段插入原文件字节，其余字节不变，避免 armodel 整文件重新序列化带来的格式差异。
- armodel 的 `AUTOSAR` 是全局单例，每次加载都会清空；文档对象带代次号，过期使用会报错。`SourceStore` 按文件版本缓存解析结果。

#### 更换解析库

[lib/arxml_backend.py](../../table_to_swc_arxml/table_to_swc_arxml/lib/arxml_backend.py) 集中封装了 `armodel` 的全部用法，
其余模块只依赖与库无关的抽象：`PortInfo`、`AccessInfo` 描述端口和访问点，`ArxmlDocument` 提供查询和片段生成，
`ArxmlBackend` 负责加载文件并返回文档和解析诊断。

更换解析库时：在该模块中新增实现 `ArxmlDocument` 的文档类和提供 `name`、`load()` 的后端类，
注册到 `BACKENDS`，运行时用 `--backend <名称>` 选择；其余模块无需改动。

### 两个工具之间的重复代码

日志初始化、清单读取与 `excludeArxml`、原子写出、依赖检查在两个工具中各有一份。为避免多一个共享包依赖，
目前保留两份实现，修改其中一份时需同步另一份；后续可补契约测试保证两边行为一致。

## 测试与 CI

测试数据、基线产物和运行方式见 [../../test/README.md](../../test/README.md)。

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| [tests.yml](../../.github/workflows/tests.yml) | 代码、测试或依赖变更、PR、手动 | Ubuntu/Windows × armodel 1.8.7/1.9.2：ruff（F、B、E9）与 `pytest test` |
| [wheel.yml](../../.github/workflows/wheel.yml) | 代码或打包配置变更、PR、手动 | 构建 wheel，安装并检查两个命令，上传 artifact，不发布 |
| [release.yml](../../.github/workflows/release.yml) | 推送 `x.y.z` 标签 | 构建、校验版本、发布到 GitHub Release，见 [release.md](release.md) |

## 内部工程集成

- 内部工程通过 [../../init_app_env/init_app_env.py](../../init_app_env/init_app_env.py) 从 `init_app_env/whl` 安装固定版本的 armodel、py_eb_model 和本工具 wheel。
- 内部工程的 CMB 动作通过 profiles 配置 EB 工具环境，产物复制命令目前为注释状态。启用复制时，修改 `outputPrefix` 或 `--output-dir` 后须同步源路径，并仅在退出码为 0 时复制。
- `table-to-system-arxml` 的 EB 模式与内部工程生成 Autosar Builder 工程的脚本使用相同的 `PrefSystemImporter --file-list` 调用方式。

## 版本历史与迁移

- **合并为单个 wheel**：0.2.0 之前两个工具分别发布 `table-to-system-arxml`、`table-to-swc-arxml` 两个包，标签为 `tableToSystemArxml/<版本>`、`tableToSwcArxml/<版本>`；现在合并为 `table-to-arxml`，标签直接是 `x.y.z`。`init_app_env.py` 会先卸载旧的两个包。
- **System 工具**：
  - 不再依赖或导入 `pya4a`，也不启动 `xlsx-composition-sw`；白表格式和有效输入的输出约定保持兼容。其他工程工具可能仍需要 `pya4a` 等库，本工具不会卸载它们。
  - 旧配置字段 `name`、`xdm`、`arxml`、`output`、`generatedComponents` 已不用，不提供自动迁移。
  - `projectRoot` 改为必填（原默认值 `../../..` 对应旧仓库布局）；白表去掉了未使用的 `Connection` 列，旧白表中的该列被忽略。
- **SWC 工具**：参数已与 System 工具统一。

  | 旧写法 | 新写法 |
  | --- | --- |
  | `default_variant` | `defaultVariant` |
  | `project_dir` / `--project-dir` | `projectRoot` / `--project-root` |
  | `list_file` / `--list-file` | `arxmlFileList` / `--arxml-file-list` |
  | `log_file` / `--log-file`（日志文件） | `logDir` / `--log-dir`（日志目录） |
  | `log_level`、`strict_list`、`in_place`、`output_dir`、`dry_run` | `logLevel`、`strictList`、`inPlace`、`outputDir`、`dryRun` |

  `workbook` 和 `logDir` 不再有默认值，必须显式提供。0.2.0 起命令行相对路径按当前工作目录解析（此前按 `projectRoot`），相对 `--config` 也不再需要 `--project-root`。
