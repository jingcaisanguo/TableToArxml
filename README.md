# TableToArxml

TableToArxml 提供两个 Python 命令行工具，把 Excel 表格中的 AUTOSAR 配置转换为 ARXML：

- **System 工具**：由绿表生成白表和 Composition/System ARXML，也支持直接从白表生成 ARXML；可选择以同名 base 文件为基础增量添加 SWC 实例、连线和信号映射。
- **SWC 工具**：根据工作簿向已有 ARXML 增量添加组件端口、接口、数据类型和 RTE 访问对象。

两个工具随同一个 `table-to-arxml` wheel 安装，适用于 Python 3.9 及以上版本。

## 安装与快速开始

从 GitHub Release 下载 wheel，在项目自己的 Python 环境中安装：

```powershell
python -m pip install ./table_to_arxml-<版本>-py3-none-any.whl
```

安装后可使用下面两个命令查看参数：

```powershell
table-to-system-arxml --help
table-to-swc-arxml --help
```

开始转换时传入 TOML 配置文件：

```powershell
table-to-system-arxml --config ./config/system.toml
table-to-swc-arxml --config ./config/swc.toml
```

转换输入、配置和输出方式因工具而异；请先阅读对应指南，并使用与 wheel 同版本的文档和模板。

## 命令与 Python 模块

安装的 shell 命令和 Python 模块调用是两种不同的入口：

| 用法 | 示例 | 说明 |
| --- | --- | --- |
| 安装命令 | `table-to-system-arxml`、`table-to-swc-arxml` | pip 根据打包配置创建的命令行入口 |
| Python 模块 | `python -m table_to_arxml.system`、`python -m table_to_arxml.swc` | 通过当前 Python 解释器运行已安装模块；参数与对应安装命令相同 |

`table_to_arxml.system` 和 `table_to_arxml.swc` 是 Python 模块路径，**不是可以直接在终端输入的命令名**。不能直接运行 `table_to_arxml.system`；需要写成 `python -m table_to_arxml.system`。如果使用虚拟环境，请使用同一环境中的 `python`。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [完整使用说明](docs/user/README.md) | 安装、配置格式、调用方式和通用规则 |
| [System 工具](docs/user/system/README.md) | System 转换流程、配置、输入和输出 |
| [绿表格式与转换规则](docs/user/system/green-table-rules.md) | `R-Port` / `P-Port` 绿表页、列定义、连接方向和转换示例 |
| [白表格式](docs/user/system/white-table-format.md) | 白表工作表、字段及生成语义 |
| [SWC 工具](docs/user/swc/README.md) | SWC 转换流程、校验和输出规则 |
| [SWC 工作簿格式](docs/user/swc/workbook-format.md) | SWC 工作簿各页和字段定义 |
| [SWC 空白模板](docs/user/swc/SwcTemplate.xlsx) | 可填写的工作簿模板 |
| [迁移说明](docs/user/migration.md) | 版本升级和输入兼容性变化 |
| [开发说明](docs/dev/README.md) | 开发环境、模块结构和 CI |
| [测试说明](test/README.md) | 测试结构、样例及运行方法 |
| [构建与发布](docs/dev/release.md) | wheel 构建和版本发布流程 |

## 支持与兼容

Python 3.9 是最低要求；新环境建议使用仍受维护的 Python 3.13。最低版本不表示所有 Python、armodel 和工程组合均已验证。CI 当前覆盖 Windows/Ubuntu、Python 3.9/3.13，以及 armodel 1.8.7、1.9.2 和 1.9.8。

armodel 依赖范围为 `>=1.8.7,<=1.9.8,!=1.9.1`。上限只会在新版本完成兼容测试后更新。安装 wheel 时 pip 会自动安装兼容依赖。

新绿表使用 `R-Port` 和 `P-Port` 页签；旧的 `INPUT` / `OUTPUT` 组合仍兼容。`RECEIVE` / `SEND` 不属于支持的兼容页名。详见[绿表规则](docs/user/system/green-table-rules.md)。

## 开发

从源码运行、测试和构建 wheel 的说明见[开发文档](docs/dev/README.md)。提交及 PR 分支遵循 `TTA-数字` 命名，通过 PR 合入 `main`，不直接推送主分支。

[third_party](third_party/README.md) 中的移植脚本为按需选用内容，不属于本工具的测试、CI 或发布范围。
