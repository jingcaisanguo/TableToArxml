# 构建与发布

返回 [README.md](README.md)。两个工具合并为一个发行包 `table-to-arxml`，需要 Python 3.9 或更新版本；
安装后同时提供 `table-to-system-arxml` 和 `table-to-swc-arxml` 两个命令。安装与使用见 [../user/README.md](../user/README.md)。

## 本地构建

从仓库根目录执行：

```powershell
python -m pip install build
python -m build --wheel --outdir dist .
python -m pip install --force-reinstall --no-deps ./dist/table_to_arxml-0.4.0-py3-none-any.whl
python tools/verify_wheel.py
python tools/package_user_docs.py
```

生成 `dist/table_to_arxml-<版本>-py3-none-any.whl` 和 `dist/table_to_arxml-<版本>-user-guide.zip`（`dist/` 不提交）。
上面的安装检查应在专用虚拟环境执行，先安装运行依赖，不要覆盖正在使用的工程环境。
构建前确保 `dist/` 中没有其他版本 wheel；文档打包器要求其中只有一份 wheel，且版本与源码一致。

- 打包配置在根目录 [pyproject.toml](../../pyproject.toml)，依赖从根目录 [requirements.txt](../../requirements.txt) 读取。
- wheel 只包含 `src/table_to_arxml` 包，不包含维护脚本、文档、测试数据、配置、日志或输出目录。
- 版本号只在 [src/table_to_arxml/__init__.py](../../src/table_to_arxml/__init__.py) 的 `__version__` 中定义（`pyproject.toml` 动态读取），两个工具共用；不要用相同版本号发布不同内容。

## 发布

发布由 [release.yml](../../.github/workflows/release.yml) 完成，**标签名就是版本号**，格式为 `x.y.z`（三段纯数字），例如 `0.4.0`：

1. 修改 `src/table_to_arxml/__init__.py` 的 `__version__`，经 PR 合入 `main`。
2. 在 `main` 的对应提交上打标签并推送。不要提前创建同名 Release，由工作流创建和发布：

   ```powershell
   git checkout main
   git pull --ff-only
   git tag 0.4.0
   git push origin 0.4.0
   ```

3. 工作流依次执行，任一步失败都不发布：
   - 检查标签是否为 `x.y.z`，且提交位于 `main` 历史中；
   - 调用测试工作流，在 Windows/Ubuntu、Python 3.9/3.13、armodel 1.8.7/1.9.2/1.9.8 的矩阵上运行 lint 和全部测试；
   - 构建 wheel，要求文件名为 `table_to_arxml-<标签>-py3-none-any.whl`，即标签与 `__version__` 完全一致（`0.02.0` 这类写法会因规范化后不一致而失败）；
   - 安装并执行 `pip check`，在仓库外运行命令入口、依赖检查、配置错误退出码及 BCM/MC/SWC 三组真实转换基线；
   - 打包同版本用户说明与模板，先创建 Release 草稿并上传两个附件，再公开发布，自动生成发布说明。

同名 Release 已存在时工作流失败，不覆盖 wheel 或用户说明，不使用 `--clobber`；请发布新版本。
如果上传失败留下草稿，维护者应先调查失败原因，再决定清理草稿或发布新版本，不能覆盖已公开产物。
仓库需启用 GitHub Actions，并允许工作流写入 Release；同一标签的发布串行执行，不要求上传到 PyPI。

用户说明压缩包的根目录是使用说明入口，包含 `system/`、`swc/` 和迁移说明；只有一份当前 SWC 模板，不包含旧链接跳转页。
不包含开发文档、测试数据、工具源码或 `third_party` 内容；wheel 仍只打包运行代码和发行元数据。

## 第三方安装脚本

[third_party/init_app_env](../../third_party/init_app_env) 不归本工程维护，发布流程不会更新它。
使用该脚本的工程如需新版本，自行替换其 `whl/` 中的 wheel，并修改 `init_app_env.py` 中 `PIP_PACKAGES` 的版本和文件名。
