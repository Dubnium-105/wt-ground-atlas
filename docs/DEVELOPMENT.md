# 开发与可复现验证

## 环境分层

| 工作 | 需要 |
|---|---|
| 浏览已生成的网站 | 静态 HTTP 服务与现代浏览器 |
| 组装、验证和打包站点 | Python 3.11+ 标准库 |
| 计算推荐 | Node.js 20+；无 npm 运行依赖 |
| 采集、GRP 与数值计算 | `requirements.txt`；可选匹配驱动的 CuPy |
| 浏览器测试 | `npm ci` 与 Playwright Chromium |

建议在虚拟环境中安装计算依赖。`requirements-tested.txt` 记录原数值环境，不代表所有新环境已逐一验证；CI 使用 `requirements.txt` 的兼容范围并执行回归测试。

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm ci
npm test
npx playwright install chromium
npm run test:smoke
```

测试入口会自动把 `backend/` 加到 Python 模块路径，不必为了测试设置 `PYTHONPATH`。离线 CLI 仍使用以下入口：

```bash
# Linux/macOS
export PYTHONPATH=backend
python -m wt_atlas --help
```

```powershell
# Windows PowerShell
$env:PYTHONPATH="backend"
python -m wt_atlas --help
```

## 快照准备

源码仓库没有完整生产数据。`python build.py` 在缺少必要输入时直接列出文件和文档入口，不自动联网抓取、不用合成数据冒充实际载具，也不触发全量物理计算。

网站构建必须有：

- `data/snapshots/catalog.json`：来源清晰的规范化目录。
- `data/snapshots/changes.json`：版本方案；无法获取来源时使用明确的不可用状态和空 entries，不能编造调整。
- `data/snapshots/target-layers.json`：目标抽样层，保存几何与材料身份。
- `reports/target-layers.json`：对应的抽样导出报告。

`data/snapshots/decision-probabilities.json` 是可选轻量积分摘要，缺失时保留显式无摘要状态。它与 10+GB 原参考弹热图的发布方式分开。

常用数据更新过程：

```bash
python -m wt_atlas sources --refresh --changes
# 按 CLIENT_BACKEND.md 完成客户端绑定、计算和导出后：
python scripts/build_target_layers.py --workers 6
python -m wt_atlas summarize
python build.py --probability-base-url https://data.example.com/probability/RELEASE_ID/
```

`--refresh` 和客户端命令会生成/替换本地数据。要使用现有数据，先备份或保留对应版本，不在生产托管机上运行这些采集与计算步骤。Windows 客户端提取工具的版本校验及实际目录参数见 [CLIENT_BACKEND.md](CLIENT_BACKEND.md)；参考四车 profile 流程见 [PIPELINE.md](PIPELINE.md)。

## 常用命令

| 命令 | 范围 |
|---|---|
| `npm run test:js` | 决策、摘要与数据客户端，无生产数据依赖 |
| `npm run test:python` | 后端数值与站点发布边界 |
| `npm run test:site` | 仅标准库站点构建测试；仍需 Node 生成推荐夹具 |
| `npm run test:smoke` | 临时合成站点、两个 HTTP 源、真实浏览器 |
| `npm run test:browser` | 使用生产快照的完整浏览器验收 |
| `python build.py --skip-decisions` | 只组装，仍拒绝过期推荐 |
| `python scripts/serve.py --port 8765 --no-browser` | 仅服务 `dist/site/` |
| `python scripts/serve.py --directory /path/to/extracted-site --no-browser` | 浏览解压后的静态包 |

`.github/workflows/ci.yml` 运行源码回归与合成浏览器测试，不抓取游戏资料、不重算全量 GRP、不发布网站。完整生产验收仍需维护者在真实快照上执行。历史 `test:browser` 要求本地 `embedded` 模式、完整热图及 `qa.json` 的服务地址，并会阻止 HTTPS 请求；不能直接用于默认 disabled 或外部 HTTPS 数据源。外部部署以双源冒烟测试加实际站点验证为准。

## 合成夹具的边界

`tests/site_fixture.py` 仅为自动化验证创建几辆显式标记的合成车辆、小网格和实际 Node 推荐输出，均位于临时目录。浏览器测试禁止除两个本地源以外的网络请求。体积隔离测试使用逻辑大小 11 GiB 的稀疏文件证明网站构建不会复制概率数据；它不是 11 GiB 真实计算、压缩率或 CDN 吞吐基准。

请勿把合成夹具生成到生产快照目录或上传为实际数据。测试可以证明加载与发布边界，不证明游戏模型校准、客户端覆盖数或完整毁伤模型。

## 修改约定

- 算法改动进入 `shared/` 或 `backend/wt_atlas/`；游戏参数、来源和经验权重进入配置/快照。
- 浏览器传输改动进入 `web/data-client.mjs`；二进制语义改动进入 `shared/probability-codec.mjs` 并更新概率契约。
- 发布路径或数据格式改动必须同步构建器、客户端、相关测试与部署文档。
- 不编辑 `dist/site/`、不提交 `data/exports/` 或计算缓存、不维护共享引擎的第二份源码。
- vendor 内容保留来源与哈希；需要替换时单独记录版本和来源审计。
