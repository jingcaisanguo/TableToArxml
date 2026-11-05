# 第三方脚本

本目录存放移植到其他工程时**按需选用**的辅助脚本。它们不属于 table-to-arxml 工具本身：

- 不打进 wheel，工具运行时也不导入或调用它们；
- 不在本工程的测试、lint、CI 和版本发布范围内，由使用它们的工程自行维护；
- 是否使用、如何调整，按目标工程的需要决定。

| 目录 | 用途 |
| --- | --- |
| [init_app_env/](init_app_env) | 从 `whl/` 安装固定版本的 armodel、py_eb_model 和 table-to-arxml wheel；只需在 `init_app_env.py` 的 `PIP_PACKAGES` 中按安装顺序维护 wheel 文件名，包名和版本自动从包内 `METADATA` 读取 |
| [autosar_builder/gen_ab_project/](autosar_builder/gen_ab_project) | 调用 EB `PrefSystemImporter` 生成 Autosar Builder 工程和 ARXML 清单 |

`gen_ab_project.py` 与 `table-to-system-arxml` 的 EB 模式使用相同的 `PrefSystemImporter --file-list` 调用方式和默认排除规则，
因此两者生成的清单同源；两边修改时不会自动同步。

`init_app_env.py` 读取 wheel 元数据后，同版本已安装的包仍会跳过。所有配置的 wheel 都必须在指定目录中，即使对应包已安装；
wheel 缺失、损坏或缺少包名/版本元数据时会记录错误并以退出码 1 结束。
