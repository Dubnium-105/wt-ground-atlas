# 决策引擎、模型边界与数据接口

`shared/engine.mjs` 是浏览器 Worker 与离线 Node 构建共用的纯函数引擎。`config/model.json` 保存所有游戏匹配规则、默认交战条件、经验权重、搜索范围、落点模型参数与显示输入范围。车辆、弹药、BR 和变更值由采集快照提供，算法源码不含特定车辆的加分或 BR 例外。

## 对分数的准确解释

这是**待校准的决策效用指标**，范围由模型定义。它不是游戏胜率、每发击毁概率或已验证的游戏穿透率。角色齐全、对手覆盖和资料完整度是不同指标；只有目录完整不能说明装甲物理完整。

地面对抗的独立组件包括：

- 装甲压力：精确缓存键匹配时使用条件分层模型的积分结果；其次使用本车弹药的穿深表与目标 GRP 抽样路径；两者均缺失时，只在防护 RHAe 或均质装甲经过明确验证的情况下计算粗指标。Wiki 的标称装甲厚度不会转为 RHAe，复合装甲也不会凭常数补齐。
- 射击节奏：人工装填只读取满级王牌字段，自动装弹只读取已识别机制的周期。重新瞄准与装填取较长者，允许重叠。射击机会不等于击毁次数。
- 机动：有来源的前进、后退、功重比和中心转向，影响机动相对表现。
- 观察与炮控：已有的观瞄代数、方向机和高低机字段。
- 遮蔽：已有的抛射烟和发动机烟数据。

未知组件保留明确先验和上下界。完全没有攻击能力证据的配对输出 `score:null`；不会将缺失武器或未知防护填成零。正向与反向各自调用 `evaluatePair`。不使用 `1 - 正向分数` 作为反向结果。

航空按单个已核验合法挂载评价，名称、数量、最大射程、战斗部和验证命中条件分开读取。质量未知时保留未知，不能因为导引头类型相同就确定相同效能。反辐射武器只有目标辐射条件成立时进入当前攻击任务。目标机动回避只是可配置行为先验；装甲响应缺失仍然显示为未知。重生点未知使用标注先验，不能当作免费、立即可用的航空火力。

防空对防空的降低权重只作用于整队汇总，单次配对原始指标不变；防空对航空不使用这个系数。多车系统可以通过 `system_id` 防止把同一系统重复放进独立槽位。

## 本车弹药与 GRP 抽样对抗（1.1.0）

`scripts/build_target_layers.py` 校验现有完整射线层缓存的 SHA-256，对每个构型、每个视角的受保护目标投影确定性抽取最多 64 条路径。输出保留第一处受保护内部目标之前的装甲层、动能/破甲材料响应、实体路径长度、倾角与封顶值。未知路径仍为 `null`，无遮挡路径为空数组，两者不混同。当前覆盖 1,225 个构型、24 个方向、1,881,600 条路径。

运行时只在攻击载具自己的 `ammo` 列表内选择弹药。自动选弹逐目标比较抽样响应与装填节奏；手动指定弹药严格检查归属。`ammo_matchups` 记录各弹种被选中的配对数，`ammo_choices` 是最常使用的弹种，不意味着对所有敌车固定使用同一发弹。反向配对重新读取敌车自己的弹药。

动能弹、破甲弹和已识别反坦克导弹分别使用本弹的实际距离穿深与倾角表。`angle_deg` 选择目标方位；每层的真实入射角由 GRP 几何给出。板层按来源倾角表折算，不再重复叠加几何倾斜；实体材料按射线路径长度消耗。超过来源最大倾角采用配置声明的余弦延拓；超出支持视角或距离、HE/碎片等未实现机制仍为未知。抽样响应单独标为 `sampled_layered_ammunition`，不是原参考弹精确缓存。

这条路径组合 Wiki 弹药表与独立客户端材料快照，不声称两源具有同一个游戏提交。它是静态、直线路径的近似，不模拟跳弹、碎片、毁伤、ERA 激活/消耗、串联战斗部或完整 NERA 动态。原 M829A2 / M830 热力图仍保留为参考研究，未变成其它弹药的结果。

队列汇总用正向压力 O 与独立反向压力 I 的相对优势：默认 `sigmoid(logit(O) - logit(I))`。双方相同保持 0.5；提高本方或降低敌方响应会单调提高结果。该值是决策对比，不是统计胜率。原来把 O 和 `1-I` 做算术平均会压缩差异；新版同时将环境平均与不利对手表现的合计权重从 65% 提高至 80%，角色覆盖从 20% 降至 10%。`worst_case` 使用实际对手表现的低分位，置信区间下界另算，不再把资料不确定性直接当作不利对手表现。区间同步通过同一单调函数传播。

这些调整让用弹、防护和具体对手更直接影响分数，不保证每个国家的跨 BR 分布都更宽。实际比较记录于 `reports/scoring-comparison.json`，真实弹药对比记录于 `reports/owned-ammunition-validation.json`；没有为了显示区分度而将候选强制拉伸到 0–100。

## 组队搜索

引擎遍历一国**所有可用的真实陆战历史 BR 上限**。每个上限先按数据配置裁剪各任务角色的候选，再以已有队列的边际对手覆盖选择成员，并尝试配置中的均衡、地面、空地组合顺序。这个方法是确定性的启发式搜索，**不保证穷举组合意义上的全局最优**。

默认容量为 10 槽；每个角色保留的候选数至少等于容量，避免原候选裁剪把十槽队列限制为六辆。合法可用池不足时保留实际成员数。`topLineups` 汇合所有 BR 和任务组合候选，以评分降序排列，并按无序成员集合去重后取前三；首页不受自定义目标 BR 筛选。评分相同时依次比较资料覆盖、实际 BR 和成员 ID，保持确定性。

约束包括国家、真实 Ground RB、槽位、特种载具开关、可用载具 ID 集合、地面成员存在和多车系统去重。实际队列最高 BR 始终由成员计算；候选上限较高不能人为抬高低 BR 队列的分析环境。

对手先验按已经确认等价的构型组分配总质量。组内换皮只是分摊该组的质量，折叠显示不改变分析输入。未经验证的同底盘、同族不折叠。没有排队遥测时，升降房情景占比均为公开配置先验，不宣称改变降房概率。

## 版本变化

`applyScenario` 复制基线后应用模拟方案。载具未匹配、旧值冲突、重复字段、文字说明无新旧值、非法字段或非有限数字均产生审计记录。旧值必须与采集基线一致，不会静默覆盖。

两版对抗使用同一算法和模型配置。`compareVersions` 同时输出：旧队列在基线中的结果、同一成员迁移到方案后的结果、方案重新优化的结果和仅保留地面的替代方案。成员不变也会参与变动差值比较，故只上调一架飞机也能识别队列拆分。公告明确说匹配成本或协同改变，不说原成员不能再一起出场。

`buildVersionOverview` 输出新版评分前三，以及受成员 BR 不同步调整影响的旧队列前三。后者按旧版评分排序，并重新计算保留原成员的新版得分；全体同幅调整、无调整、被审计拒绝的变更不计入拆队。两类榜单均按成员去重，默认预计算进入新版国家分片；自定义条件或导入方案由浏览器 Worker 重算。

## 导出的主要函数

| 函数 | 输出 |
|---|---|
| `applyScenario(catalog, scenario, version)` | `{vehicles,audit,applied,rejected,version,simulated,source_status}` |
| `getBRs(vehicles,nation,version)` | 已知的、去重排序的 Ground RB 上限 |
| `matchEnvironment(vehicles,lineup,version,config)` | 实际最高 BR、主力上下界、同版对手、先验、编队合法性；更低 BR 携带提醒 |
| `evaluatePair(a,b,context,config,probabilities)` | 精确弹药/挂载、原始分数与区间、各组件、缺项、条件、汇总权重 |
| `evaluateLineup(vehicles,ids,context,config,probabilities)` | 实际 BR、`metrics`、正向 `matrix`、独立 `reverse_matrix`、威胁、优势、空地条件 |
| `recommendNation(vehicles,nation,context,config,probabilities)` | 所有 BR 的 `candidates`、`best`、`alternatives`、搜索范围与限制 |
| `topLineups(recommendations,limit=3)` | 全 BR、全任务候选按评分排序并按成员去重 |
| `buildVersionOverview(vehicles,nation,scenario,context,config,probabilities,recommendations)` | `planned_top`、`split_top`、受影响队列数和拆队判定；可复用两版推荐结果 |
| `compareVersions(vehicles,ids,scenario,context,config,probabilities)` | `before`、`after_fixed`、`after_optimized`、对手进出与公告 |
| `validateLoadout(vehicle,preset,context,config)` | 合法性、互斥/挂点/依赖/质量错误和当前辐射条件下可用武器 |
| `integrateAim(shard,context,config)` | 非均匀瞄准后的单发效用区间、未知/无内部目标/落空质量 |
| `aimContextKey(context,config)` | 完整瞄准参数的规范化同步缓存键；技能、距离、运动、炮控、水平/垂直误差和模型配置变化都会区分 |
| `validateModelConfig(config)` | 数值、权重、范围和必需结构校验 |
| `applyCalibration(config,calibration)` | 带来源、样本与验证报告的离线参数拟合导入；导入本身不代表已验证 |

### Context

`config.defaults` 提供初值。可覆盖 `version`、`nation`、`br`、`slots`、`skill`、`distance_m`（瞄准距离）、`physical_distance_m`（弹道物理距离）、`angle_deg`、`includeSpecial`、`availableIds`、`ammo_by_vehicle`、`loadout_by_vehicle`、`available_spawn_points` 和 `target_radar_emitting`。

`includeMatrix:false` 可省去返回稠密矩阵。搜索和离线发布使用此模式；用户查看自选队列时由 Worker 按需计算。内部 `_pairEvaluator` 仅用于缓存，外部调用者不需要传入。

`metrics` 包含 `mean`、`down_tier`、`same_tier`、`up_tier`、`worst_case`、`role_coverage`、`air_window`、`aa_coverage`、`known_pair_fraction`。`threats` 和 `advantages` 的 `vehicle_id` 可定位科技树或档案。主力环境与低 BR 携带可能性不可混同。

## 概率和瞄准积分

输入 shard：`{width,height,pixel_pitch_m,probabilities,status,metadata?}`。数组行优先，尺寸和间距来自元数据。`probabilities` 是解码后的浮点数数组，未知仍为 NaN。矩阵大小不是固定值。

目标中心取轮廓有效像素内最靠近几何中心的点。可穿区域按连通域找到内部稳健点，避免空洞质心。根据技能配置，将一般中心瞄准与这些稳健点组成混合分布。实际落点误差是独立的二维高斯模型，支持水平与垂直误差、距离、技能、运动和炮控条件。

积分对每个像素矩形计算高斯 CDF 差，不在可穿区内截断，也不删除落到轮廓外的质量。输出的 `known_mass + unknown_mass + no_protected_target_mass + miss_mass` 保持完整概率质量。未知材料给区间而非零值。状态 1 仅表示数据定义的受保护内部目标未出现在射线上，不能解释为整车不可受损。

改变瞄准距离或技能不改动原始物理概率数组。精确配对缓存要求目标、构型、弹药、弹药物理配置哈希 `ammo_profile_sha256`、物理射距、姿态、几何版本、配置版本、物理模型版本、来源版本和 `aim_context_key` 全部匹配；缺失字段不能彼此相等后就通过。瞄准缓存键包含完整瞄准配置和未知先验，不能把平均水平的预积分结果用于不同技能或距离。旧客户端研究图可独立查看，但不能因同名弹药或参考图存在就认定某车装备它，或属于当前 Wiki 快照。

仅改变 BR 不使物理缓存失效。导入方案改变 `ammo` 或 `protection` 时，受影响车辆会标记 `probability_cache_invalidated_by_scenario`，旧精确记录不再使用；需要后端依据新物理输入重新构建快照与对应结果，不能只沿用弹药名称或旧构型 ID。

## 离线预计算

```bash
node scripts/build_decisions.mjs \
  --catalog data/snapshots/catalog.json \
  --scenario data/snapshots/changes.json \
  --config config/model.json \
  --probabilities data/snapshots/decision-probabilities.json \
  --target-layers data/snapshots/target-layers.json \
  --out web/data/recommendations
```

支持 `--nations usa,germany` 做限定构建，`--force` 强制重算。输出每个国家、版本的轻量 JSON、内容哈希文件名、Gzip 旁文件与 `index.json`。不发布全配对稠密矩阵。构建键包含目录、方案、模型配置、轻量概率摘要、目标抽样层和引擎源码的哈希；完全相同输入会复用已有构建。

概率摘要默认读 `data/snapshots/decision-probabilities.json`；该默认文件不存在时使用明确的空摘要状态。显式传入的文件不存在会报错，不会悄悄忽略。后端汇总脚本产生 `{schema_version,model_version,records,provenance}`，其中 `model_version` 属于装甲物理模型；决策算法版本另由配置追踪。每条记录只有 `reference_only:false` 且全部条件匹配时才可参与配对。当前上传研究资料默认保持 `reference_only:true`，必须有经核验的来源与身份绑定后才可改变。

`index.json` 包含每国每档候选的固定旧队列迁移与优化对比，以及所有匹配环境进出信息。无真实调整数据时，方案明确记录源状态，不能从历史案例编造生产调整。

## 自动验收

```bash
node tests/engine.test.mjs
```

测试数据均为显式夹具，与生产快照隔离。覆盖需求清单的异步上调、边界、敌方单独变化、精确弹药分离、换皮、防空任务权重、合法混挂、未知概率/落空、技能和射距语义、固定/重算队列、确定性、冲突审计、配置验证和离线拟合接口。浏览器静态运行与资源测量由项目端到端验收报告单独提供。
