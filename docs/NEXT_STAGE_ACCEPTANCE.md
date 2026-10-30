# 最新继续实施验收（2026-10-30T12:05:14.842103+00:00）

当前已测试代码提交 `9331d2e75c56aa5cf90dad99d0091677b0407fc6`；本节及更新后的机器报告为最新结果。后面的 09e431a 记录作为历史基线保留。原工作区的 30 个修改文件和 11 个未跟踪文件仍留在原目录。

- 修复等待动作被误算为证据覆盖、正式榜任务证据未绑定具体队列的问题；恢复历史评估与本轮新增计算现在分别计数。
- 新增从源穿深表和独立绑定几何层重新计算原生距离的 CPU 路径，支持原子输出、内容哈希、版本隔离、损坏重算和分批恢复。
- 同一弹药多距离数据不再重复占候选位置；同距离冲突拒绝，缺失距离保持未知。真实双向 100/500/1000m 已计算 6 图，预检 6/6，432 条诊断；仅覆盖两辆车，不代表全服远距覆盖。两次生成耗时 11.178s / 7.596s，峰值 RSS 534,900,736 / 448,991,232 bytes。原 10m 概率图读取数为 0。
- 新增 `--run-sensitivity` 实际重跑五组扰动并校验完整来源。两车样例敏感性门禁未通过（无可发布榜）；合成回归也正确识别对敌方先手敏感的情形，未将测量失败改为通过。

| 本轮验证 | 实际结果 |
|---|---|
| Python 全套 | 132 项：131 通过，1 项 Windows symlink 能力限制跳过；38.773s |
| Python forecast 子集 | 79 通过，已计入全套 |
| Node.js | 开发分支 51 通过；实际研究站副本源码另 55 通过 |
| HTTP 冒烟 | 4 通过 |
| 预测 Playwright | 自动候选、版本、筛选、九组合、移动端、下钻、故障重试通过 |
| 真实组合站 / 真实研究站 | 5 / 11 项浏览器检查通过 |
| 原生 10m 子集回归 | 72 诊断；历史 6 检查点最大差 0；原始 32 文件 ZIP 仍缺失 |
| 新原生距离集成 | 6 张真实重算图，432 诊断，3 距离双向覆盖；正式榜 0 |

授权前旧发布目录受沙箱文件权限限制，首次重建在写站点阶段失败，已生成的搜索表保存在缓存；本轮在新目录恢复并重新校验来源。研究站从 90 个按哈希核对的源码/输入文件副本重建，并重新生成与当前原仓库研究引擎匹配的全部国家推荐，原文件保持不变。

当前全量有界构建：1790.540s，主进程峰值 RSS 755,965,952 bytes；实际执行计数 `{"current": {"restored_tables": 716, "computed_tables": 0, "new_evaluations": 0, "restored_evaluations": 1410}}`。97,421 张匹配图的来源核验仍执行，原始 44.17 GB 图集未进入站点。本轮与旧研究推荐重算并行，耗时不能直接视为独立吞吐对比；旧研究全国家重算 1041.340s、静态组装 28.899s。详细覆盖矩阵和此前冷/热性能保留在报告中。默认三场景覆盖仍为 52.85%；新增两车远距子集单列，未混入该全量覆盖比例。

当前 build key `3a389aaae149267783b68b9246fc5af627999429c2233ce19baf9cc311d6a42f`。统一站点 76,950,842 bytes；发布包 `dist/ground-atlas-next-stage-83c6faa.zip` 为 66,742,822 bytes，SHA-256 `0c205527ea941b46eeffd69919dc91dec5a43eb142c986912868004afd069f75`。ZIP CRC 和解压后的 5,446 文件 SHA-256 全部通过；初屏 loopback 101.40ms / 251,536 bytes，0 个 GRP/NPZ 请求。离线原始数据和缓存不在发布包内。

算法与诊断产品可以直接部署；数据门禁仍为 `algorithm_ready=true / data_ready=false / publishable=false`，正式排名 0 条、研究榜 0 条。未提供同版本官方 BR/挂载/SP、任务/毁伤或授权统计等来源时，不宣称空地最强、胜率或击毁率。公网部署目标/CDN 未配置，概率模式保持 disabled。部署命令、输入精确格式和回滚说明见 [产品文档](NEXT_STAGE_PRODUCT.md)，最新阻塞见 [release-readiness.json](../reports/release-readiness.json)。

用户授权后，4 个继续实施提交已通过普通 Git push 上传至 PR [#5](https://github.com/Dubnium-105/wt-ground-atlas/pull/5)。已确认提交 `24996fea47fbaa78c3da7083408fddaa02f63f46` 的两条远程工作流全部通过：[源码与轻量站点 CI](https://github.com/Dubnium-105/wt-ground-atlas/actions/runs/41053806658)、[预测合同与浏览器 CI](https://github.com/Dubnium-105/wt-ground-atlas/actions/runs/41053806579)。本次随后提交仅同步验收记录；产品代码、真实输入、build key 和发布包保持上述已验证版本。原先的网络及审批阻塞已解除，保留在机器报告的历史尝试字段中；公网托管目标、CDN 和真实数据缺口仍待外部输入。

---

## 09e431a 历史基线记录（以下数值不代表本轮最新发布包）

# 下一阶段实施与实测验收记录

本记录生成于 2026-10-30T08:25:15.987191+00:00，代码基线为 PR #4 合并提交 `465317f6ce0a7d3efb06297f1cb1cfac588ad905`，最终实现提交 `1beb5612534cecd51aced34744a5897f1ed2266e`。后续提交仅补充验收材料和私有数据忽略规则。开发分支 `feature/atlas-next-stage`，PR [#5](https://github.com/Dubnium-105/wt-ground-atlas/pull/5)。原目录的 30 个修改文件与 11 个未跟踪文件留在原工作区；使用独立 worktree，未 reset、stash、覆盖或强制推送。

实现已能构建、验源、搜索、生成统一静态站和验证发布包。算法与合成端到端通过，真实大数据流水线完成；**没有算出可发布的全服强势队列**。当前 `algorithm_ready=true / data_ready=false / publishable=false`，正式榜 0 条、研究榜 0 条。站点可部署为诚实的数据诊断和研究产品。

## 实现对应关系

| 合同模块 | 已交付代码与验证 |
|---|---|
| 数据发现、清单、来源绑定 | `client_paths.py`、`forecast/inventory.py`、`scripts/forecast_data.py`；SQLite 增量索引、GRP/profile/layer 独立 SHA 链、精确装备 ID、数组预检、方向/距离/角度覆盖矩阵、有限进程批次 |
| 物理、技能与同时事件 | 保留并扩展 `aim.py/combat.py/engine.py`；逐像素质量守恒、双向独立、同刻结算、技能积分、空间毁伤输入、shot/pair/mission/lineup 内容键及证据追踪 |
| BR、实际挂载与 SP | `capabilities.py` 和配置枚举；解锁与携带分离、完整挂载互斥/数量/质量/依赖、官方来源规则、同父车辆单槽、未知航空成本、阶段出击规划和替补边际贡献 |
| 自动完整队列搜索 | `search.py`；全国家/BR 自动发现、beam 与一/二槽交换、合法敌队采样、风险尾部、去重、预算截断、固定成员迁移/重组及变化归因；小世界穷举 oracle 回归 |
| 版本、更新与统计 | `versions.py/updates.py/statistics.py`；固定来源哈希、离线缓存更新、别名冲突、范围失效、隔离 planned 情景；授权统计按玩家/战局连通组及时间切分，NLL/Brier/校准/漂移，缺资料保持未校准 |
| 一个正式网站 | `build.py --forecast`、`scripts/build_product.py`、`web/forecast/`；预测为主入口，九种技能×任务预计算组合，分页/取消请求/重试/移动端，旧图鉴、对抗、防护研究在 `/research/` 下钻 |
| 发布与恢复 | 内容哈希和 gzip、统一清单、大小与路径检查、独立 staging 原子替换、`.previous` 回滚、ZIP CRC 与全部文件 SHA-256 验证；原始概率数据不进站点 |

PR #2/#3 的逐项纳入和弃用说明、输入字段、完整 CLI、缓存失效、部署与回滚见 [产品契约](NEXT_STAGE_PRODUCT.md)。机器模式见 [输入 schema](../schemas/forecast-input.schema.json)。新预测排名只调用 Python V3；旧研究引擎维持自身模型身份。提供的旧站 engine catalog 曾漏投影武器字段，已从该站相同快照的 nation 数据精确修复，并用真实浏览器验证在线/离线一致。

## 实际运行的验收

基线 `npm test`：Node 51/51；Python 88 项中 4 项因 Windows 默认 GBK 读取 UTF-8 而报错（23.370s，初始 RSS 未测，记为 null）；独立 forecast 35/35。已修复显式 UTF-8 文件和子进程解码，最终全套结果如下。

环境：Windows、Python 3.14.4、Node v22.19.0，Playwright Chromium 151.0.7922.34。依赖安装 `python -m pip install -r requirements.txt`、`npm ci` 成功。

| 实际命令 | 结果 |
|---|---|
| `npm test` | 116 Python + 51 Node.js，通过 |
| `python -m unittest discover -s tests -p "test_forecast*.py" -v` | 63，通过；属于上述 Python 总数 |
| `npm run test:smoke` | 4 HTTP 检查，通过；独立概率 origin、离线/故障恢复、子路径 |
| `node tests/forecast.e2e.mjs` | 合成自动榜、版本/国家、九组合切换、研究下钻、移动端、哈希错误重试，通过 |
| `node scripts/qa_research_http.mjs <原仓库> dist/site/research` | 11 项真实研究浏览器检查，通过 |
| `node tests/product_runtime.e2e.mjs dist/site` | 5 项最终真实组合站检查，通过 |
| `build_forecast.py --sample-root data/work/derived-native-sample --diagnostics ...` | 72 条、24 角×3 技能、0 榜，耗时 1.025s |
| `npm run build -- --output dist/no-input-site --forecast-output dist/no-input-forecast --reports reports/no-input` | 无输入诊断站 566,304 bytes，0 榜，通过 |
| `package_release.py` + 解压后 `verify_release.py` | ZIP 全部 CRC、5446 文件 SHA，通过 |

独立 CI 已在实现提交上实际通过：原项目 [run 41023492080](https://github.com/Dubnium-105/wt-ground-atlas/actions/runs/41023492080)、预测 [run 41023492272](https://github.com/Dubnium-105/wt-ground-atlas/actions/runs/41023492272)。最终材料提交的 CI 状态见 PR Checks。

原始 `analysis-sample-2.59.0.92.zip` 未找到，不能声称本次核验了原 32 文件归档。另从实际图集复制 JSON、NPZ、404 弹药档案（保持三个源 SHA 不变），提取 2 车目录并标为 sample，生成 **4 文件** manifest。单向 BMP-2M APDS-FS → Khalid、10m，72 条诊断；与已提交历史审计的 6 个检查点比较，全部数值最大差 0。这个真实回归子集不能代替缺失原 ZIP 的完整验收。

## 真实数据覆盖与性能

原始数据根通过 `WT_ATLAS_DATA_ROOT` 指向原仓库。实际发现客户端、jobs/GRP、目录、弹药和图集，具体相对路径及内容哈希在 [data-coverage.json](../reports/data-coverage.json)。客户端物理版本 2.59.0.92；Wiki 采集时间 2026-10-29T01:19:11.129331+00:00，但目录自身 `game_version=null`、`source_status=unknown`，不能把客户端版本填成 Wiki 已核验版本。

| 实测对象 | 结果 |
|---|---|
| 目录 | 2,679 车：1,226 地面、1,341 固定翼、112 直升机；10 国家；404 弹药档案 |
| 原始图集 | 220,202 JSON + 220,202 NPZ；NPZ 44,165,399,237 bytes |
| 当前匹配且已验证 | 97,421 图，16,853,831,074 bytes NPZ；1,226 个独立几何绑定，错误 0 |
| 排除历史/缺项 | 103,263 档案 SHA 不匹配、19,395 缺距离、123 缺档案；未伪造或覆盖它们 |
| 默认三场景需求 | 732,546 有向配对需求；531,387 正向；387,168 双向（52.85%）；165,744 缺自有弹药、201,159 缺方向/距离（分类有交集） |
| 距离 | 只有原生 10m；没有 100/500/1000m 的验收结果 |
| 初次元数据清点 | 883.706s，CPU 211.484s；总清点 889.053s |
| 全部匹配 NPZ 预检 | 97,421 张 / 360.618s（约 270 张/s）；含来源绑定共 391.000s；6 workers，1536 MiB 解码预算 |
| 最终真实端到端构建 | 1442.952s，恢复 716 张完整搜索表（记录 1,410 次既有评估，本轮未重做搜索）；单发缓存命中 0、未命中 0、按需解码 0 |
| 最终构建资源 | 主进程峰值 RSS 756,023,296 bytes，CPU 281.047s；不含 worker RSS |
| 1k 合成元数据 | 冷 3.420s / 热 0.583s，增量命中 100%，3 次按需 NPZ 读取 |
| 10k 合成元数据 | 冷 34.084s / 热 5.837s，增量命中 100%，3 次按需读取，峰值 RSS 90,353,664 bytes |
| 小世界搜索 | 预算 1 差距 19.3874 且截断；预算 4/64 实际 3 次评估达到穷举 oracle，两个种子重放一致 |

首次真实端到端耗时 1805.007s，实际执行 1,410 次队列评估，单发命中 6,326 / 未命中 19,123 / 按需 NPZ 解码 19,037；中间暖构建 1435.016s，复用 25,449 次单发缓存，重新完成队列搜索。最终一次在相同代码和输入上恢复 716 张完整表，705 张搜索预算截断，来源检查仍完整执行。最终日志的 evaluations 是恢复表的历史总数，不是本轮新增计算量。

真实端到端使用已提交 `config/forecast.audit.json`：全部已采集国家/合法 BR，5 槽、常规与全部获取范围，每表上限 2 次评估、8 个敌队、1 个积分节点、10m 正面。它验证全服发现和大数据执行链路，**不证明生产搜索充分或全局最优**。该单场景覆盖为 244,182 需求 / 129,056 双向；上表三场景覆盖不可与它混计。默认正式搜索预算和三场景配置保留，九技能/任务组合在合成世界完成端到端验证，未声称对现实数据完成九组充分搜索。

性能详见 [search-benchmark.json](../reports/search-benchmark.json)。旧研究站大目录自定义版本重算仍较慢：实测约 64.8s，旧版采用约 25.8s；自动预测主入口使用离线结果。Windows 无 `/proc`，浏览器 aggregate RSS 指标为 null；不捏造内存读数。

## 发布产物与可复现命令

最终 build key：`d4814526e594d7e6b730075bc71f546c1b75b2964e04f6de386debcf7e492187`。统一站点 75,988,359 bytes，5446 文件；本机 loopback 首页 ready 111.50ms、首屏传输 251,199 bytes、原始 GRP/NPZ 请求 0。网络和设备不同时延不可据此保证。

发布包：`dist/ground-atlas-next-stage.zip`，66,528,818 bytes，SHA-256 `2909923df89461a290d821959c24b9d3f94d80327a058f318b46764f486d7768`，5,447 entries。全部 CRC 和解压后文件哈希通过。静态部署检查通过，**没有发布到公网**：未提供目标托管站点或概率 CDN。当前组合研究快照概率模式为 disabled；浏览器验收额外通过本地只读 HTTP 挂载实际概率导出验证防护切片，未假称已配置公共 CDN。

```sh
# 提供输入后的一条正式入口；生产完整配置，不沿用有界验收配置
python build.py --forecast --catalog "$WT_ATLAS_DATA_ROOT/data/snapshots/catalog.json" --ammunition "$WT_ATLAS_DATA_ROOT/data/derived/grp_probability_v2/2.59.0.92/ammunition/all_resolved_profiles_final_with_apcr_gun_caliber.json" --atlas "$WT_ATLAS_DATA_ROOT/data/derived/grp_probability_v2/2.59.0.92/KE-weighted-opponents" --bindings data/cache/geometry-bindings.json --preflight --resume --all-profiles --research-site "$WT_ATLAS_DATA_ROOT/dist/site" --output dist/site
python scripts/package_release.py --site dist/site --output dist/ground-atlas-next-stage.zip
python scripts/verify_release.py --root dist/release-verification
```

PowerShell 使用 `$env:WT_ATLAS_DATA_ROOT`；geometry-bindings 先按产品文档 `forecast_data.py bind` 生成，每次运行重新验证独立证据。真实验收的完整参数保存在版本化 `config/forecast.audit.json`，构建和输入哈希在 [forecast-validation.json](../reports/forecast-validation.json)。生产数据、缓存、原始 GRP/NPZ、完整弹药与私有统计均不提交 Git，也不打包到站点。

## 明确的外部阻塞

详见 [release-readiness.json](../reports/release-readiness.json) 的 `required_external_inputs` 和实际门禁 `why_not_publishable`。下列路径是待提供输入的建议落点，**不是声称文件已存在**；字段和数组限制见产品契约及 schema。

- **original_sample** — `analysis-sample-2.59.0.92.zip`：Original 32-file manifest archive (expected historical ZIP SHA256 5a5dc1b5e4254f54491599505cbdb15a1292562c1dfc148849dcf270ce10b463); the derived 4-file local sample does not replace this acceptance.
- **snapshot_identity** — `data/raw/next-stage/snapshot-info.json`：snapshot_id, game_version, datamine_commit, wiki_collected_at, effective_date, source_status and sources URL/hash; independently establish release status and Wiki/client version alignment. Do not fill unknown fields by assumption.
- **physical_coverage** — `data/derived/grp_probability_v2/<version>/<atlas>/arrays/`：Missing forward/reverse owned-ammunition JSON+NPZ, exact ammo_profile_sha256, target_sha256 and independently bound GRP/profile/layer inputs, source_version/datamine_commit, array SHA/bytes, native distance and yaw. Native 100/500/1000 m maps are absent; 10 m cannot substitute.
- **official_configuration** — `data/snapshots/catalog.json`：Per-vehicle br_provenance.source/source_version; rating_configurations.ground_rb with official_verified source/version/options/allowlists; legal loadouts with source/source_version/source_status; spawn_rule.status/source/source_version/base_points/room_costs/respawn_multiplier.
- **missions** — `data/raw/next-stage/missions.json`：records: variant_key,target_vehicle_id,scenario_id,task,lower,mean,upper,source_version,source,status; validated evidence includes loadout_id,flight_conditions,radar_conditions,target_countermeasures,source_sha256. No maximum-range-to-hit-rate conversion.
- **damage** — `data/raw/next-stage/effects.json and referenced NPZ`：records: map_id,map_array_sha256,source,source_version,status,array_file,array_sha256. conditional_outcomes[angle,height,width,3] for firepower,mobility,termination; finite nonnegative mutually exclusive mass <=1. Required only to switch the endpoint to damage.
- **sensitivity** — `data/raw/next-stage/sensitivity.json`：Object with passed=true, catalog_sha256=digest(catalog), config_sha256=digest(effective runtime config), plus the measured sensitivity evidence. Digests use canonical JSON; the Boolean is accepted only with exact current input hashes.
- **statistics** — `data/private/authorized-statistics.json`：Authorized packet: source,sampling_window,game_version,mode,sample_count,deduplication,bias_notes,authorization,endpoint,observations; observations have observation_id,player_id,match_id,timestamp,stratum,game_version,prediction,outcome. Optional empirical calibration; missing keeps uncalibrated.
- **planned_version** — `data/raw/next-stage/scenario.json or a complete --planned-* snapshot`：Official announced/proposed next-version changes with sources and version identity, or explicitly hypothetical labels; no next-version source was supplied.
- **hosting** — `deployment target and versioned probability CDN URL`：No authorized public deployment target/CDN configured. Static ZIP is verified locally; remote live deployment is not performed.

未取得这些证据时仍能构建诊断站；不会用默认常数补成实测击毁率、胜率、远距物理或全服最强排名。没有下一版正式来源时不复制当前榜冒充下一版。统计与伤害输入缺失分别保持未校准及 penetration 端点。
