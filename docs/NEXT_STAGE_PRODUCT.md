# 下一阶段产品、输入与验收契约

预测首页使用 Python V3 的物理 → 落点 → 同时事件 → 任务 → SP 出击策略 → 队列组合搜索。旧 `shared/engine.mjs` 仅保留在 `/research/`，其分数不进入 V3 排序。分数是各自合法匹配环境中的条件效用，不是胜率、击毁率或不同 BR 的直接对决概率。

## 构建与诊断

Python 3.11+、Node 20+，先执行 `python -m pip install -r requirements.txt`、`npm ci`。以下路径都是用户提供的本地文件；生产数据不入 Git。

```sh
python scripts/forecast_data.py doctor --data-root /data/wt --out reports/doctor.json
python scripts/forecast_data.py bind --catalog /data/wt/data/snapshots/catalog.json --repository /data/wt --jobs /data/wt/data/work/client/jobs --out data/cache/bindings.json
python build.py --forecast --catalog /data/wt/data/snapshots/catalog.json --ammunition /data/wt/ammunition.json --atlas /data/wt/atlas --bindings data/cache/bindings.json --preflight --resume --max-workers 4 --memory-budget 1024 --all-profiles --research-site /data/wt/dist/site --reports reports --output dist/site
```

PowerShell 可以将以上命令各自放在一行；也接受 Windows 的 `D:/...` 路径。`WT_ATLAS_DATA_ROOT` 为未显式给路径时的发现根目录。只有唯一一组输入才自动选择；部分显式输入、多个候选或缺文件都不会猜测来源。客户端可用 `WT_CLIENT_ROOT` 或 `python -m wt_atlas client prepare --source ...`；自动检查 Steam 的常见 Windows/Linux 路径，无个人目录常量。

`npm run build` 默认进入预测产品；`npm run build:research` 和原来的 `python build.py` 保持旧研究构建兼容。`scripts/build_forecast.py` 仍可单独产生 `dist/forecast`。没有物理输入时也能构建诊断首页；没有 `--research-site` 时研究入口显示快照缺失说明。已有研究快照经验证后组合到 `/research/`，保留其页面代码、引擎与原始 manifest，仅注入共享路由和返回预测入口；这种组合不是把旧模型的分数混入新榜。

`--all-profiles` 预计算新手/普通/高手 × 纯地面/空地协同/航空支援九组结果，浏览器切换实际离线结果。代价约为九组搜索；共用索引和单发缓存。只需一组时可用 `--skill expert --preference ground`。UI 只提供本次实际构建的情景，不显示尚未计算的选项。槽位、获取范围、场景、先验、风险偏好均来自 `config/forecast.json`。

其他可执行选项：

| 选项 | 行为 |
|---|---|
| `--nations usa,germany --br-range 8.0 10.3 --slots 5 8` | 限定实际枚举的国家、BR 和槽位；依然自动生成队列 |
| `--max-evaluations 20000` | 每国家/BR/槽位/获取范围的队列评估上限；截断表不入正式或研究榜 |
| `--dry-run` | 验证配置、输入并报告依赖变化，不写站点；可能更新元数据缓存 |
| `--resume` | 复用有校验和的完整搜索表；重验源 NPZ，避免热缓存隐藏文件损坏 |
| `--force` | 忽略索引与搜索表的增量命中 |
| `--max-workers --memory-budget` | NPZ 预检进程数与解码负载预算（MiB）；不是全 Python 进程 RSS 的硬限制，队列搜索目前在主进程运行 |
| `--sample-root ... --diagnostics` | 样例 manifest 全量验证、原生距离逐角逐技能诊断；样例永远不能发布全服榜 |
| `--scenario ...` | BR、性能、配置等变化的独立情景；released 变化推进基线，不复制为未来榜 |
| `--planned-catalog --planned-ammunition --planned-atlas` | 完整的第二物理快照，三项必须一起给；不能同时混用 patch |
| `--planned-bindings --planned-effects --planned-missions` | 第二快照各自的独立证据；不跨版本推断 |
| `--snapshot-info` | 合并明确提供的快照身份补充；不会绕过物理版本及来源校验 |
| `--effects --missions --community --sensitivity` | 空间毁伤、任务证据、定性社区资料、与本次输入哈希绑定的敏感性报告 |

大型预检也可分阶段运行 `forecast_data.py inventory` / `preflight`。`verify_indexed_forecast.py` 专门续验已清点的数据库，不重扫元数据；元数据改变后先重新 inventory。`--limit` 只用于分批预检，不代表全部完成。

## 数据契约

机器可读摘要见 [forecast-input.schema.json](../schemas/forecast-input.schema.json)，实际数值/身份/物理约束仍由运行时验证器执行。参考可执行 fixture 在 `tests/test_forecast*.py`，其中所有游戏属性均为合成值。

目录需有 `vehicles[]`、`snapshot`。快照使用 `snapshot_id/game_version/datamine_commit/wiki_collected_at/effective_date/source_status/sources[]`；兼容现有 `id/client_source.version/datamine_enrichment.ref/collected_at/captures`。状态只接受 released、announced、proposed、hypothetical、unknown。采集日期不等于生效日期，不能自动从文件名填正式状态。当前生产门禁要求 released 的同版本基线；未实装情景明确保留研究身份。

每车保留 `id/nation/kind/role/wiki_role/source_attributes`；地面 MBT 是中坦子类，直升机无核验子类时为 unknown。固定翼 Ground RB 从官方 `trb` 获取，地面/直升机使用核验的对应 RB 字段，原始字段由现有采集器保留。任务能力和目录分类互相独立。

弹药 profiles 保持 V2 数组形式。必须有 `name/mode/distance_m/provenance`；mode 仅 KE/CE，provenance 中的精确 `penetration_source_ammo_ids` 或既有受支持的 ID 来源负责装备归属。每图 JSON 的 `map_id/target_id/target_sha256/ammo_id/ammo_profile_sha256/source_version/datamine_commit/array_file/array_sha256` 与原文件对应。NPZ 必须通过原 V3 的 dtype、shape、status、有限数值、像素几何与精确 yaw 校验。没有距离的 CE 档案不能被当成 10m；相似名称、口径、国家和 reference_slice 不能建立装备归属。

覆盖矩阵按地面父车辆的合法 BR 邻域、配置场景、精确自有弹药、距离和已核验 yaw 统计有向需求；它不是真实排队频率或所有 BR 配置组合数。合法配置组合在搜索表分别枚举。没有读取角度的元数据不会被算作可计算方向。统计区分元数据选择、数组核验、独立几何绑定三层；不能把目录文件数称为已验证地图数。

独立几何绑定读取 jobs 中的 `vehicle_id/status/profile_path/layer_metadata/raw_path/profile_sha256/grp_sha256`；逐个核验原始 GRP、profile、layer NPZ 的 SHA，核对目录的 geometry/configuration SHA 和 profile 的版本，然后用生成器的完整 tuple 重建 target SHA：

```text
vehicle_id, geometry_sha256, configuration_sha256, layers_sha256,
client_version, datamine_commit, plan
```

`--bindings` 文件中的 repository/jobs 只指定证据位置；每次重新读取物理证据，不直接信任 JSON 中“verified”标签。缺少链条的对象保持 source_declared_id_version_only。BR 修改不改变物理目标 configuration_id。

### 自选 BR 与实际携带配置

`rating_configurations.ground_rb` 包含 `status/source.url/source_version/options[]`；option 包含 `id/br.current` 和明确的 `allowed_ammo_ids/allowed_loadout_ids/allowed_weapon_ids` 策略。official_verified 才能作为当前规则，announced/proposed/hypothetical 只能在同标签情景中枚举。每车 `br_provenance.source/source_version` 是生产来源要求。

航空 `loadouts[]` 包含 `id/legal/weapons[]`；weapon 包含 `id/count/station`，支持 `mass_kg/requires/conflicts_with`，loadout/vehicle 可声明 `max_payload_kg`。生产还要求 loadout 的 `source/source_version/source_status=official_verified`。真实携带与 `ownership.unlocked_modifications_by_vehicle` 分离；解锁不改变 BR。多车系统的 system_id 共用槽位，component 不独立入队。

`spawn_rule` 包含 `status/source/source_version/base_points/room_costs/respawn_multiplier`。room_costs 的键为房间顶级与配置 BR 的步数差。无法核验的规则必须 assumed；未知航空成本保持 null，永远不按免费处理。出击策略按阶段 SP、同域已出击次数和剩余成员进行动态规划，可等待、保留替补；边际贡献是在原房间中禁用一个成员后的期望效用差。

### 任务、毁伤与校准

`--missions` 可省略，或提供空 `records[]`。按用户于 2026-10-31 确认的策略，默认配置启用 `mission_policy.mode=assumed_defaults`：缺少非地面物理任务记录时使用 `default_interval={lower:0,mean:0.5,upper:1}`，状态固定为 `assumed_not_observed`。0.5 是可配置的中性先验，不是命中率、胜率或实测效果。默认任务占比、真实证据覆盖与独立地面物理覆盖分开输出；任务先验不能补齐缺失的物理图。已有有效记录优先，记录更新及先验变化均进入缓存身份。`ground` 仍直接使用双向物理交战模型。

后续网站用户反馈与其他来源可整理成 `--missions` 的 `records[]`。每条要求 `variant_key/target_vehicle_id/scenario_id/task/lower/mean/upper/source_version/source/status`。合法 task 为 ground、anti_tank、ground_control、recon、anti_helicopter、anti_air、cap、cas、sead；数值必须有序且在 [0,1]。计入已核验生产任务证据还要求 evidence 的 `loadout_id/flight_conditions/radar_conditions/target_countermeasures/source_sha256`。条件对象中记录导引头、包线、飞行状态、雷达与反制适用性；`measurement=maximum_range` 不能充当任务效果。默认策略不放宽导入记录的来源、版本和数值检查。

需要严格证据准入时设置 `mission_policy.mode=require_evidence`；旧配置未声明此段时保留严格行为。默认模式不再因缺少任务记录而单独阻断条件模型排名，但其他来源、物理覆盖、任务建模、SP、挂载、搜索及敏感性门禁继续有效。实战记录可留空，校准保持 `uncalibrated`，不自动收集或上传用户反馈。

`--effects` 同样使用 records。每条为 `map_id/map_array_sha256/source/source_version/status`，以及 `array_file/array_sha256`。NPZ `conditional_outcomes` 形状为 `[angle,height,width,3]`，三个互斥增量为火力失能、机动失能、终止，总和 ≤1。validated 必须与原图和版本匹配；`constant:[...]` 仅允许 assumed 的敏感性核。缺核保留未知区间，penetration 不会被改名 damage。

`scripts/calibrate_forecast.py` 接收授权统计 packet：`source/sampling_window/game_version/mode/sample_count/deduplication/bias_notes/authorization/endpoint/observations`。observation 需 `observation_id/player_id/match_id/timestamp/stratum/game_version/prediction/outcome`；时间带时区，outcome 为 0/1 的同一端点。连接共享玩家或战局的样本，再按时间分 train/validation/test；跨边界组丢弃并计数。报告分层 logistic 参数、NLL、Brier、校准曲线、组 bootstrap 区间与漂移，不自动把配置改成 validated。原 `forecast/calibrate.py` 继续支持落点模型拟合。没有实战数据就保持 uncalibrated。

社区 packet 使用同样的来源字段和 `annotations[]`，每条 `vehicle_id/source_url/labels/disputes`；赞数不会作为效用权重，资料展示和排序隔离。

### 配置缺失与有界数组读取

缺少精确自有弹药绑定或合法实际航空挂载的载具仍保留在敌方未知总体中，但不能被搜索器选入己方队列补齐槽位。拒绝记录分别标注 `unknown_owned_ammunition_configuration` / `no_verified_legal_carried_loadout`，并注明 `opponent_population_retained=true`。完整队列的所有成员必须有可选配置。

物理图与空间毁伤核共用 NPZ 读取器：在 NumPy 分配前检查 ZIP 展开大小、重复成员、NPY 头声明的形状与实际数据长度，拒绝对象数组和超预算输入。毁伤核检查全部角度的形状、浮点类型、有限非负互斥概率质量，而非仅检查本次访问的切片。读取器、物理适配和毁伤代码哈希进入索引、单发及构建身份，旧缓存不能掩盖校验规则变化。

## 缓存、更新与发布

SQLite 分开存元数据、单发、完整搜索表和证据节点。元数据按目标/弹药输入内容哈希失效；BR 改动保留物理图，几何、材料或弹药变化只影响相关身份。NPZ 按需读一个图，预检使用有限批次 ProcessPool。单发/派生缓存有内容校验和，坏缓存删除重算。恢复完整表前重验源文件与毁伤文件，不能跳过来源检查。

`python scripts/trace_forecast.py --cache data/cache/forecast.sqlite --key HASH` 查询 shot/pair/mission/lineup 节点。网站详情给出任务/队列哈希；展开时才下载对应表。缓存属于离线审计产物，不放到站点。

`scripts/update_forecast.py --manifest ... --before ... --after ...` 支持本地捕获或 HTTPS 官方来源，Datamine URL 必须包含固定 40 位 commit。每项 source 有 kind、game_version、sha256，以及相对 path 或 url。无网络可 `--offline` 复用校验过的缓存。别名冲突会报告，绝不模糊匹配。若本地快照和公告的版本关系不完整，获取成功也不表示可发布。

统一站点以 `index.json` 和 `data/manifest.json` 为发布根；结果 JSON 采用内容哈希名称，附 gzip 摘要。根清单及 HTML 使用 revalidate/no-cache；hash 文件 immutable。跨域概率数据继续使用现有 `deploy/` CORS 配置和版本化数据根，只在研究页打开某图后请求切片。初屏不请求 GRP/NPZ。

生成器先写独立 staging，验证所有 descriptor、压缩对应关系、大小、路径与符号链接，再原子替换。多情景也是全部成功后替换。统一产品将旧站保存在 `dist/site.previous`，只有带发布 marker 的目录才能替换。默认预测预算：总量 100MiB、单文件 8MiB、gzip 单分片 2MiB；统一站使用 deployment.json 的整站预算。

```sh
python scripts/package_release.py --site dist/site --output dist/ground-atlas.zip
# 解压到全新的目录后：
python scripts/verify_release.py --root /path/to/extracted/site
```

只有校验通过的 ZIP 或 dist/site 内容部署到静态托管。发布根须与大概率存储隔离。回滚时停止写入，检查 `.previous` 的 marker/manifest/哈希，先将当前站移到另一个同级目录，再将 `.previous` 原子重命名为原站点；部署记录保留 build_key、源快照与模型哈希。不要覆盖离线输入目录。

## 门禁与本次数据限制

release-readiness 独立列 algorithm_ready、data_ready、publishable。正式榜需要同版本来源、独立物理绑定、全部必要数组验证、双向覆盖、完整任务和挂载来源、官方 SP、未截断搜索、与 catalog/config 哈希绑定的敏感性报告。未校准不能声称实战验证；model_error 和 out_of_sample_confidence_interval 保持 null，不用物理缺项上下界替代。

`config/forecast.audit.json` 是实测大数据流水线的有界验收配置：所有国家/BR、5 槽、两种获取范围，每表 2 次搜索、8 个采样敌队、1 个积分节点、10m 正面。它验证真实端到端吞吐，不用于强势推荐；截断结果不进榜。默认模型仍是完整配置。运行结果和原始样例缺口见四份 `reports/*.json` 与 [实施验收记录](NEXT_STAGE_ACCEPTANCE.md)。

## PR #2 / #3 的处理

| 来源 | 纳入 V3 | 未合并部分与原因 |
|---|---|---|
| PR #2 `rating-configurations.mjs` | 官方状态/来源契约、allowlist、解锁与携带分离、配置 BR 进入身份键、互斥检查 | 未直接合并旧分支或复制其 JS 排序路径；Python 枚举器处理实际完整挂载并用 V3 效用搜索 |
| PR #3 `meta-rankings.mjs` | 国家/BR 自动发现、实际队列 BR、固定成员版本迁移、去重、多样性、前后重组对照 | 旧线性打分榜不进入生产；没有数据的预设全服排名和固定国家加分未采用 |

旧研究功能保留自己的模型标签，原工作目录的未提交改动不被修改，也没有无条件并入当前开发分支。

## 统一站点的目录一致性

组合 `--research-site` 时，研究站必须由与预测 `--catalog` 完全相同的目录文件构建。发布器核对预测报告、研究清单和每个推荐分片的目录 SHA-256；只要有一个不同，就拒绝替换已有站点。旧研究清单没有目录哈希时，可从已校验的推荐分片恢复该身份。此校验记录为 `research.catalog_alignment`，不改变两个模型各自的含义。更新 BR 目录后，应先重新生成研究推荐与静态站，再组合；不能把旧目录的页面接到新 BR 推荐后面。

## 逐队列发布证据（后续审计修正）

等待出击具有已知的零收益，但不产生任务观测。出击策略分别返回 model_state_coverage、evidence_coverage 和 assumed_mission_coverage。严格策略按实际证据准入；默认策略可使用显式任务假设，同时独立检查 physical_pair_coverage，避免用先验填补缺失物理方向。production_mission_coverage 仍仅统计 validated 且提供 delivery evidence 的任务记录。全局存在某类任务记录不能替代入榜队列自己的证据。

完整模式配置必须明确设置 publication.scope=full_ground_rb、publication.required_tasks，并把所有所需任务放进正权重的 spawn.phases；默认配置维持原生距离地面研究身份。完整模式对每个入榜队列逐任务检查 minimum_pair_coverage；假设性或未配置的任务不放行。缺少官方 SP/BR、版本身份等其他门禁仍独立阻止发布。

构建命令的 search_execution 是实际本次执行记录：computed_tables / restored_tables / new_evaluations / restored_evaluations。原 evaluations 字段表示产物内搜索表所记录的总评估数，恢复时不能将其当成本轮计算量。执行耗时和命中计数不参与发布内容哈希。

## 源原生距离重算与多距离弹药（2026-10-30 追加）

`scripts/rebuild_range_maps.py` 从独立核验的 GRP、构型和有序几何层重新执行 CPU 物理模型。只接受同版本 KE 自有弹药源穿深表的精确距离节点，不读取、缩放或反推已有概率图。新增距离以第一层装甲交点为参考，仍是已声明限制的直线路径条件模型，不是游戏实测弹道或击毁率。无 `impact_distance_m` 的旧档案保持原算法输出。

```sh
python scripts/rebuild_range_maps.py --repository "$WT_ATLAS_DATA_ROOT" --catalog "$WT_ATLAS_DATA_ROOT/data/snapshots/catalog.json" --ammunition "$WT_ATLAS_DATA_ROOT/data/derived/grp_probability_v2/2.59.0.92/ammunition/all_resolved_profiles_final_with_apcr_gun_caliber.json" --target uk_khalid --profile ammo_30166cc90bd7c43e5753 --distances 100 500 1000 --out data/derived/native-ranges-2.59.0.92 --resume --limit 3
```

输出 `ammunition.json`、`arrays/*.json+npz`、模型来源标记、`events.jsonl` 与 `run.json`。生成目录可直接作为 `--atlas`；使用其配套弹药清单及独立绑定文件完成 preflight。模型/数值配置变化使用新输出目录，弹药源变化使用新的内容哈希；损坏输出在 resume 时重新计算。`--limit` 限制本轮新增图，恢复任务会越过已完成图继续推进。内存预算约束解码单个几何/数组大小，不宣称等于进程 RSS 上限。

同一 `(vehicle, BR configuration, loadout, ammo)` 仅占一个候选配置位置。该弹药的多距离档案归入 `profiles`，按场景精确距离选择，证据记录每个距离的档案哈希；同弹药同距离的冲突档案拒绝进入搜索。缺失距离返回未知，不复制邻近距离。

## 可执行敏感性验收

`build_forecast.py --run-sensitivity` 在基线搜索后实际重跑五组条件：横纵瞄准误差各缩小/扩大 20%、敌方技能均值增加 0.5、搜索预算翻倍、队列采样种子切换。报告保存在 forecast validation 的 `sensitivity` 字段，含每次评估/恢复次数、候选重合度和最大效用变化。

扰动幅度和通过阈值均来自版本化的 `sensitivity` 配置：`aim_narrower_factor/aim_wider_factor/opponent_skill_mean_shift/search_budget_multiplier/seed_offset` 控制实际重跑；`thresholds.minimum_overlap/maximum_score_delta` 控制准入。随仓库提供的默认政策为每个非空视图候选重合度至少 0.5、共有候选效用变化不超过 10 分，且不能有空榜或截断搜索；这不是实测误差或统计置信度。失败不会阻止诊断站构建，只阻止正式榜发布。外部 `--sensitivity` 必须提供同结构的完整测量报告，绑定目录、有效配置、物理图、任务/毁伤与代码输入及内容校验和；仅 `passed: true` 不再通过。使用 `--run-sensitivity` 可在离线本机生成该报告，不依赖外部授权数据。

真实研究站验收可使用 `node scripts/qa_research_http.mjs <与推荐缓存一致的源码/数据根> <研究站目录> <独立概率导出目录>`。第三个参数可选，默认使用数据根下的 `data/exports/probability`。这样可以在隔离副本中重新生成旧研究推荐并核对 Worker 结果，同时只读原有大型概率导出；临时 HTTP 清单仅用于验收，不会写回站点或宣称已配置生产 CDN。

## 独立榜单视图与版本变化筛选

全局代表榜的 `global_limit` 只限制该榜本身。“各 BR 最佳”和“各国最佳”从全部未截断搜索表独立选择；同一父载具在不同合法 BR 配置中可以分别代表各 BR。纯地面、空地协同和抗升房各自保留搜索中实际评估过的分类候选，不从全局 Top 100 再过滤。它们仍受实际搜索预算和证据覆盖约束，不保证全局最优。

各视图发布为内容哈希 JSON/gzip 分片，切换时才读取。来源门禁检查所有可通过国家/BR 筛选访问的候选，敏感性测量覆盖所有非空视图。样例、未知分数和截断结果不能借分类入口进入正式榜。

版本变化跟随槽位、获取范围、国家、当前/新版本 BR 筛选，并支持上升、衰退、不可迁移的独立筛选和分页。对照展示原队列、保留成员迁移、同新版 BR 重组及具体配置；已移除配置保留原筛选上下文，截断搜索不展示已核验的最优变化值。

敏感性报告保存完整政策及有效配置哈希。修改阈值、扰动或数据后，旧报告不能继续放行；政策声明为 `assumed_robustness_policy`，不代表实战校准。旧配置缺少该节时仍可构建资料诊断，但 `--run-sensitivity` 要求先显式补充政策，不使用代码中的隐含默认阈值。

槽位数现在约束完整入榜队列：选择 N 槽时，候选必须包含 N 个不同父载具/多车系统。部分队列只用于搜索扩展；未使用的替补保留真实成员身份和零边际值，不虚构出击收益。合法独立成员不足时返回 `insufficient_distinct_members` 和 `available_parent_groups`，国家/BR 页面展示无法组成该槽位队列的具体原因。

版本对照的 `migrations` 清单只携带国家/槽位/获取范围、前后 BR、变化值和完整记录描述符；成员与归因按国家及原 BR 分片。浏览器仅下载当前页引用的记录，翻页或筛选会取消旧请求。统一发布验证同时校验这些分片的原始/压缩大小与哈希，坏分片不能替换已有站点。

## 自动刷新当前 Wiki 目录

```sh
python scripts/update_forecast.py --refresh-wiki --before /data/wt/data/snapshots/catalog.json --catalog-output data/snapshots/catalog.current-wiki.json --cache data/cache/current-wiki --out reports/current-wiki-refresh.json
```

该命令实际获取地面、固定翼和直升机三个目录，校验并缓存内容哈希，再按精确载具 ID 生成独立的新目录。固定翼只使用 Ground RB `trb`。当前 BR 与已采集的树位置、获取范围一起更新，原 `planned` 值重置为当前捕获值，不把已经生效的调整继续当下一版。新增载具保留缺少物理/弹药数据的状态；目录消失的 ID 标为本次未观测，不能因此断言它已从游戏删除。

旧目录及原始 GRP/NPZ 不被覆盖。相同 ID 的弹药、几何、物理构型和客户端来源只按原记录保留；国家/平台冲突直接拒绝。目录页不能证明旧航空预设、自选 BR 规则和 SP 仍然合法，因此旧规则保留为审计证据并暂停进入当前搜索，等待当前版本的完整挂载/规则来源重新验证。未知配置仍以未知状态保留在对手环境中。

Wiki 不提供游戏版本标记，刷新成功仍为 `source_status=unknown`，不会填入本地客户端版本或打开正式榜。`--offline` 使用上次完整且哈希校验通过的捕获，保留原采集时间；`--dry-run` 只核对缓存并输出差异，不改写目录。任意一个目录抓取失败时，旧完整捕获清单与输出目录保持原样。`config/sources.json` 的 `wiki.catalog_max_bytes` 控制单目录响应上限。

## 按来源声明的出击类别累计重生费用

`spawn_rule` 新增 `spawn_group`、`respawn_model="power_of_count"`、`respawn_power`。这一模型使用 `(1 + 同类别先前出击次数) ** respawn_power`，对应固定客户端脚本 `get_spawn_score_type_mul`。实际携带挂载上的规则优先于车辆规则；制空与对地挂载可以声明不同类别，不再被统一的 `air` 计数误加价。车辆所属平台和出击计费类别是不同字段。

出处：[2.59.0.92 固定脚本](https://github.com/gszabi99/War-Thunder-Datamine/blob/af0cce561a73704e30c1df66eff06209bcc3f035/gui.vromfs.bin_u/globals/ranks_common_shared.nut)，捕获 SHA-256 `f4ba585b2260289bb7eb4b9e8d62b9357b6c1d6e36e7a4b44a67c21660008c9e`。脚本中的类别可受任务与实际武器影响，因此导入规则必须给出已核对的 `spawn_group`；不会仅凭车辆分类自动标为官方规则。基础费用、房间修正、武器加价和任务覆盖仍须独立验证。既有 `respawn_multiplier` 保持原语义，两种计数模型不可同时声明。

## 固定版本 Ground RB 规则导入

```sh
python scripts/import_rule_snapshot.py --catalog data/snapshots/catalog.current-wiki.json --source-root data/raw/pinned-rules/2423f3099e2a5ec3f31efdce87b9f4380ea761ec --commit 2423f3099e2a5ec3f31efdce87b9f4380ea761ec --fetch --catalog-output data/snapshots/catalog.rules-2.59.0.94.json --out reports/formal-rule-import.json
```

下载固定提交的版本、wpcost、warpoints 和客户端 BR 选择脚本，逐文件检查 Git blob 身份、大小和 SHA-256。中断后可复用匹配 Git 对象的文件，全部成功才发布捕获清单；`--offline` 严格重放完整捕获。脚本语义哈希未知时拒绝导入。可选 `--client-wpcost <解包后的 wpcost.blk>` 逐记录比较本地客户端规则，不把不同版本的本地文件标成一致。

Ground RB 按客户端 `economicRankTankHistorical` 优先、`economicRankHistorical` 回退规则转换。仅接受精确 ID 或唯一 ASCII 大小写差异，拒绝大小写碰撞和国家冲突。无 BR 的不可选组件维持原样；独立的未来计划不被当前规则覆盖。输出另存，不修改输入目录或给物理数据更换版本。规则来源已核验不等于 GRP、弹药、挂载、任务效果与发布身份均已核验。

本次真实导入核对 2,680 条目录：2,650 一致，26 个不可独立选用组件保留空值，4 条 Ground RB 从 6.7 改为 6.3（N1K2-J、N1K2-Ja、Spitfire F Mk XVIIIe、Yak-3U）。完整逐车审计写入本地报告；摘要与源哈希保存在 `reports/formal-gap-progress.json`。2.59.0.92 本地 char 配置与其固定源的 3,437 条记录一致，但这不证明 2.59.0.94 物理兼容性。

导入还提取 `custom_presets.slot` 的精确挂点、容器数量、`DependentWeaponPreset` 与 `BannedWeaponPreset`，包括 BLK 同名字段形成的多个挂点引用。载具上的 `mount_rule` 直接参与优化器的挂载合法性检查；重复挂点、未定义预设、缺少依赖和互斥组合均拒绝。未知源结构或悬空预设引用单独报告，不能忽略后按合法组合计算。

实际 2.59.0.94 审计：726 辆载具导入挂点约束，7,453 个旧组合通过约束检查，5 个组合不符，24 辆载具的源约束有悬空引用。F-102A 的 AIM-26B 与 FFAR 冲突已用真实源验证拒绝。约束通过只证明此次检查覆盖的挂点关系，不代表旧嵌套武器、固定内置武器、完整载荷、SP 或任务效用已验证，因此导入不把隔离挂载改成 `legal` 或 `official_verified`。见 `reports/formal-mount-validation.json`。

## 原始样例归档验收

`doctor` 另列 `sample_archives`，检查数据根及常用 `data/exports` 位置中的 `analysis-sample*.zip`，不会自动解压或把发现等同于验证。此次在原数据仓库的 `data/exports/analysis-sample-2.59.0.92.zip` 找到原归档，ZIP 哈希匹配合同；32 个 manifest 文件校验通过、源哈希未变，72 条诊断与 6 项历史检查点完全一致。见 `reports/original32-sample-verification.json`。该样例仍只有一个方向、原生 10 m，产生 0 条正式排名。

## 精确历史弹药档案与注记差异

旧图可以通过显式 `--profile-history <完整历史弹药 JSON>` 核对其原始档案。参数可重复，用于 `forecast_data.py inventory/preflight`、`build_forecast.py` 及统一 `build.py --forecast`；独立下一版使用自己的 `--planned-profile-history`。不提供历史档案时，任何档案哈希不符仍拒绝。

核对先要求历史完整 JSON 的哈希精确等于物理图声明的 `ammo_profile_sha256`，再比较历史与当前完整档案。只允许空的 `catalog_physical_mismatches`、空的 `v2_equivalent_candidates` 注记，以及两种已定义精确匹配方法的描述变化。所有其他字段必须逐字规范化一致，包括弹药 ID、全部装备归属、源武器及其哈希、游戏版本、Datamine commit、距离、口径、穿深、斜率、跳弹曲线、机制、模型假设和未来新增字段。缺少历史全文、距离未知、非空歧义、参数或归属不同都不会通过。

原始 JSON、NPZ、原图身份和原档案哈希均不修改。索引保存两份完整档案的哈希及历史文件哈希，shot 追踪同时输出 `source_profile_sha256`、`active_profile_sha256`、`profile_reconciliation`。改变或撤去历史输入会使索引和构建缓存失效；NPZ 校验、独立 GRP 绑定、版本隔离、双向覆盖与正式发布门禁照常执行。历史核对不是跨版本兼容证明，也不允许把近距图变成长距图。

首次实盘审计在 103,263 张档案哈希不符的图中定位到 49,215 张仅含上述注记变化的图；其他 54,048 张继续保留拒绝。这个数量是可核对元数据，不等于已通过 NPZ 验收的图数。最终可用数以对应 preflight 报告为准。

全目录检查进一步发现，这 49,215 张历史图均已有当前档案对应的副本，声明的数组 SHA-256 相同，因此不能计为新增覆盖。选择器仅在存在唯一当前完整档案、历史注记证明有效、数组哈希及其余模型/目标/来源字段完全相同时保留当前图。历史副本在索引中标为 `historical_duplicate`，单列 `historical_duplicate_maps`，不计入已验证图数。选择后仍必须对当前 NPZ 执行完整校验；当前图损坏不会自动改选历史图。两个当前图、只有历史档案的重复图，或任意模型、数组和未知字段冲突仍按歧义隔离。撤去当前元数据后，历史图才可按普通独立输入重新验收。

重复档案修正新增三项回归：当前覆盖在增量扫描中保持、损坏不回退且显式撤去当前元数据可恢复历史验收；数组/模型/未来字段冲突拒绝；两个当前图即便字节相同也不自动择优。全套 `npm test` 为 58 项 Node.js 和 183 项 Python 测试。Windows 上 11 GiB 发布边界测试会实际分配空间，避免并行重复启动全套测试，可将该命令的 `TEMP`/`TMP` 指向空间充足的临时目录。

## 自动补算已有完整输入的原生配对

```sh
python scripts/plan_native_rebuild.py --catalog /data/catalog.json --ammunition /data/ammunition.json --index data/cache/verified.sqlite --config config/forecast.audit.json --out reports/native-rebuild-plan.json
python scripts/rebuild_native_maps.py --repository /data/source-repository --catalog /data/catalog.json --ammunition /data/ammunition.json --requests reports/native-rebuild-plan.json --out /data/new-native-maps --backend cpu --resume
```

规划器只读已经完成数组验收的索引，自动枚举合法 BR 邻域中的跨国有向配对，检查精确自有弹药、原生距离和 yaw。输出去重后的目标/弹药任务、实际装备方 ID 和所需角度；没有自有原生档案的方向单列，不能通过别车弹药填充。它不接收人工队列，也不会把待计算任务计入覆盖。

重算器先校验请求中的目录与弹药文件哈希，重新绑定原始 GRP、配置和有序层数据，再用完整原始档案计算。输出必须位于源仓库之外，单目录限一个物理源版本；未知距离、参考弹、伪标远距、非自有弹药、缺角度或变更几何都会拒绝。缺角度需要先重建几何，不能把其他 yaw 改名。支持有界解码、原子 NPZ/元数据写入、`--limit` 分批与 `--resume` 完整数组复核；损坏生成缓存会重算。输入概率图读取数始终为 0。

默认使用现有 CPU 层模型；已安装 CuPy/CUDA 的环境可显式选择 `--backend v2-cuda`，调用仓库现有 V2 CUDA 实现。两种后端保留各自代码和数值配置身份，不伪造相同哈希。当前检出的 CRLF 与历史 LF 代码字节不同，因此即使数组数值完全一致，也分别记录代码、map 和 NPZ 哈希。输出仍是条件穿透模型，不能标为实测击毁率。

真实缺图规划发现 309 个方向已有自有原生档案，归并为 358 个任务；另外 64,836 个方向缺少该距离的精确自有档案。首轮 CUDA 重算完成全部 358 张，用时 124.229 秒、父进程峰值 RSS 1,100,562,432 字节。两组历史样本的全部数组数值一致，原始文件未修改。新结果必须与既有图集一起再次验收后才更新产品覆盖；单独生成这些图不解除全模式任务效果、SP、当前版本一致性和搜索收敛门槛。

使用 `python scripts/assemble_native_atlas.py --base-index <已完成 preflight 的索引> --ammunition <完整原始弹药文件> --extra <新图目录> --out <不存在的新目录>` 原子组合数据。组合前复核基础元数据哈希/数组文件状态，并完整校验新增 NPZ；跨物理版本和重复物理身份直接拒绝。默认同卷硬链接，不改动来源；跨卷回退复制，`--copy` 可强制独立副本。硬链接属于离线工作数据而非不可变副本，来源的后续变化必须由 preflight/读取器重新校验。输出明确为 `assembled_requires_fresh_preflight`，需对新目录重新运行 `forecast_data.py preflight`，不能直接据目录文件数量宣称覆盖。

## 指定距离的 CUDA 重算与客户端破甲弹来源

`prepare_range_profiles.py` 保留全部原始档案，追加指定距离的派生档案。动能弹只接受源穿深曲线的精确节点；破甲弹要求提供解包后的 aces 目录，核对武器文件哈希、精确弹名、弹种和 `cumulativeDamage.armorPower`，全部一致后才生成条件穿透档案。不把固定聚能穿深解释为远距离命中率，也不导入穿后毁伤。暂不支持的嵌套火箭结构、参数不一致或缺少源文件均写入逐档案拒绝清单。

```sh
python scripts/prepare_range_profiles.py --ammunition /data/original-ammunition.json --source-root /data/extracted/aces.vromfs.bin_u --distances 10 100 500 1000 --out /data/range-ammunition.json --report reports/range-profiles.json
python scripts/plan_native_rebuild.py --catalog /data/catalog.json --ammunition /data/range-ammunition.json --index /data/verified.sqlite --config /data/physical-scenarios.json --out /data/range-requests.json
python scripts/rebuild_native_maps.py --repository /data/source-repository --catalog /data/catalog.json --ammunition /data/range-ammunition.json --requests /data/range-requests.json --source-root /data/extracted/aces.vromfs.bin_u --out /data/new-range-maps --backend v2-cuda --resume
```

物理规划配置包含 `matchmaking.spread_steps` 和 `scenarios`；每个场景必须给出 `physical_distance_m`、`yaw_deg`。按需枚举合法配对；生成图保留源几何中的全部角度，任何请求角度缺失都会拒绝。完整弹药输入必须包含派生档案的原始父档案，重算器按父档案内容哈希重新生成并逐字段比较，防止仅修改距离标签或穿深。破甲弹在断点恢复时也重新核对客户端文件。

新 CUDA 适配器只修改动能弹的距离参考点：第一层装甲交点对应请求距离，内部后续距离继续使用 V2 顺序层模型。没有修改 vendored V2 文件；旧档案仍调用原核函数。适配器、派生规则、原核函数、源档案和几何均进入模型身份，相关代码变动需使用新输出目录。`progress.json` 每完成 100 张更新一次；`run.json` 保存完成或分批结束的实测结果。所有 NPZ 及元数据原子写入并验证，`--resume` 不依赖进度数字判断完成。

可用 `verify_native_ranges.py --repository ... --catalog ... --ammunition <原始档案> --source-root ... --targets <目标 ID...> --profiles <源档案名...> --distances 100 500 1000 --out reports/range-parity.json` 对真实独立绑定几何运行 CPU/GPU 交叉检查。它同时抽查均匀网格和非空射线，比较状态、概率、所需穿深及材料风险标记；报告保留样本量和实际误差。该检查证明两种离线实现的一致性，不代表游戏实战校准。

2026-10-31 的初次扩展核对 348 个动能弹、51 个破甲弹源档案，追加 1,248 个距离档案，另有 5 个破甲弹档案未通过直接源校验。12 组真实几何检查共 195,846 条采样射线，最大概率差 `1.4722347259521484e-5`。首批 1,000 张新图计算与验证完成，完整规划为 351,403 张；规划量和生成量不等于最终已发布覆盖。实测记录见 `reports/range-rebuild-progress.json`，完整生产数据保持在离线目录。

## 从已捕获客户端重新验证航空固定挂载

```sh
python scripts/import_client_payloads.py --catalog /data/catalog.rules.json --capture /data/client-capture.json --rules-root /data/pinned-rules --commit <40位提交> --catalog-output /data/catalog.payloads.json --out reports/client-payloads.json
```

输入捕获清单的 `packs` 按资源包文件名索引，每项包含 `version`、`saved`、`sha256`、`extracted`。导入器复核保存的资源包哈希和一致版本，验证固定 Datamine Git blob，并要求解包后的 char `wpcost.blk` 与固定版本规则逐记录完全一致。随后重读 aces 中的载具预设、挂点和嵌套容器，复用现有固定挂载解析器，为各武器以及载具使用的源文件记录哈希。

只有客户端明确声明、精确挂点和依赖关系通过、完整所选武器可解析且数量明确的固定预设才标为合法。容器明确的子弹数量作用于其引用的单个弹体资源；顶层发射器没有数量时仍拒绝。内嵌弹体的发射器保留其显式数量。循环容器、未知携带触发器、缺少 shop 预设、互斥配置和不支持的来源约束均不能被静默忽略。未通过的旧挂载继续隔离，并保留在新目录中供审计。

这一步验证实际携带配置，不生成任务命中率或 SP，不改变全局快照的发布状态。导入器将 `client_payload_sources` 留作离线来源追踪；静态站点仍应由现有构建器选择发布字段，不能将完整客户端目录或概率数组发布到首页。当前验收摘要见 `reports/client-payload-progress.json`。

## 用客户端本地化 ID 恢复缺失弹药

```sh
python scripts/recover_native_ammunition.py --catalog /data/catalog.payloads.json --ammunition /data/original-ammunition.json --capture /data/client-capture.json --language-capture /data/language-capture.json --rules-root /data/pinned-rules --wiki-root /data/wiki --out /data/recovered-ammunition.json --out-catalog /data/catalog.ammunition.json --report reports/ammunition-recovery.json
```

语言捕获记录包含 `version`、`saved`、`source_sha256`、`extracted`；最后一个路径下必须有 `lang/units_modifications.csv` 和 `lang/units_weaponry.csv`。命令验证原包哈希、版本和固定 Datamine 身份，按精确载具 ID 读取实际 `commonWeapons`，只接受该车明确声明的弹药选择器或默认弹丸。选择器的本地化 ID、弹丸 ID 与 Wiki 名称必须精确对应，不做口径、名称片段或武器位置推断。`--vehicles <ID...>` 可限定独立批次。

导入器重新核对已缓存 Wiki 页面的原始 SHA-256、所属武器和完整穿深表。弹链为单一弹丸时，其最大穿深表才能用于该弹丸；混合弹链保留有序成员、挂载、选择器和源哈希，并报告缺少逐弹参数，不能把最大值赋给每一发。重复身份、同名但不同挂载来源、未知机制、子口径弹缺少伤害口径、源表冲突均拒绝。全口径动能弹沿用明确标记的 V2 弹丸口径假设，不声称客户端显式提供了 `damageCaliber`。

相同完整弹丸定义与全部物理输入可以合并计算，但每个弹药 ID 的载具、挂载、语言和 Wiki 证明独立保存在 `recovery_bindings`。任意证明变化都会改变档案内容哈希。增量输入包含此前恢复的档案时，先核对其来源；源文件、语言或 Wiki 变更会要求从原始档案重新恢复，不静默沿用旧绑定。输出目录另存，现有原始弹药和正在重算的输入均保持不变。

恢复的档案可直接交给 `prepare_range_profiles.py`、原 V2 读取器和现有原生补算器。该步骤只建立计算输入，不增加已验收图数或正式榜单数。2026-10-31 首次全目录审计恢复 97 辆载具的 114 条弹药绑定，合并为 34 份物理档案，其中 87 辆此前没有合格的自有档案。新增 102 份距离档案后，24 组真实几何 CPU/GPU 对照比较 391,284 条射线，最大概率差约 `1.5795230865478516e-5`。完整输出与缺口见本地 `reports/formal-ammunition-recovery.json`，轻量验收摘要见 `reports/ammunition-recovery-progress.json`。

## 按固定客户端脚本计算挂载 SP 倍率

```sh
python scripts/import_spawn_components.py --catalog /data/catalog.ammunition.json --rules-root /data/pinned-rules --capture /data/client-capture.json --out-catalog /data/catalog.sp-components.json --report reports/sp-components.json
```

`forecast/native_spawn.py` 实现固定客户端的参数优先级、质量插值、挂载倍率、弹链附加倍率和按实际武器划分出击类别的逻辑。导入前校验固定 Git blob、资源包版本与哈希，以及 `ranks_common_shared.nut` 和 `std/math.nut` 的已审阅内容。Windows 解包脚本仅允许 CRLF → LF 的字节差异，仍分别保存原始哈希；任何其他代码差异均拒绝。

实现保留客户端的关键区别：普通炸弹、火箭、凝固汽油弹与鱼雷的增量合计受最大上限约束；制导炸弹按总质量乘最高导引方式系数；空空导弹取最高导引方式系数；直升机 ATGM 的射程和近炸附加在导引倍率之后相乘；大火箭判据使用单枚最大 TNT 量。弹链附加值相加，不与武器倍率相乘。空的类别覆盖块会禁用通用规则，而非回退到通用质量加价。自定义挂载必须提供精确武器 ID 与数量。

输出 `spawn_cost_components` 独立于已有 `spawn_rule`。没有所选弹链时，只有所有已声明弹链都无附加值才可确定完整倍率；否则保留 `combined_multiplier=null`。按武器划分类别还依赖任务开关，导入器只提供开关启用条件下的类别，不凭战斗机/攻击机标签推断任务开关已启用。`shop_get_spawn_score` 的基础费用、房间 BR 修正与最终取整仍由原生函数决定，因此 `final_spawn_points=null`，不会升级为 `official_verified` 出击规则，也不自动解除排名门禁。

本次真实导入覆盖 1,150 辆载具的 7,539 套合法挂载，7,327 套取得完整倍率，46 辆载具的 212 套挂载缺少所选内置弹链。详细逐挂载结果保存于本地 `reports/formal-sp-components.json`，轻量摘要见 `reports/sp-component-progress.json`。额外提取的同版 `mis.vromfs.bin` 与 `game.vromfs.bin` 共 6,205 个文件未提供最终原生费用公式；该缺口继续保留。

## 默认弹链与具体挂载武器的绑定

给 `recover_native_ammunition.py` 加上 `--default-belts`，可从同版 GUI 资源包和 `menu_options.csv` 接入默认弹链。导入器校验四份已审阅客户端脚本及资源包哈希，按客户端条件判断弹链：包含弹匣多发条件、单图标覆盖和隐藏默认弹药标记；随后要求 Wiki 父武器名称与客户端原始大小写的武器本地化键、实际挂载数量精确相符。只有名称为 Default 而没有父武器证明的记录仍不绑定。

GUI、默认标签、父武器名称和原始弹链顺序全部保留在来源证据中；增量导入重新校验这些证据。新发现的所有者如果使用相同弹丸和物理参数，会合并到已有档案并保留每条绑定，不产生重名物理档案。混合弹链继续要求逐弹穿深曲线，不能使用 Wiki 的最大穿深代表每一发。

2026-10-31 对剩余 2,871 条弹药记录重新审计，新增精确绑定 144 条默认弹链；全部为混合弹链，没有新增可计算物理档案或覆盖。需要逐弹数据的记录由 76 条明确到 220 条，未找到精确选择器的记录由 1,231 条降为 1,087 条；此前无合格自有档案的载具仍为 181 辆。详见本地 `reports/formal-default-belt-recovery.json` 与轻量摘要 `reports/default-belt-progress.json`。

## 发布来源审计与实际候选范围

发布门槛继续核对全部可独立出战载具的 BR 和 SP 来源，以及全部标记为合法的挂载来源；不能只核对全局榜优胜者。`system_role=component` 与候选枚举器保持一致，不要求多车系统的部件提供独立出击规则。隔离的 `legal!=true` 预设仍保留在诊断中，但不再否决无关合法候选。已合法的挂载如果提供自己的 `spawn_rule`，必须单独满足版本和来源要求，不能借用车辆默认规则的核验状态。

所有全局、分类、筛选榜和未截断搜索表内的可排名队列，都重新核对实际枚举的成员键、载具、BR 配置、弹药、挂载、模式和版本；成员必须 `selection_ready`，携带的挂载详情须与当前枚举结果相同。未知装备仍留在对手总体中，来源审计不删除它们，也不改变物理覆盖分母。缺成员、旧版本、隔离配置和歧义成员键会阻止正式发布。报告的 `source_audit` 给出实际检查数量、隔离数量、多车系统部件和未知对手配置数量。

对当前完整目录的来源专项审计检查了 2,670 辆独立载具、7,539 套合法挂载，并保留 3,140 套隔离挂载及 10 个系统部件的区分。此项审计不等于物理、搜索或完整发布验收；SP 和其余 BR 来源缺口仍然存在。

## 按实际挂载应用缺失预设约束

同版客户端的 24 架飞机在部分挂架中引用了不存在的预设名。导入器保留每条原始依赖和互斥关系，并在 `unresolved_relations` 中记录源挂点、预设、关系类型和缺失目标。选择需要缺失目标的预设仍返回 `required_mount_preset_missing`，直接选择不存在的预设仍拒绝；不猜测别名，也不把该问题扩大为整架飞机所有配置均不可用。未识别的约束语法仍按不支持处理。

完整重导入新增 319 套合法配置，原有 7,539 套全部保留；当前共 1,174 架飞机／直升机、7,858 套合法预设、2,821 套隔离记录。148 条源文件中缺失目标的关系均保留在审计记录中；55 套受检查配置因实际缺少依赖被拒绝。重新导入 SP 组成参数后，7,646 套有完整倍率，212 套仍需要所选内置弹链，最终原生 SP 费用仍未生成。结果见 `reports/mount-scope-progress.json`，完整目录为本地 `catalog.formal-sp-components-2.59.0.92.v4.json`；运行中的物理补算继续使用原有不可变输入。

## 完整携带设备与数量语义

固定挂载导入现在解析副油箱、瞄准吊舱、机炮／机枪、干扰弹发射器、鱼雷和助推器。每项仍要求实际挂架选择、资源引用、同版商店预设和完整依赖检查；仅有触发器名称不能把其他资源伪装成副油箱、吊舱或鱼雷。未识别设备仍会隔离整套配置，不能静默忽略。

机炮与干扰弹发射器的 `count` 表示设备数，`ammunition_per_unit` 和 `ammunition_count` 分别表示每件设备和合计装填量；一门携带 150 发弹药的机炮不会变成 150 门机炮。弹链、干扰弹类型没有由固定预设指定时保留 `ammunition_selection_status=not_selected`。机炮挂载上的质量覆盖资源默认质量，来源字段明确区分两者。副油箱、助推器和携弹设备保留配置质量，但 `mass_complete=false`，不推算未选择燃油、推进剂或弹药的满载质量。

只有来源、物理参数、弹数和修改件要求相同的设备才能合并计数；`mount_assignments` 保存每个声明选择和内部武器序号。逐挂载修改件要求进入已有解锁过滤。未知质量不掩盖其他已知质量已超出载荷限制的情况。研究引擎保留辅助设备的质量和依赖，但不会把副油箱、瞄准吊舱、干扰弹或助推器计入攻击武器；只有这些设备的预设不会获得已知的攻击评分。预测页面分别显示设备数、弹药数量与未选择状态。

此项仅扩展完整配置身份与来源覆盖，不生成命中率、击毁率、任务效果、穿后毁伤或最终 SP。真实重导入和回归测试摘要见 `reports/carried-items-progress.json`；大型目录及逐项报告保留在本地，运行中的物理输入不变。

2026-10-31 全目录重导入共验证 1,196 架飞机／直升机的 9,623 套预设，比 v4 增加 1,765 套、22 架；原有合法预设及其逐弹种数量全部保留，地面目录完全不变。设备解析错误和未知触发器拒绝已清零；仍拒绝商店缺失预设 15 条、缺少实际依赖 64 条、数量错误 2 条、互斥组合 2 条。历史隔离记录仍有 1,325 套。重新导入 SP 组成参数后 9,390 套倍率完整，233 套仍缺所选弹链，最终 SP 数量为 0。最终代码通过 287 项 Python、61 项 Node.js 测试及合成统一静态站点的 Playwright 验收；完整工作目录为本地 `catalog.formal-sp-components-2.59.0.92.v6.json`，这不等于正式全服排名验收。

## 默认固定武备与来源明确的无外挂预设

`primary_armament.py` 根据同版 `weaponryinfo.nut`、`weaponrypresets.nut`、`bulletsinfo.nut` 中的规则解析默认 `commonWeapons`、内部挂点和旧式 `WeaponPilons`，并核验三份脚本的固定哈希。固定武备与预设外挂相加，不因外挂列表为空而丢失机炮／机枪。虚拟占位武器保留在诊断中，完全相同的旧式声明按客户端 `appendOnce` 规则去重。默认主武器 ID 明确为空字符串；可选主武器改装另列为未选择选项，不把改装效果混入默认配置。

只有机型声明、实际存在的预设文件和同版商店记录同时成立，且文件中没有武器声明或未知指令时，才接纳 `source_declared_empty`。缺失文件不等于空预设。挂架系统中的普通空选择仍报错；经过来源审计的固定空预设单独保留核验证据。固定武器数量、弹数、来源哈希和修改件要求保存在 `default_primary_armament`；挂载记录引用其内容哈希并携带精简展示摘要。候选枚举校验该引用与摘要，BR 武器限制包含固定武器，但固定武器不计入外挂质量上限。

逐机刷新失败时，旧挂载记录保留供研究使用并改为隔离状态，不能沿用旧的合法标记进入新目录候选。页面区分“无额外挂载”和“外挂信息缺失”，并显示实际默认固定武备。此模块不计算机炮命中率或穿后毁伤，也不完成其他主武器改装选项的枚举。

Ka-50 与 Ka-52 的同一机炮声明分别重复出现 `[500,250]` 和 `[460,250]` 弹数参数。重复参数不代表多门机炮或可相加的弹箱；相同值重复时只计一次，不同值保留 `ammunition_quantity_status=ambiguous_repeated_parameter` 和原始值，合计弹数为 `null`。显示明确标为“弹药数量未核实”，直到客户端运行时的参数读取优先级得到验证。

2026-10-31 最终全目录导入覆盖 1,454／1,454 架航空载具，合法预设 10,491 套，新增 868 套来源明确的无外挂预设，补上此前 258 架完全没有合法记录的飞机。原有 9,623 套合法外挂及其逐弹种数量全部保留，地面目录不变。另有 1,325 套历史隔离记录；导入无逐机错误和解析错误。仅做航空候选来源枚举时，1,438 架、10,471 个配置可选，16 架仍缺已知 BR，未删除这些未知对手以提高覆盖率。86 架有其他主武器改装选项，尚未枚举。

SP 组成参数覆盖全部 10,491 套合法预设，其中 10,183 套倍率完整，308 套仍缺所选弹链，最终 SP 为 0。代码通过 302 项 Python、62 项 Node.js 测试，以及合成统一静态构建和 Playwright 验收。精确摘要见 `reports/primary-armament-progress.json`；完整工作目录为本地 `catalog.formal-sp-components-2.59.0.92.v8.json`。航空来源枚举未提供地面物理输入，不能代替物理覆盖、正式搜索或发布验收。

## 可替换主武器与完整配置身份

导入器逐一解析源文件中声明的主武器改装，选择该改装的 `effects.commonWeapons`，再与实际副武器预设组合。不同主武器不能叠加。默认挂载 ID 保持不变；非默认配置 ID 为 `<preset>::primary=<URL 编码的修改件 ID>`，另存原始 `source_preset_id`，供挂架与 SP 来源查找使用。主武器本身的内容哈希、弹数摘要和改装来源随配置一同核验，额外传感器、红外和干扰参数保留原文，不转换成任务成功率。

所选修改件必须同时存在于客户端与同版商店，直接研发前置要求须一致。`required_modification_ids` 保存实际 `reqModification` 的已购买前置闭包，`required_enabled_modification_ids` 保存当前装备需要启用的修改件；购买前一种主武器作为研发前置，不代表同时装备两种主武器。副武器要求其他主武器时拒绝组合。循环、缺失或不一致的前置关系拒绝对应配置；`prevModification` 仅作为原始树关系保留，不冒充购买前置规则。

BR 选项可通过 `allowed_source_preset_ids` 和 `allowed_primary_armament_ids` 分别限制副武器预设与主武器选择；已有完整挂载、武器和研发状态限制继续生效。候选键包含完整配置 ID，因此切换主武器会改变队列成员和缓存身份。页面明确显示所选主武器改装。数据实测和回归验收见 `reports/primary-modifications-progress.json`。

2026-10-31 的 v9 目录纳入 86 架载具的全部 122 个来源主武器选项，新增 1,221 套完整配置，合计 11,712 套合法配置，覆盖 1,454 架航空载具。原有 10,491 套配置、携带武器及 SP 组成参数均保持一致，地面目录不变；历史隔离记录仍为 1,325 套，逐机与解析错误为 0。仅航空候选枚举得到 1,438 架、11,692 个可选配置，耗时 0.658 秒；16 个未知 BR 条目仍明确拒绝。SP 倍率完整配置为 11,378，334 套缺所选弹链，最终 SP 仍为 0。309 项 Python、63 项 Node.js 及合成静态站点 Playwright 验收通过。主批次物理计算与完整发布验收继续独立执行。

## 客户端修改件与弹药选择器的精确映射

`recover_native_ammunition.py --selector-rules` 核验同版 char/GUI 包、三份已审阅脚本和 `config/modifications.blk`，按 `getModificationBulletsEffect` 的参数顺序读取首个 `bulletMod` 或 `additiveBulletMod`。只枚举载具声明的修改件所指向的实际挂载武器选择器；不再要求修改件 ID 与武器块名称相同，也不在映射缺失时猜测同名块。真实源审计发现 75 处不同名映射，既有同名选择器没有相反映射。

地面载具的具名机炮弹链按客户端 `isCaliberCannon` 的 15 mm 阈值，可用首发弹丸的本地化名称显示整条弹链。导入器遵守直接名称优先于 `weapons/<id>/short` 的规则，不匹配后续弹丸，也不把该规则用于默认弹链、反制装置或小口径机枪。完整弹丸顺序仍保留，混合弹链继续拒绝生成单一最大穿深档案。来源、修改件、实际选择器与显示规则都记录在绑定证据中；增量恢复检查全局修改件和 GUI 证据变化。

2026-10-31 全目录复查新增 125 条明确绑定，待定位选择器从 1,087 降至 962；这 125 条均为混合弹链，需要逐弹曲线的记录从 220 明确到 345。现有目录和弹药档案的字节哈希均不变，没有新增物理档案或地图，无可用自有弹药档案的载具仍为 181。316 项 Python、63 项 Node.js 和合成统一站点 Playwright 验收通过。详细记录保存在本地 `reports/formal-selector-effect-recovery.json`，轻量摘要见 `reports/selector-effects-progress.json`。

## 无命名弹丸的精确源块身份

`recover_native_ammunition.py --structural-projectiles` 可保留没有 `bulletName` 的原始弹块。每发弹丸的 `projectile_identities` 记录武器相对路径与完整文件 SHA-256、实际选择器、从零开始的弹块序号、弹块内容哈希和来源声明名称（缺失为 `null`）。`source-projectile:<sha256>` 是本项目的来源定位键，不冒充游戏内部弹药名称；原始弹块不注入任何虚构字段。重复且有冲突的声明名称继续拒绝。

载具归属仍需精确挂载、声明修改件和本地化匹配。默认弹链继续要求 `--default-belts` 的 GUI 核验、父武器名称及实际挂载数量。新增源块身份本身不会恢复弹链中每发的穿深；混合弹链完整保留顺序，继续报告 `mixed_belt_requires_individual_projectile_tables`，不能使用整条弹链的最大穿深代替各发弹丸。

对有完整来源且弹丸相同的选择，KE 导入仍检查原 Wiki 穿深、客户端机制、弹芯口径、倾角和跳弹曲线。增量恢复必须启用同一身份机制，并从原武器块重新生成定位证明；源文件、选择器、弹序或证明内容变动都拒绝沿用。真实结果见 `reports/structural-projectile-progress.json`，不把名称绑定数量计为新增物理覆盖。

## 混合弹链逐弹计算与不可变档案复用

在上述恢复命令中增加 `--ordered-belts`，可把精确绑定的混合选择导入目录的 `ammo.ordered_belt`。导入器重新读取已有同质弹档案的原始武器与选择器，核对每个完整弹块的 SHA-256，再逐个匹配混合选择的弹块；不会按名称、口径或最大穿深匹配。原物理档案及其原有装备归属不变，弹链通过独立挂载证明和完整档案哈希引用组件。远距档案必须追溯到相同原始档案的完整哈希。增量导入会重建已有弹链证明，来源变更时拒绝沿用。

目前支持源文件明确声明相等正整数 `bulletCount` 的组件。源块顺序和数量保留在证明中；“交战从首个组件开始，按源块顺序循环”明确标为模型假设，不声称经过客户端发射时序实测。数量不同、数量缺失、组件档案缺失或有歧义的选择继续列出原因。全部组件都需要对应目标、原生距离和精确 yaw 的物理图，缺一个也不能凭其余组件提升覆盖率。

一个弹链仍是一个可选弹药配置，不增加对手的载具权重。每发的命中积分、物理档案与地图哈希进入事件序列；连续射击按序循环至已知容量或暴露结束。同刻射击继续使用事件前双方状态。输出记录每个组件的 shot key、弹序、弹链证明哈希及模型假设。弹匣内射速使用该武器的 RPM，手动更换弹匣时间不会取代逐发间隔；没有经过核验的补弹过程不自动续射。新导入的多炮管弹链在缺少完整开火时序时标为 `multiple_mount_firing_schedule_unverified`，不能按单管完整结果发布。

真实导入保留 8 条混合选择。已有补充物理图可复用，但这些新载具的匹配环境还需要 1,664 份额外组件图；图的完成与独立预检另行记录，不能把来源绑定成功计作物理覆盖完成。穿后毁伤不在本次真实计算范围内，端点仍为条件穿透。

`scripts/plan_native_rebuild.py` 默认把混合选择的全部缺失组件加入重算请求，已有一个组件不会掩盖另一个缺项。`--ordered-belts-only` 可仅规划这部分增量；其余参数仍是 `--catalog`、`--ammunition`、`--index`、`--config` 和 `--out`。输出可直接交给现有 `scripts/rebuild_native_maps.py --resume`，无需改动物理档案或复制计算引擎。

该补充批次已实际完成：1,664 份地图、221,930,393 字节，计算 195.224 秒、峰值 RSS 1,175,777,280 字节。独立预检耗时 117.089 秒，全部数组通过，拒绝、歧义与几何绑定错误均为零。它补齐这 8 个选择在既定匹配范围内的 124,512 个正向距离／角度情景所需组件；不代表全目录双向覆盖。两种四联装炮仍缺多管时序，另外六种可进入逐弹交战计算。真实 Flakpanzer I 混合弹链与 Type 98 AP-T 的四距离、yaw=0、技能=0 双向诊断通过交换对称性检查。344 项 Python、64 项 Node.js、Playwright 统一构建及四项研究站烟测通过；摘要见 `reports/ordered-belt-progress.json`。目录增量回放完全一致，原弹药档案哈希保持不变，正式榜单仍为零。

## 精确单射流化学能弹药与选择性补算

恢复命令增加 `--chemical-energy`，在既有精确挂载、选择器、本地化和源哈希校验后读取单射流 `cumulativeDamage.armorPower`。火箭使用 `rocket` 内层的有效口径、跳弹预设及穿深，不混用外层显示参数。M908 按真实选择器和 `120mm_m908` 弹块恢复，使用源文件明确给出的 80 mm 有效口径，不沿用旧 M830A1 绑定。旧档案保留原样；不可用旧 CE 来源在报告的 `legacy_ce_source_rejections` 中列出，新参数使用独立身份。

同一 Wiki 弹药弹窗可能同时有恒定化学穿深表和距离相关的动能撞击表。前者与目录精确核对，后者完整保留作证据，不拼成一个距离曲线，也不使用其最大值。实际 CE 穿深来自客户端标量。只接受明确支持的单射流类型；HESH、串联预装药、EFP、偏转射流和多重装药继续拒绝。每次距离派生会重读原始弹丸、载具挂载及伤害响应文件，验证完整内容哈希和物理参数；计算模型身份包含化学能适配器代码哈希。

结果仍是**已有有效射流到达装甲时的条件穿透**。源最小/最大射程作为证据保留，不据此声称实现飞行、引信、命中率或穿后毁伤。100 mm/m 空气衰减沿用既有 V2 CE 模型并明确标注假设；没有原生 `damageCaliber` 时使用原生口径的假设同样保留。四个距离仍分别重新计算有序几何，不缩放既有概率图。

`plan_native_rebuild.py --profiles <精确档案名...>` 可仅规划新弹药的全部缺图。另一种自有弹药已有图不会使这次补算被跳过；请求保存选择范围及完整输入哈希。此选项与 `--ordered-belts-only` 互斥，默认规划行为保持原样。

2026-10-31 的 v13 导入新增 73 个弹药选项、35 个档案，覆盖 67 辆载具，无自有参数的独立地面载具由 180 降至 155。438 个旧原始档案及三个旧距离参数包全部保持一致，增量导入回放逐字节一致。73 个新选项均进入候选枚举，具有 10/100/500/1000 m 参数；38,664 张补图已启动，首批 50 张独立验证通过。M908、Pl-3 和 I-TOW 在真实 M1A1 几何上的四距离 CPU/CUDA 交叉检查覆盖 196,128 条采样射线，12 组均含部分穿透概率，最大差为 5.96×10⁻⁸；这验证实现一致性，不是游戏实测概率。358 项 Python、64 项 Node.js、Playwright 和四项静态站烟测通过。完整物理覆盖、SP、版本身份与全量搜索验收尚未完成；实际进度以 `reports/chemical-projectile-progress.json` 为准。

实际 Windows 补算曾因读者短暂占用 `progress.json` 而在原子替换时报错，已写入的 1,000 张图保留。`atomic_write` 对 Windows 5/32/33 文件占用错误只重试替换动作，最多六次、累计等待 0.31 秒；始终保留上一版可见文件，不回退到截断写入。持续锁定或其他权限错误仍抛出异常。断点恢复重新校验并复用这 1,000 张图后继续计算，单元测试覆盖暂时占用、持续占用及非 Windows 错误。

## 原生 SP 最终算术的静态核验

`python scripts/audit_native_spawn.py --client-exe '<客户端>/win64/aces.exe' --game-version 2.59.0.92 --out reports/native-sp-final-arithmetic.json` 只读取客户端文件。程序核验已审阅可执行文件的完整 SHA-256、大小、四段调用函数及两个常量区间的哈希；其他文件或版本不会自动套用这些偏移。无需启动游戏、注入进程或安装反汇编运行时，报告不包含客户端二进制内容。

已定位 `shop_get_spawn_score` 注册、包装函数、参数适配和最终费用函数。普通合成路径先把原生整数基础费用转为 float64，依次乘上脚本返回的武器倍率和类别／重复出击倍率，最后加入带符号的 `0x1.fffffffffffffp-2` 偏置并向零截断为 int32。偏置是 0.5 的前一个浮点值；不能替换为 Python 的银行家取整，也不能在两次乘法之间先取整。`native_spawn_final.compose_final_stage` 实现这一受限步骤，测试覆盖半整数、紧邻半整数的下界、乘法顺序、整数范围和非法输入。

这**没有**恢复完整战局费用：基础费用可来自运行时费用表或 `get_unit_spawn_score` 回调，正的 `persistent_weapon_costs` 覆盖还可提前返回。实际任务状态、弹链、配置和重复出击历史仍须核验。现有 Python 倍率端口也不自动冒充 Squirrel 原生 float32 运算返回值。报告保持 `complete_base_formula_verified=false`、`generated_verified_final_costs=0`、`publishable=false`，不改变既有出击规则和排名门禁。

可选 `--replay <输入.json>` 接收数组，每条恰含 `base_cost`、`weapon_multiplier`、`type_multiplier` 三个数值，生成可复现算术结果和输入哈希。所有回放均标为 `supplied_inputs_not_verified_battle_cost`，不能把人为输入的基础费用升格为真实 SP。若后续提供游戏内成本采样，应同时保留版本、模式、房间最高 BR、载具／配置、实际挂载／弹链、先前同类别出击次数、任务覆盖项及来源；单独一张费用截图不足以覆盖全部状态。

## 官方 BR 表与后续数据版本核对

`scripts/audit_br_publication.py` 从官方公告链接取得发布工作簿，再按工作簿列出的原始 URL 捕获航空和地面表，保存四份内容哈希及采集时间。首次传 `--fetch --announcement-url <官方公告 URL>`；以后使用同一 `--source-root` 可离线核验和重放。输入为 `--catalog`，独立报告由 `--out` 指定。捕获目录、输入目录及报告应分开，捕获文件不会进入站点或 Git。

读取器校验表头及 Google 分块后的连续行号，分别读取固定翼的 Ground Realistic 列与地面载具的 Realistic 列。对于带删除线的旧提议，保留原始单元格证据并使用未删除的值；无地面模式变更的行不会借用 Air RB。表尾的武器及挂载调整作为独立记录保留。显示名仅用于生成审计候选，重名、缺名和多重对应均报告，不由显示名推断载具 ID 或直接升级版本状态。

本地实际捕获的 2026 年 10 月官方表中，372 条显示名可唯一对应的地面 RB 变更在固定提交 `2423f3099e2a5ec3f31efdce87b9f4380ea761ec` 的 2.59.0.94 规则导入结果中全部一致，在本地 2.59.0.92 规则结果中全部不同。另有 110 个未匹配显示名、6 条重复显示名、1 条目录重名；这些计数是表格行的审计结果，不代表同数目的游戏载具缺失。34 行含修改痕迹，另保留 5 条无 BR 变更的武器／挂载说明。两次 .94 离线审计输出的 SHA-256 相同。

固定提交来源的规则导入报告显示 592 辆原目录载具 BR 更新，2,062 辆保持一致，26 条原本不可选条目保持不可选。该 .94 目录是待核验的版本迁移输入，不覆盖仍在计算的 .92 目录。两个 Datamine 版本之间还涉及地面武器、一个地面载具源、航空挂载及全局配置；必须逐项验证物理输入与配置关系才能复用旧版本结果。仅 BR 不同本身不要求重算已有物理图，但新的合法匹配环境可能需要补算之前没有生成的载具组合。

## 可恢复的大图集合并

`scripts/assemble_native_atlas.py` 支持在 `--base-index` 后重复传入 `--extra-index`，直接使用各批次已完成数组预检的索引；任何仍待验证的选中数组、缺失的索引指纹、发生变化的元数据或数组都会拒绝合并。输入弹药档案由 `--ammunition` 指定，`--out` 必须是新的独立目录。原有 `--extra <目录>` 用法仍会逐个校验附加数组。

传入 `--resume` 时，工具将结果写入输出同级的隐藏暂存目录，并记录索引及弹药文件哈希。中断后用完全相同的参数重试，可核对并复用已经暂存的硬链接或副本；副本与元数据通过临时文件原子替换，未完成的副本不会冒充完整数组。参数、索引内容或暂存元数据发生变化时拒绝续用；可恢复模式要求附加批次提供已验证索引。所有批次身份检查完成后才原子重命名为最终目录，随后仍需对合并图集执行完整预检。暂存进度可通过隐藏目录中的 `.wt-assembly-progress.json` 查看；同一输出不要同时启动两个合并进程。

## 不同 Datamine 提交的物理来源差异

`scripts/audit_version_inputs.py` 从本地 Git 对象库读取两个完整提交的树，不依赖 GitHub 可能截断的递归树 API。用 `--repository` 指定仅含提交和树对象的本地仓库，`--old-commit`、`--new-commit` 指定完整提交，`--ammunition` 与 `--bindings` 指向旧版物理输入，`--out` 写独立报告。可使用 `git fetch --filter=blob:none --depth=1 <Datamine 仓库> <完整提交>` 把所需提交和树分别获取到独立裸仓库；审计命令本身不拉取源码或改动对象库。

工具比较每个弹药档案声明的武器、全部归属车辆源，以及已绑定目标的对应载具源；原始提交不匹配、来源路径缺失、文件缺失和 Git blob 变化均分别报告。还比较装甲类别、材料响应、改装和本地化文件。报告只判断 Git 源对象是否变化，`client_grp_equivalence_verified` 与 `promoted_physics_version` 始终为 false；它不会把旧数组伪装成新版数据。

对本次 2.59.0.92 → 2.59.0.94 比较，1,963 个远距档案中的 1,951 个声明的载具和武器源对象不变，12 个涉及变更；1,226 个已绑定目标的载具源中，1,225 个不变、1 个变化。六个核验的全局装甲／损伤响应文件不变，改装配置和两份弹药本地化文件变化。当前结果尚不能证明新版客户端 GRP、完整挂载或运行时与旧版一致；这些来源核验和受影响物理重建仍是发布门槛。

## 官方客户端清单与已绑定 GRP 核验

`scripts/audit_client_manifest.py` 支持用 `--fetch --capture-root <独立缓存目录>` 捕获官方默认更新通道的版本、清单地址和 `.yup` 原始内容。下载前后再次检查版本与清单身份，检测到切换就拒绝生成完整捕获；后续省略 `--fetch` 可离线重放。传入 `--client-root`、`--repository`、`--inventory` 和独立 `--out`，工具只读取游戏目录、原仓库的来源清单和缓存 GRP，不启动游戏或修改客户端。

工具检查清单自身版本与官方地址、完整内容哈希，并逐项核对本地 `warthunder.yup`、`aces`、`char`、`gui` 数据包及已缓存 GRP。每个 GRP 同时检查缓存 SHA-256，以及官方清单中唯一的 SHA-1／文件大小条目；清单未列出、路径不唯一、源文件缺失和字节不符会分别报告。该核验与碰撞资源名称、装甲构型和几何绑定的独立核验共同使用，不单独解除排行发布门槛。

2026-10-31 的实际官方默认通道返回 2.59.0.92。已安装清单与官方下载清单的 SHA-256 均为 `9c100a150d735dad8f411fccb48cec7c5a78b410c4ff09e1af9b03d7f0e575aa`；三份客户端数据包全部匹配，938 个不同 GRP 均唯一匹配清单，涉及 1,226 辆已绑定载具。这是该采集时刻的结果；后续更新需重新捕获。0.94 Datamine 提交仍单独保留，不因提交时间较新就判定为默认通道的客户端版本。

## 未知 BR 与资格的发布门槛

即使载具带有同版本来源字段，只要默认 Ground RB 或任何动态配置的 Ground RB 未知，正式发布审计都会列出载具 ID 并阻止发布。动态配置还必须具有已验证状态和来源 URL；搜索时跳过了某个未知配置不能证明全服候选已覆盖。已明确识别为多载具系统组件的记录继续按已有组件规则处理。

官方[舰载侦察机说明](https://wiki.warthunder.com/mechanics/247-shipboard-reconnaissance-aircraft)将当前 16 个无 BR 条目中的 12 个标为舰载而非独立航空单位，将 KOR-2 标为当时未使用；官方[无人机介绍](https://warthunder.com/en/news/7840)描述了另三个条目对应的战场攻击无人机。它们提供进一步核对资格的线索，但旧文章本身不能证明当前版本所有相关条目的完整资格；在逐项绑定版本和适用模式之前，不会自动解除这些记录的门槛或填写虚构 BR。
