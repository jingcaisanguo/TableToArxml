# table-to-system-arxml 命令测试

返回 [../README.md](../README.md)。BCM 使用 [BcmTableToSystemArxmlConfig.json](BcmTableToSystemArxmlConfig.json) 测试绿表模式，
MC 使用 [McTableToSystemArxmlConfig.json](McTableToSystemArxmlConfig.json) 测试白表模式，两者共用 [../common/arxml.lst](../common/arxml.lst)。

## 输入

| 文件 | 内容 |
| --- | --- |
| [GreenTable/Bcm/LIT.xlsx](GreenTable/Bcm/LIT.xlsx) | 一条输入、一条输出 |
| [GreenTable/Bcm/TUR.xlsx](GreenTable/Bcm/TUR.xlsx) | 相同两条连接从对端声明，用于验证去重 |
| [WhiteTable/BcmWhiteTable.xlsx](WhiteTable/BcmWhiteTable.xlsx) | 从上述绿表生成的白表，用于直接白表模式 |
| [WhiteTable/McWhiteTable.xlsx](WhiteTable/McWhiteTable.xlsx) | AUXL/Horn 的白表，用于 MC 直接白表模式 |

BCM 测试连接：

- `TUR.PowerMode` -> `LIT.VeINP_CAN_PowerMode_sig`，uint8。
- `LIT.VbOUT_LIT_FLPosition_flg` -> `TUR.PositionActive`，boolean。

LIT 端口名称保留原命名，但测试连接是普通组件连接，不涉及 CAN 信号映射。
两份表重复声明同一连接，最终只生成两条连接，不应生成四条。

MC 白表声明 `itsAUXL`、`itsHorn` 两个 `Add` 实例，测试以下两条连接：

- `AUXL.HornTemplateService` -> `Horn.Horn_Templet1`，ClientServer。
- `AUXL.HornTemplateSignal` -> `Horn.Horn_Templet2`，SenderReceiver uint8。

## 准备

使用 Python 3.9 或更新版本，在独立虚拟环境中安装本地构建的 wheel（构建方式见 [docs/dev/release.md](../../docs/dev/release.md)），依赖为 wheel 声明的 `armodel>=1.8.7,<1.9.4`。
以下命令都从**仓库根目录**执行；命令行相对路径按当前工作目录解析，JSON 中的路径按配置文件所在目录解析。

```powershell
python -m pip install ./dist/table_to_arxml-0.3.0-py3-none-any.whl
```

## BCM 绿表

```powershell
table-to-system-arxml --config test/tableToSystemArxml/BcmTableToSystemArxmlConfig.json
```

产物写到 `test/tableToSystemArxml/output/BcmTest`，日志写到 `test/tableToSystemArxml/log/BcmTest`。
预期退出码为 0，生成 `BcmWhiteTable.xlsx`、`BcmGreenTables.xlsx`、`BcmSystem.arxml`。
ARXML 包含两个组件原型、两条装配连接、两个实现映射，以及指向同一 ECU 的两个组件引用。
不要求 `PrefSystemImporter`、XDM 或原生产工程目录。

## BCM 白表

```powershell
table-to-system-arxml --config test/tableToSystemArxml/BcmTableToSystemArxmlConfig.json --white-table test/tableToSystemArxml/WhiteTable/BcmWhiteTable.xlsx --output-dir test/tableToSystemArxml/output/BcmWhiteTest --log-dir test/tableToSystemArxml/log/BcmWhiteTest
```

该模式只生成 `BcmSystem.arxml`；规范化 XML 后应与绿表模式输出相同。
修改绿表或接口后，先运行绿表模式，再更新保存的白表，避免两种输入不一致。

## MC 白表

```powershell
table-to-system-arxml --config test/tableToSystemArxml/McTableToSystemArxmlConfig.json
```

配置使用单数 `whiteTable` 字段，不是 `whiteTables`，不需要 MC 绿表。
产物写到 `test/tableToSystemArxml/output/McTest`，日志写到 `test/tableToSystemArxml/log/McTest`。
预期退出码为 0，只生成 `McSystem.arxml`，不生成白表或合并绿表。
输出包含两个组件、两条连接、两个实现映射及同一 ECU 下的两个组件引用。
白表模式的 Composition/System/根组合/ECU 引用来自白表 General 页，不由配置中的 `general` 覆盖。

## 联合运行

```powershell
table-to-system-arxml --config test/tableToSystemArxml/McTableToSystemArxmlConfig.json --config test/tableToSystemArxml/BcmTableToSystemArxmlConfig.json --output-dir test/tableToSystemArxml/output/BcmMcTest --log-dir test/tableToSystemArxml/log/BcmMcTest
```

命令行统一目录，避免两份配置各自目录不同导致报错；五个输入模型只加载一次。
生成 `McSystem.arxml`、`BcmSystem.arxml` 及 BCM 的两份 Excel。
各 ARXML 只写出对应输入表格声明的组件与连接，并非自动累计导出所有组件。

## 覆盖范围

本数据覆盖表格解析、组件添加、连接去重、接口类型检查及系统/ECU/实现映射。
不覆盖 CAN/LIN、EB 清单生成、接口映射策略或完整 AUTOSAR XSD 验证。

后续跨 BCM/MC 连线仍使用同一清单：在表格中声明参与的两端组件实例、端口和连接即可。
白表中 `Add` 创建实例，`Reference` 只能引用目标 Composition 中已有的实例；仅加载组件类型不等于已存在实例。
两端接口须兼容，且 P-port/R-port 方向正确。当前样例尚未添加跨组连接，不会因共用清单自动生成跨组连线。
