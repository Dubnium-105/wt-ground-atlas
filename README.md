# GROUND OPS · 战争雷霆陆战历史决策工作台

## GitHub 源码仓库

此仓库仅跟踪源码、配置、文档、测试和小型示例数据。生产快照（`data/snapshots/`）、运行与构建报告（`reports/`）以及预构建网站数据（`web/data/`）不纳入版本控制。新克隆需要按下文的数据采集/客户端流程准备输入，再运行 `python build.py` 生成网站数据；完整预构建交付包仍按“发布包”一节生成。

这是一套**独立静态网站 + 离线数据后端**。没有使用 Sites，不依赖账号、在线业务 API、数据库或常驻 GPU 服务。前端读取已生成的数据分片；采集、GRP 解码、射线和物理计算、版本推演预计算由后端批处理完成。

交付包已经包含可以直接运行的 `web/`，以及它的完整源码、版本化输入快照、模型参数、更新/构建脚本、测试与审计记录。首次使用不需要联网抓取数据，也不需要重新计算 GRP。

**当前客户端数据：**已接入本地 2.59.0.86 客户端的地面车型配置、基础/补丁 GRP 和逐节点材料，覆盖目录中的 1,225 辆地面载具。原脚本两份参考弹的全视角条件防护计算、来源与覆盖结果见 [客户端后端](docs/CLIENT_BACKEND.md) 和 `reports/client-*.json`。弹丸原始定义与已完成物理计算分开记录；参考弹图不自动建立生产精确配对。

## 直接打开站点

解压完整项目。Windows 可以双击 `run-local.cmd`，或在项目根目录执行：

```powershell
python scripts/serve.py
```

Linux / macOS：

```bash
python3 scripts/serve.py
```

地址和端口显示在终端，也会自动打开浏览器。默认地址、端口和是否自动打开浏览器都来自 `config/hosting.json`；可用 `--port`、`--bind`、`--no-browser` 覆盖。

本地脚本使用 Python 3.11 或更新版本；仅浏览预构建网站不需要 Node.js。重新计算推荐与构建需要 Node.js 20 或更新版本。完整交付包内含逐文件 SHA-256 清单，解压后可执行 `python scripts/verify_release.py` 检查下载与解压是否完整。

也可以使用普通静态服务：

```bash
python -m http.server --directory web
```

请通过 HTTP 打开。浏览器的 `file://` 限制会阻止模块、Worker 和数据读取，双击 `index.html` 不是运行方式。

## 页面与操作

主导航仅保留“队列推荐”和“版本变化”；我的队列、载具图鉴、逐车对抗、防护研究和数据与方法从“研究工具”进入。手机端主导航直接可见，推荐成员横向浏览，采用操作位于首屏。设计依据为 [anthropics/skills 的 frontend-design](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md)，保留本地字体与原有静态数据加载方式。

| 页面 | 功能 |
|---|---|
| 队列推荐（首页） | 首屏推荐阵容、一键采用、其他组合、本国版本变化摘要；全 BR 扫描与各国比较按需展开。 |
| 版本变化 | 默认本国 BR 前后对照，上调/下调/性能筛选、全部国家范围、20 条分页；队列影响比较、跨国影响图与方案编辑。 |
| 载具图鉴 | 官方国家、等级、分支、文件夹、前置关系、陆战历史 BR、变更筛选与档案入口。 |
| 我的队列 | 槽位、特种载具、拥有列表、队列导入导出、具体弹药与已验证合法预设、射距、技能、重生点及辐射条件。 |
| 逐车对抗 | 每辆己方车对各相关敌人的单向矩阵，独立反向结果、弹药/挂载、缺项、权重、条件与来源。 |
| 防护研究 | 按目标、精确参考弹、视角加载概率切片；像素/ray_id/材料标记、非均匀瞄准、未知区间与落空质量。 |
| 来源与审计 | 来源快照、目录/属性/物理覆盖、模型配置、变更冲突、量化误差和构建信息。 |

推荐使用同一个纯函数引擎在离线 Node 构建和浏览器 Worker 中运行。搜索是确定性的角色约束启发式搜索，不宣称穷举所有组合的全局最优。

### 建议的首次检查顺序

1. 在全局栏选择国家、BR 和版本，在总览采用一支队列。
2. 在队列实验室调整成员或限制，执行分析；若改变推荐条件，重新扫描本国全部 BR。
3. 查看矩阵并点击单元格，检查弹药、原始指数、反向结果、缺项和汇总权重。
4. 在版本推演中比较原成员与重新编队；也可以导入 JSON 方案或使用本地编辑器。
5. 在防护研究切换目标、弹药、角度和技能，查看完整概率质量与物理射距标签。

队列和拥有列表默认保存在浏览器本地。方案可导出为 JSON，当前生产基线不会被本地编辑覆盖。

## 数据和数值如何管理

车辆 BR、性能、弹药和挂载均来自输入快照。经验权重、未知先验、匹配跨度、搜索限制、默认条件和瞄准误差参数均在配置中；渲染层不内置车辆参数、推荐榜单或针对特定车型的加分。

| 文件 | 内容 |
|---|---|
| `data/snapshots/catalog.json` | 原始规范化目录及逐字段来源，当前基线。 |
| `data/snapshots/changes.json` | 腾讯计划或本地方案，带旧值校验与审计。 |
| `data/snapshots/changes-raw-rows.json` | 可追溯的原始调整行。 |
| `data/snapshots/decision-probabilities.json` | 离线生成的轻量条件积分摘要；当前参考记录不可冒充已验证的攻击者配对。 |
| `config/model.json` | 所有决策经验系数、规则参数、行为模型与默认条件。 |
| `config/pipeline.json` | GRP 获取、CPU/CUDA 计算、物理来源、采样和量化配置。 |
| `config/sources.json` | 采集入口、固定 Datamine 提交、解析规则、单位换算。 |
| `config/ui.json` | 显示、分页、配色与输入范围；不存放车辆结论。 |
| `config/hosting.json` | 本地静态服务配置。 |

数学恒等式、二进制布局、数组索引、协议状态码和 CSS 排版数值不属于车辆数据或待校准游戏规则。原上传 CUDA 参考包保存在 `backend/vendor/grp_probability_v2/`，保持原始文件与哈希用于审计；新 CPU 主流程的数值参数来自配置。

## 更新与可复现构建

只重新构建站点，需要 Python 和 Node.js；前端不需要 npm 运行依赖：

```bash
python build.py
```

这一命令从本地快照生成两个版本的全部国家/BR 推荐、影响图与静态分片。缓存身份包含目录、方案、决策模型、物理摘要和引擎代码。输入不变时复用推荐构建；组装器会拒绝混入旧输入生成的推荐。

离线后端依赖安装：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Linux / macOS 使用模块前设置：

```bash
export PYTHONPATH=backend
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="backend"
```

重新采集官方资料及调整表：

```bash
python -m wt_atlas sources --refresh --changes
python build.py
```

不带 `--refresh` 使用已存在的离线缓存。`sources` 会重新生成快照，`--help` 才是无写入查看。不要在数据更新中途发布网站；完成采集、审核、预计算后再整体替换发布目录。

## 全量 GRP 的后端工作流

完整客户端入口会自动匹配基础和补丁资源、生成材料配置、计算并发布：

```powershell
$env:PYTHONPATH="backend"
python -m wt_atlas client all --source "D:\steam\steamapps\common\War Thunder" --workers 8
python -m wt_atlas summarize
python build.py
```

状态与恢复命令见 [CLIENT_BACKEND.md](docs/CLIENT_BACKEND.md)。下面的 `acquire/run` 仍保留为显式 profile 工作流，其默认来源是四辆原始样本：

```bash
python -m wt_atlas acquire --source /path/to/war-thunder-client
python -m wt_atlas decode
python -m wt_atlas status
python -m wt_atlas run
python -m wt_atlas export
python -m wt_atlas summarize
python build.py
```

也支持显式本地/远端下载清单，逐文件声明 URL、SHA256、稳定 ID 和来源版本。不会猜测下载地址，不会在网页访问时下载游戏客户端。任务支持内容去重、重试、断点继续、单任务失败隔离与配置变更后缓存失效。

**GRP 不是全部防护输入。**裸几何还必须关联同版的 DamageParts、材料、装甲机制与排除语义。没有配置的新文件可以先解码，再进入 `waiting_profile`；版本不齐进入 `waiting_version_alignment`。后端不会复制相似坦克的配置、猜测材料系数或把未知当零。具体字段及绑定命令见 [PIPELINE.md](docs/PIPELINE.md)。

已有真实层缓存可以直接在 CPU 上重算。CUDA 路径作为原实现兼容入口保留，当前交付环境未执行 GPU 路径。

物理结果按目标、精确弹药、物理条件和角度生成轻量切片与摘要。使用配对摘要前还必须满足载具实际弹药归属、构型、客户端来源、物理射距、姿态、材料/几何版本和瞄准上下文等一致性条件。当前上传图是独立参考层，无法核验的 Wiki↔Datamine 弹药关联不会自动建立。

## 静态部署

将 **`web/` 内的全部内容**复制到任意静态 HTTP 托管目录即可。首页只读取配置、轻量目录、当前国家和版本的推荐分片；自选计算按需加载计算目录；打开防护研究才读取对应切片。

`.gz` 旁文件由浏览器显式读取并解压，因此普通静态服务器也可使用压缩结果，不要求特殊业务服务。若服务器另行启用透明 gzip，避免对已经是 `.gz` 的文件再次设置 `Content-Encoding: gzip`；它们是压缩文件资源。

不要把 `backend/`、`data/raw/`、`data/work/` 或原始 GRP/NPZ 上传到公开站点。原始资料、完整射线层和材料大表均留在计算端。

## 测试和验收

```bash
node tests/engine.test.mjs
node tests/probability_summaries.test.mjs
PYTHONPATH=backend python -m unittest discover -s tests -p 'test_*.py' -v
```

浏览器验收使用开发环境中的 Playwright，普通静态 HTTP 与外部服务不可用的条件下检查核心流程：

```bash
python scripts/serve.py --port 8765 --no-browser
# 另一终端，仅运行开发测试时安装以下依赖
npm install
npx playwright install chromium
node tests/browser.e2e.mjs
```

测试用的地址、视口和资源预算位于 `config/qa.json`。它们是验收夹具，不会被写入生产载具数据。实际测试和测量见 `reports/`，逐条验收和未完成的数据项见 [ACCEPTANCE.md](docs/ACCEPTANCE.md)。

发布包可以用 `python scripts/package_release.py --output /path/release.zip` 重新生成。该命令只纳入当前清单引用的静态分片，生成逐文件 SHA-256，并在原子写入后检查所有 ZIP 条目的 CRC。进一步执行 `python scripts/check_package.py --archive /path/release.zip`，会在新的临时目录中验证全部哈希、导入后端并离线重建站点；不依赖被排除的采集缓存和计算缓存。

## 可信度边界

- 决策指数是待校准的效用模型，不是胜率、保证收益或降房概率。
- Wiki 采集快照没有公开游戏提交号；不会假定它与独立客户端概率数据物理同版。
- 标称物理厚度、RHAe、模型栈消耗和概率是不同量；未知等效防护保持未知。
- 防护图是分层直线路径条件估计，尚未覆盖完整 NERA 动态、ERA 消耗、有限口径采样、碎片、爆压和毁伤状态。
- 航空自选挂点表仅证明挂点兼容；未核验的组合不会被拼成合法混挂。缺少雷达、重生点和武器包线时继续显示缺项。
- 本地地面目录的资源与材料已批量接入；四份上传 profile 用于独立数值对照。当前概率图仍限于原脚本 M829A2、M830 两份参考弹与其指定条件，游戏内校准和完整毁伤模型尚未实现。

更多说明：[来源](docs/SOURCES.md) · [决策引擎](docs/DECISION_ENGINE.md) · [轻量概率契约](docs/PROBABILITY_CONTRACT.md) · [完整验收](docs/ACCEPTANCE.md)。


## 2026-10-26：用弹评分与推荐页更新

- 推荐主页按评分并列显示该系全部 BR 候选中的前三支队列，按成员去重。默认容量为 10 槽；自定义队列入口在「研究工具」中。
- 点击国家/区域旗帜切换；「目标权重 BR」滑条位于自定义队列内，仅限定自选成员，不筛选首页榜单。
- 版本变化页分别显示新版评分前三、被拆队列中的旧版评分前三。被拆指成员 BR 调整不同步；卡片同时展示旧版评分、保留原成员后的新版评分和变化成员。
- 评分按本车弹药对敌车 GRP 抽样路径计算，自动模式逐目标选弹；可在自定义队列指定弹药。正反方向独立计算。
- 汇总提高对抗表现的占比，降低角色齐全的固定加分；区间与实际不利对手表现分开计算。分数仍为未校准决策指数。

已有快照可直接运行 `python build.py`。更新客户端射线层后，先运行 `python scripts/build_target_layers.py --workers 6` 再构建。抽样模型不包含完整毁伤与动态反应装甲，详见 [决策引擎](docs/DECISION_ENGINE.md)。
