# table-to-swc-arxml 命令测试

返回 [../README.md](../../README.md)。配置为 [TableToSwcArxml.toml](TableToSwcArxml.toml)，工作簿为 [SwcTestPorts.xlsx](SwcTestPorts.xlsx)，
使用共享清单 [arxml.lst](../../data/common/arxml.lst)。工作簿格式见 [workbook-format.md](../../../docs/user/swc/workbook-format.md)。

## 空白模板与格式维护

实际项目请使用不含样例行的 [SwcTemplate.xlsx](../../../docs/user/swc/SwcTemplate.xlsx)。
本目录的 [SwcTestPorts.xlsx](SwcTestPorts.xlsx) 保留 LIT/Horn 数据，用于比较生成结果。
两者的列定义、README、批注、下拉验证和样式由 [tools/generate_swc_workbooks.py](../../../tools/generate_swc_workbooks.py) 统一生成：

```powershell
python tools\generate_swc_workbooks.py
```

该命令会重建仓库内两份工作簿；不要直接修改生成文件中的布局。验证生成器时可用
`--output-dir <临时目录>` 输出到其他位置，不覆盖仓库文件。
模板使用 Excel 表格自动扩展筛选范围，页名和列名仅使用当前规范；运行时由解析器完整校验。

## 测试端口

工作簿只在 `P-Ports`、`R-Ports` 中添加端口，接口使用 `System.arxml` 中已有的 `/BcmTest/PowerModeInterface`（uint8）
和 `/BcmTest/PositionActiveInterface`（boolean），均 `Create RTE Artifact=N`：

| 组件 | 端口 | 方向 | 接口 |
| --- | --- | --- | --- |
| LIT | `p_LIT_TestPowerMode` | P | `PowerModeInterface` |
| LIT | `r_LIT_TestPositionActive` | R | `PositionActiveInterface` |
| Horn | `p_Horn_TestPositionActive` | P | `PositionActiveInterface` |
| Horn | `r_Horn_TestPowerMode` | R，`Enable Update=Y` | `PowerModeInterface` |

LIT 与 Horn 的端口方向互补，可用于后续测试 BCM 与 MC 组件连线。

## 运行

在仓库根目录安装本地构建的 wheel（构建方式见 [docs/dev/release.md](../../../docs/dev/release.md)）后执行。命令行相对路径按当前目录解析，配置中的 `projectRoot`（`../../..`）指向仓库根目录：

```powershell
python -m pip install ./dist/table_to_arxml-0.3.0-py3-none-any.whl
table-to-swc-arxml --config test/samples/swc/TableToSwcArxml.toml
```

- 预期退出码为 0，汇总包含 `Validated 4 of 4 requested ports: 4 to create, 0 already valid, 0 skipped` 和 `2 ARXML file(s) affected`。
- 修改后的 `LIT.arxml`、`Horn.arxml` 按仓库相对路径写到 `test/samples/swc/output/SwcTest/test/data/common/arxml/...`，源模型不变。
- 日志写到 `test/samples/swc/log/SwcTest`。
