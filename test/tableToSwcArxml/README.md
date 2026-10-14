# table-to-swc-arxml 命令测试

返回 [../README.md](../README.md)。配置为 [TableToSwcArxml.json](TableToSwcArxml.json)，工作簿为 [SwcTestPorts.xlsx](SwcTestPorts.xlsx)，
使用共享清单 [../common/arxml.lst](../common/arxml.lst)。工作簿格式见 [workbook-format.md](../../docs/user/tableToSwcArxml/workbook-format.md)。

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

在仓库根目录安装本地构建的 wheel（构建方式见 [docs/dev/release.md](../../docs/dev/release.md)）后执行。命令行相对路径按当前目录解析，配置中的 `projectRoot`（`../..`）指向仓库根目录：

```powershell
python -m pip install ./dist/table_to_arxml-0.2.1-py3-none-any.whl
table-to-swc-arxml --config test/tableToSwcArxml/TableToSwcArxml.json
```

- 预期退出码为 0，汇总为 `Validated 4 of 4 requested ports: 4 to create ...; 2 ARXML file(s) affected.`
- 修改后的 `LIT.arxml`、`Horn.arxml` 按仓库相对路径写到 `test/tableToSwcArxml/output/SwcTest/test/common/arxml/...`，源模型不变。
- 日志写到 `test/tableToSwcArxml/log/SwcTest`。
