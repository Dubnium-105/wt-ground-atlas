# GROUND OPS · 战争雷霆陆战历史数据工作台

独立静态前端 + 离线数据流水线，提供队列推荐、版本变化、载具图鉴、逐车对抗与条件防护研究。运行网站不需要 Python 后端、数据库或常驻 GPU 服务。

**为轻量托管分离三类内容：源码留在仓库；站点构建到 `dist/site/`；10+GB 的概率分片单独发布到对象存储或静态数据服务器。** 浏览器只在打开防护研究时读取对应索引和切片，站点部署不会携带全量概率数据。

本仓库包含源码、配置、测试、原始参考实现和少量数值样例，**不包含生产快照、全量概率数据或预构建网站**。新克隆可以运行自动化测试；构建实际站点前需要准备自己的离线数据。历史覆盖和验收文档不代表本次克隆已带有相应产物。

## 自动预测产品入口（V3.1）

`npm run build` / `python build.py --forecast` 构建自动队列预测首页，统一发布到 `dist/site`。提供 `--research-site` 可将已有且通过校验的图鉴、逐车对抗、防护研究合并到 `/research/`。无真实输入时生成明确的诊断页，不生成假榜。旧研究独立构建仍可用 `npm run build:research`。

完整的单条构建命令、九种技能/玩法情景、来源绑定、动态 BR、版本迁移、缓存、统计契约和回滚方法见 [产品手册](docs/NEXT_STAGE_PRODUCT.md)；实际测试、覆盖与发布阻塞见 [本次验收](docs/NEXT_STAGE_ACCEPTANCE.md)。生产数据不在仓库中，报告中的 10m 条件结果不是胜率或远距离预测。 当前 Wiki 目录可用 `scripts/update_forecast.py --refresh-wiki --before ... --catalog-output ...` 刷新到独立文件；该过程保留原物理来源，并显式隔离未重新核验的旧挂载和版本身份。

## 目录职责

| 路径 | 职责 | 发布到站点 |
|---|---|---|
| `web/` | 页面、样式、浏览器模块、本地字体 | 由构建器复制 |
| `shared/` | Node 与浏览器共用的决策引擎、概率解码器，只有一份源码 | 由构建器复制 |
| `backend/wt_atlas/` | 资料采集、客户端处理、CPU/CUDA 计算、导出 | 否 |
| `backend/vendor/` | 固定版本的参考实现、工具与数值样例 | 否 |
| `scripts/` | 构建、数据验证、发布打包、本地服务 | 否 |
| `config/` | 模型、来源、流水线、部署与显示配置 | 仅必要的前端配置 |
| `tests/` | 数值回归、部署边界、浏览器测试与明确标记的合成夹具 | 否 |
| `docs/` | 架构、开发、部署与数据契约 | 否 |
| `deploy/` | 可选的静态服务与 CORS 配置示例 | 按托管环境配置 |
| `data/snapshots/` | 离线输入快照；不入 Git | 否 |
| `data/derived/recommendations/` | 可重建的推荐中间产物；不入 Git | 只复制当前清单引用的分片 |
| `data/exports/probability/` | 大型概率索引与内容寻址分片；不入 Git | 单独发布 |
| `data/raw/`、`data/work/`、`data/cache/` | 采集、计算与缓存；不入 Git | 否 |
| `dist/site/` | 可直接部署的静态站点；不入 Git | **只发布此目录内容** |

详细依赖与数据流见 [架构说明](docs/ARCHITECTURE.md)。

## 新克隆：先验证代码

需要 Python 3.11+、Node.js 20+。网站组装和打包仅使用 Python 标准库；数值测试与离线计算才需要 `requirements.txt`。

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -r requirements.txt
npm ci
npm test
npx playwright install chromium
npm run test:smoke
```

`npm test` 覆盖决策、解码、传输、离线流水线与部署边界。`test:smoke` 自动创建临时合成快照，启动两个本地 HTTP 源，检查页面、Worker、子路径部署、远端分片、故障重试；它不采集资料、不计算全量 GRP、不上传网站。

只检查静态构建边界时，可运行 `npm run test:site`，不需要安装科学计算依赖。完整生产浏览器验收保留为 `npm run test:browser`，需要实际快照及本地 `embedded` 构建（完整热图）和已启动的站点；该历史套件会阻止 HTTPS 外部请求，外部部署使用新的双源冒烟测试并补做真实数据验证。

## 构建实际站点

准备 `data/snapshots/catalog.json`、`changes.json`、`target-layers.json` 和 `reports/target-layers.json`。`decision-probabilities.json` 可选：缺失会明确使用无摘要状态，不会填造概率。来源准备与客户端步骤见 [开发指南](docs/DEVELOPMENT.md) 和 [客户端后端](docs/CLIENT_BACKEND.md)。

```bash
# 默认构建推荐、目录和版本页面；不发布参考弹热图数据
python build.py

# 为全量概率数据启用独立、不可变的发布地址
python build.py --probability-base-url https://data.example.com/probability/RELEASE_ID/

# 仅重新组装站点；仍然校验推荐与输入哈希是否一致
python build.py --skip-decisions --probability-base-url https://data.example.com/probability/RELEASE_ID/

# 本地浏览生成的站点
python scripts/serve.py --no-browser
```

`RELEASE_ID` 由概率发布验证器生成，替换为你实际上传的版本目录。外部模式不要求站点构建机持有全量数据；若本机有对应索引，构建器会将索引哈希写入站点清单，远端必须上传同一份索引。也可在部署配置中显式固定索引 SHA-256。

### 三种概率数据模式

| 模式 | 用途 | 站点产物中的概率热图 |
|---|---|---|
| `disabled`（默认） | 没有配置数据源时先使用推荐、图鉴、版本与对抗功能 | 不包含；研究页显示暂未提供 |
| `external` | **10+GB 数据的常规部署** | 仅保存外部索引描述；按需跨域读取 |
| `embedded` | 明确需要完整离线交付或同源大容量托管 | 复制当前索引引用的数据；需要足够空间和显式体积预算 |

模式只控制原参考弹概率热图的发布方式。浏览器摘要仅包含引擎明确接受的 `reference_only: false` 配对记录，保留模型身份、目标存在信息及原快照哈希；完整研究摘要留在离线快照中。目标抽样层与决策含义保持一致。配置位于 `config/deployment.json`。默认站点上限为 100 MiB、单文件上限为 25 MiB，超限构建会失败并保留上一版产物。

## 分开发布站点与大数据

```bash
# 在计算机上校验数据，仅生成文件清单，不复制 10+GB 数据
python scripts/prepare_probability_release.py --source data/exports/probability --list-files reports/probability-files.txt

# 构建后只打包静态站点，ZIP 解压后根目录就是 index.html
python scripts/package_release.py --output dist/wt-atlas-site.zip
python scripts/check_package.py --archive dist/wt-atlas-site.zip
```

使用你现有的上传工具按清单发布概率文件，先分片后索引；站点只上传 `dist/site/`。跨域数据源需要正确的 CORS 与 gzip 配置。完整示例、缓存策略、回滚和旧目录迁移见 [部署指南](docs/DEPLOYMENT.md)。

## 功能与可信度

- **队列推荐与版本变化**：离线预计算国家/版本分片，支持采用队列、对比调整前后及自定义条件下的 Worker 计算。
- **载具图鉴与逐车对抗**：展示来源、弹药归属、挂载、独立正反结果、未知项与权重。
- **防护研究**：按目标构型、精确参考弹、物理条件和角度加载切片，保留未知区间、状态和材料标志。
- **本地方案**：队列与拥有列表保存在浏览器，方案可导入/导出，不会改写生产基线。

决策指数不是胜率；概率图是分层直线路径条件估计，不是击毁率。未知值不会填为零，参考弹图不证明任何攻击载具拥有该弹药。NERA 动态、ERA 消耗、碎片、爆压和完整毁伤模型的边界见 [概率契约](docs/PROBABILITY_CONTRACT.md) 与 [决策引擎](docs/DECISION_ENGINE.md)。

这是非官方爱好者项目，与 Gaijin Entertainment 无隶属关系。

## 文档入口

[文档索引](docs/README.md) · [架构](docs/ARCHITECTURE.md) · [开发](docs/DEVELOPMENT.md) · [部署与迁移](docs/DEPLOYMENT.md) · [数据来源](docs/SOURCES.md) · [流水线](docs/PIPELINE.md)
