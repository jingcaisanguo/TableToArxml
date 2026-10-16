# TableToArxml

将 Excel 表格转换为 AUTOSAR ARXML 的命令行工具集，两个命令随同一个 `table-to-arxml` wheel 安装：

- `table-to-system-arxml`：绿表或白表生成 Composition、System 及映射。
- `table-to-swc-arxml`：向已有 ARXML 增量添加端口、接口、类型和 RTE 访问对象。

## 文档导航

| 使用场景 | 入口 |
| --- | --- |
| 安装、调用与对外交付 | [使用说明](docs/user/README.md) |
| 版本升级与兼容性变化 | [迁移说明](docs/user/migration.md) |
| System 表格与配置 | [System 工具](docs/user/system/README.md) |
| SWC 工作簿与配置 | [SWC 工具](docs/user/swc/README.md)、[空白模板](docs/user/swc/SwcTemplate.xlsx) |
| 源码结构与开发环境 | [开发说明](docs/dev/README.md) |
| 测试和样例 | [测试说明](test/README.md) |
| 构建与版本发布 | [发布流程](docs/dev/release.md) |

运行需要 Python 3.9 或更新版本；新环境建议选择仍受维护的 Python 3.13。
这只是最低安装要求，不表示每个 Python、armodel 和工程组合均已验证，具体矩阵见开发说明。

PR 分支使用 `TTA-数字` 命名，通过 PR 合入 `main`；不直接推送主分支。
[third_party](third_party/README.md) 中的移植脚本按需选用，不属于工具的测试、CI 或发布范围。
