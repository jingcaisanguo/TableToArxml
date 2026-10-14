# 命令测试数据

本目录存放两个工具的全部命令测试输入：配置、Excel、ARXML 和清单。均为精简的测试数据，不是生产工程输入。

```text
test/
├── common/                  两个工具共用的工程模型
│   ├── arxml.lst            ARXML 清单，条目相对仓库根目录
│   └── arxml/               System.arxml、BcmArxml/、McArxml/
├── e2e/                     端到端测试：运行样例并与 expected/ 比对
├── tableToSystemArxml/      table-to-system-arxml 测试：BCM 绿表、MC 白表；expected/ 为基线产物
└── tableToSwcArxml/         table-to-swc-arxml 测试：向 LIT、Horn 添加测试端口；expected/ 为基线产物
```

- 各测试配置的 `projectRoot` 都是仓库根目录（`../..`），共用 [common/arxml.lst](common/arxml.lst)。
- 产物和日志写到各工具测试目录下的 `output/`、`log/`，已在 [.gitignore](.gitignore) 中忽略。
- 测试命令都从仓库根目录执行，详见 [tableToSystemArxml/README.md](tableToSystemArxml/README.md) 和 [tableToSwcArxml/README.md](tableToSwcArxml/README.md)。

## 自动测试

在装好根目录 `requirements.txt` 依赖和 `pytest` 的环境中，从仓库根目录执行：

```powershell
python -m pytest test
```

- [e2e/test_samples.py](e2e/test_samples.py) 直接运行源码中的两个工具，产物写到临时目录，不覆盖本目录。
- ARXML 统一换行符后与 `expected/` 逐字节比对；Excel 按单元格值比对。有意改变输出时，用新产物替换对应基线并在提交说明中写明原因。
- 另有一个 SWC 场景：同一文件中两个组件的 runnable 同名，验证新增内容只写入指定组件。
- CI（[tests.yml](../.github/workflows/tests.yml)）在 Ubuntu 和 Windows 上分别用 armodel 1.8.7、1.9.2 运行 ruff 和上述测试。

## 共享模型

| 文件 | 内容 |
| --- | --- |
| [common/arxml.lst](common/arxml.lst) | 五个输入 ARXML |
| [common/arxml/System.arxml](common/arxml/System.arxml) | 系统、根组合和 ECU；uint8/boolean 类型、`/BcmTest` 下的 `PowerModeInterface`、`PositionActiveInterface`，以及 MC 的内存段依赖 |
| [common/arxml/BcmArxml/LIT.arxml](common/arxml/BcmArxml/LIT.arxml) | LIT 组件、端口、内部行为和实现 |
| [common/arxml/BcmArxml/TUR.arxml](common/arxml/BcmArxml/TUR.arxml) | TUR 组件、端口、内部行为和实现 |
| [common/arxml/McArxml/AUXL.arxml](common/arxml/McArxml/AUXL.arxml) | AUXL 组件、服务/信号 P-port、内部行为和实现 |
| [common/arxml/McArxml/Horn.arxml](common/arxml/McArxml/Horn.arxml) | Horn 组件、ClientServer/SenderReceiver R-port、接口、类型和实现 |

BCM 和 MC 是同一个工程模型，`BcmArxml/`、`McArxml/` 只用于归类组件文件。
MC 组件沿用原 ARXML 的 `/BcmSystem/SwComponentTypes/...` 路径，不按文件夹名称重命名模型元素。
测试只把结果写到 `output/`，不修改本目录中的源模型。
