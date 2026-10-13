# Wheel 构建与 GitHub 分发

发行包名为 `table-to-system-arxml`，当前版本为 `0.1.0`，需要 Python 3.9 或更新版本。
安装后提供 `table-to-system-arxml` 命令，也支持 `python -m table_to_system_arxml`。

## 本地构建

从仓库根目录执行：

```powershell
python -m pip install build
python -m build --wheel --outdir ./table_to_system_arxml ./table_to_system_arxml
```

生成 `table_to_system_arxml/table_to_system_arxml-0.1.0-py3-none-any.whl`，与入口脚本放在同一目录。
只打包 Python 实现与发行元数据，不包含工程 JSON、Excel、ARXML、日志或输出目录。
项目根目录的启动脚本和 wheel 同目录；内部实现位于 `table_to_system_arxml/` Python 包中。
根目录脚本仅负责启动，不打进 wheel；安装命令与模块入口都调用 `table_to_system_arxml.cli:main`。
发布新版本前修改 `pyproject.toml` 的 `project.version`，不要用相同版本号发布不同内容。

## GitHub 自动构建与发布

仓库根目录的 `.github/workflows/table-to-system-arxml-wheel.yml` 在工具代码变更、Pull Request 或手动触发时构建并检查 wheel。
可在 Actions 的构建结果中下载 `table-to-system-arxml-wheel` artifact；artifact 是 ZIP，需要解压后安装其中的 wheel。
如果将工具目录作为单独仓库上传，需要相应调整 workflow 中的构建路径、路径过滤及工作流文件位置。

推送与包版本一致的标签时，工作流会将 wheel 上传到 GitHub Release。例如在提交代码后执行：

```powershell
git tag table-to-system-arxml-v0.1.0
git push origin table-to-system-arxml-v0.1.0
```

标签版本与 wheel 不一致会拒绝发布。仓库需启用 GitHub Actions，并允许工作流写入 Release。
也可以在 GitHub 网页上手动创建 Release 并上传本地构建的 wheel，不要求上传到 PyPI。

## 其他项目安装与调用

建议在项目虚拟环境中安装。下载 Release 的 wheel 后执行：

```powershell
python -m pip install ./table_to_system_arxml-0.1.0-py3-none-any.whl
table-to-system-arxml --help
table-to-system-arxml --config ./config/system-arxml.json --output-dir ./generated --log-dir ./logs
```

公开仓库也可直接按固定版本的 Release URL 安装，替换 `OWNER` 和 `REPO`：

```powershell
python -m pip install "https://github.com/OWNER/REPO/releases/download/table-to-system-arxml-v0.1.0/table_to_system_arxml-0.1.0-py3-none-any.whl"
```

在其他项目的 `requirements.txt` 中可固定同一版本：

```text
table-to-system-arxml @ https://github.com/OWNER/REPO/releases/download/table-to-system-arxml-v0.1.0/table_to_system_arxml-0.1.0-py3-none-any.whl
```

私有仓库先通过已认证的 GitHub 客户端或 CI 下载 wheel，再使用本地路径安装。不要将令牌写入 URL、配置或版本库。
GitHub Enterprise 使用对应实例域名；下载方式和 Actions 可用版本以实例策略为准。

安装会自动安装 Python 依赖，不需要另外执行 `pip install -r requirements.txt`。
`armodel` 固定为 `1.9.2`：独立安装验证发现 `1.9.8` 缺少工具依赖的 `ISignalPort` 导出，不能直接使用。
wheel 本身跨平台，但依赖需要适合目标平台的发行包；离线使用需提前下载依赖。
EB 模式仍需工程提供 `PrefSystemImporter` 和对应运行环境，wheel 不包含这些外部工具。

## 路径与兼容性

| 调用方式 | 默认配置 | 产物和日志目录 |
| --- | --- | --- |
| `table-to-system-arxml` | 当前工作目录的 `config.json` | 必须通过参数或配置指定，无默认值 |
| `python -m table_to_system_arxml` | 当前工作目录的 `config.json` | 必须通过参数或配置指定，无默认值 |
| `python ./table_to_system_arxml/table_to_system_arxml.py` | 工具根目录的 `config.json` | 必须通过参数或配置指定，无默认值 |

例如配置中显式填写以下字段，或传入 `--output-dir ./output --log-dir ./log`：

```json
{
	"outputDir": "output",
	"logDir": "log"
}
```

此片段需合并到完整配置中。缺少任一目录时，在创建日志或转换前报错并退出 1。
多配置时，未被命令行覆盖的目录字段必须在每份配置中提供，解析后必须指向同一目录。

工程需自行准备配置与输入，格式见 [configuration.md](configuration.md)。
JSON 内的相对路径始终相对 JSON 所在目录；命令行相对路径始终相对当前工作目录。
安装后的命令不会向 `site-packages` 写配置、产物或日志。
退出码保持 `0` 成功、`1` 转换失败；参数错误由 `argparse` 返回 `2`。
若命令不在 `PATH`，使用同一虚拟环境的 `python -m table_to_system_arxml`。