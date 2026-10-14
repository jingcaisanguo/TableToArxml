# 构建与发布

返回 [README.md](README.md)。两个工具合并为一个发行包 `table-to-arxml`，需要 Python 3.9 或更新版本；
安装后同时提供 `table-to-system-arxml` 和 `table-to-swc-arxml` 两个命令。安装与使用见 [../user/README.md](../user/README.md)。

## 本地构建

从仓库根目录执行：

```powershell
python -m pip install build
python -m build --wheel --outdir dist .
```

生成 `dist/table_to_arxml-<版本>-py3-none-any.whl`（`dist/` 不提交）。

- 打包配置在根目录 [pyproject.toml](../../pyproject.toml)，依赖从根目录 [requirements.txt](../../requirements.txt) 读取。
- wheel 只包含两个工具的 Python 包，不包含源码启动脚本、文档、测试数据、配置、日志或输出目录。
- 版本号只在 `pyproject.toml` 的 `project.version` 中定义，两个工具共用；不要用相同版本号发布不同内容。

## 发布

发布由 [release.yml](../../.github/workflows/release.yml) 完成，**标签名就是版本号**，格式为 `x.y.z`（三段纯数字），例如 `0.2.1`：

1. 修改 `pyproject.toml` 的 `version`，经 PR 合入 `main`。
2. 在 `main` 的对应提交上打标签并推送（或在 GitHub 网页创建同名标签）：

   ```powershell
   git checkout main
   git pull
   git tag 0.2.1
   git push origin 0.2.1
   ```

3. 工作流依次执行，任一步失败都不发布：
   - 检查标签是否为 `x.y.z`；
   - 构建 wheel，要求文件名为 `table_to_arxml-<标签>-py3-none-any.whl`，即标签与 `pyproject.toml` 版本完全一致（`0.02.0` 这类写法会因规范化后不一致而失败）；
   - 安装并执行 `pip check`、两个命令的 `--help`；
   - 上传到与标签同名的 GitHub Release，自动生成发布说明。

对已有 Release 重新推送同名标签会覆盖其中同名 wheel。仓库需启用 GitHub Actions，并允许工作流写入 Release。
也可以手动创建 Release 并上传本地构建的 wheel，不要求上传到 PyPI。

旧格式标签 `tableToSystemArxml/<版本>`、`tableToSwcArxml/<版本>` 不再触发发布。

## 更新 init_app_env

发布新版本后，同步内部工程的安装脚本：

1. 用新 wheel 替换 `init_app_env/whl` 下的 `table_to_arxml-<旧版本>-py3-none-any.whl`。
2. 修改 [init_app_env.py](../../init_app_env/init_app_env.py) 中 `table_to_arxml` 的版本和文件名。
