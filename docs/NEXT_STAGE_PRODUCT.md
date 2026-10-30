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

当前明确的稳健性政策为每榜候选重合度至少 0.5、共有候选效用变化不超过 10 分，且不能有空榜或截断搜索；这不是实测误差或统计置信度。失败不会阻止诊断站构建，只阻止正式榜发布。外部 `--sensitivity` 必须提供同结构的完整测量报告，绑定目录、有效配置、物理图、任务/毁伤与代码输入及内容校验和；仅 `passed: true` 不再通过。使用 `--run-sensitivity` 可在离线本机生成该报告，不依赖外部授权数据。
