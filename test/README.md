# 测试

本目录存放两个工具的全部测试代码、测试输入（配置、Excel、ARXML、清单）和基线产物。均为精简的测试数据，不是生产工程输入。

```text
test/
├── helpers.py               共用路径、以 python -m 运行工具、基线比较
├── system_fixtures.py       System 测试用的最小 ARXML 模型、白表和配置构造函数
├── swc_fixtures.py          SWC 共用模型和工作簿构造函数
├── e2e/                     端到端测试：在子进程中运行工具
│   ├── system/              BCM/MC 样例基线；绿表/白表自动生成与复用接口映射、无效映射阻止输出；接口不一致策略、连接器命名
│   └── swc/                 SWC 样例基线、规范名称校验、错误阻止写出、各类新增对象；工作簿布局与再生成
├── unit/                    单元测试：直接调用模块
│   ├── common/              清单读取、原子写出、日志与 ERROR 收集、依赖版本
│   ├── system/              组合实例链、组件名映射、类型签名、连接器/页签命名、命令行退出码
│   └── swc/                 工作簿读取、容器按 schema 顺序插入、命令行退出码与未知参数拒绝
├── contract/                第三方库契约测试：armodel、openpyxl 中工具依赖的行为与绕行代码
├── data/common/             两个工具共用的工程模型（数据，不是测试代码）
│   ├── arxml.lst            ARXML 清单，条目相对仓库根目录
│   └── arxml/               System.arxml、BcmArxml/、McArxml/
├── samples/system/         System 样例：BCM 绿表、MC 白表；expected/ 为基线产物
└── samples/swc/            SWC 样例：向 LIT、Horn 添加测试端口；expected/ 为基线产物
```

- 各样例配置的 `projectRoot` 都是仓库根目录（`../../..`），共用 [data/common/arxml.lst](data/common/arxml.lst)。
- 手工运行样例时产物和日志写到各工具测试目录下的 `output/`、`log/`，已在 [.gitignore](.gitignore) 中忽略；
  自动测试只写临时目录，不覆盖本目录。
- 手工运行方式见 [System 样例](samples/system/README.md) 和 [SWC 样例](samples/swc/README.md)。

## 自动测试

在装好根目录 `requirements.txt` 依赖、`pytest` 和 `ruff` 的环境中，从仓库根目录执行：

```powershell
python -m ruff check
python -m pytest
```

- pytest 选项在 [pyproject.toml](../pyproject.toml) 中配置：`src` 和 `test` 加入导入路径，使用 importlib 导入模式，
  因此不同目录下可以有同名测试文件，也无需先安装 wheel。
- 端到端测试通过 `python -m table_to_arxml.system`、`python -m table_to_arxml.swc` 在子进程中运行源码，行为与安装后的命令一致。
- ARXML 统一换行符后与 `expected/` 逐字节比对；Excel 按单元格值比对。有意改变输出时，用新产物替换对应基线并在提交说明中写明原因。
- SWC 样例工作簿和空白模板由 [tools/generate_swc_workbooks.py](../tools/generate_swc_workbooks.py) 生成；
  [e2e/swc/test_workbooks.py](e2e/swc/test_workbooks.py) 检查两者的布局、样式和数据验证，并确认重新生成的结果与仓库文件一致。
- 契约测试覆盖 CI 指定的 armodel 1.8.7、1.9.2、1.9.8，确认 `armodel_compat.py` 在新旧版本下得到相同的输出；依赖上限为 `1.9.8`。新版本须补充兼容测试、加入 CI 矩阵并通过全部测试后，才能提交更新依赖上限。
- CI（[tests.yml](../.github/workflows/tests.yml)）在 Ubuntu/Windows、Python 3.9/3.13、armodel 1.8.7/1.9.2/1.9.8 的组合上运行 ruff 和全部测试。
- 新增回归覆盖输入/输出路径重合、大小写冲突、空表头、跨文件定义冲突、解析失败、严格类型、名称长度和写入失败清理。
- 文档契约测试检查文件链接、交付目录的独立性和规范目录结构；wheel 的实际命令与三组转换基线另由
  [verify_wheel.py](../tools/verify_wheel.py) 在安装后的独立环境验证。

## 共享模型

| 文件 | 内容 |
| --- | --- |
| [common/arxml.lst](data/common/arxml.lst) | 五个输入 ARXML |
| [common/arxml/System.arxml](data/common/arxml/System.arxml) | 系统、根组合和 ECU；uint8/boolean 类型、`/BcmTest` 下的 `PowerModeInterface`、`PositionActiveInterface`，以及 MC 的内存段依赖 |
| [common/arxml/BcmArxml/LIT.arxml](data/common/arxml/BcmArxml/LIT.arxml) | LIT 组件、端口、内部行为和实现 |
| [common/arxml/BcmArxml/TUR.arxml](data/common/arxml/BcmArxml/TUR.arxml) | TUR 组件、端口、内部行为和实现 |
| [common/arxml/McArxml/AUXL.arxml](data/common/arxml/McArxml/AUXL.arxml) | AUXL 组件、服务/信号 P-port、内部行为和实现 |
| [common/arxml/McArxml/Horn.arxml](data/common/arxml/McArxml/Horn.arxml) | Horn 组件、ClientServer/SenderReceiver R-port、接口、类型和实现 |

BCM 和 MC 是同一个工程模型，`BcmArxml/`、`McArxml/` 只用于归类组件文件。
MC 组件沿用原 ARXML 的 `/BcmSystem/SwComponentTypes/...` 路径，不按文件夹名称重命名模型元素。
测试只把结果写到 `output/` 或临时目录，不修改本目录中的源模型。
