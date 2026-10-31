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

`--missions` 文件为 `records[]`。每条要求 `variant_key/target_vehicle_id/scenario_id/task/lower/mean/upper/source_version/source/status`。合法 task 为 ground、anti_tank、ground_control、recon、anti_helicopter、anti_air、cap、cas、sead；数值必须有序且在 [0,1]。生产任务还要求 evidence 的 `loadout_id/flight_conditions/radar_conditions/target_countermeasures/source_sha256`。条件对象中记录导引头、包线、飞行状态、雷达与反制适用性；`measurement=maximum_range` 不能充当任务效果。所有提供的数字是外部证据输入，程序不会从射程推断命中率。

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

## 逐队列发布证据（后续审计修正）

等待出击具有已知的零收益，但不产生任务观测。出击策略分别返回 model_state_coverage 对应的状态覆盖和 evidence_coverage 对应的实际任务证据；队列准入使用后者。production_mission_coverage 进一步按成员、敌方分布、房间和场景汇总携带配置绑定的来源证据，只有 validated 且提供 delivery evidence 的任务记录计入生产覆盖。全局存在某类任务记录不能替代入榜队列自己的证据。

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
