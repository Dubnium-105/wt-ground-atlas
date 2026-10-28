# 验收报告：应用流程与数据/模型覆盖分开验收

日期：2026-10-26。基线：[REQUIREMENTS_BASELINE.md](REQUIREMENTS_BASELINE.md)。机器可读逐项结果：[acceptance.json](../reports/acceptance.json)。

**本地客户端地面目录已经完成资源、材料与两份参考弹的全视角批量计算；生产精确配对、全弹药弹道和游戏校准仍未完成。** 本报告逐项映射全部 **126 个 ID（113 条要求 + 13 个 AT 案例）**，保留缺口，不作“100% 完成”声明。

`implemented` 表示该规则/工作流/接口有实现与当前范围内验证；`partial` 表示仍缺生产覆盖、完整模型或最终验收；`needs-data` 表示缺真实输入/校准证据。状态是条款评估，不是项目完成百分比。

当前状态计数：`implemented` 95，`partial` 30，`needs-data` 1。其中完整物理覆盖与游戏科学有效性必须看以下限制，不能由已实现条款数量推断。

## 已生成的真实产物与覆盖

| 范围 | 当前结果 | 证据 |
|---|---|---|
| 官方目录 | 10 国、2677 条；1225 地面、1340 飞机、112 直升机；2651 条已知 Ground RB，26 条未知不代填 | [来源覆盖](../reports/source-coverage.json) |
| 两版推荐 | 20 分片；350 当前候选、373 方案候选；国别 BR 并集 379；400 条调整接受、0 拒绝 | [完整性](../reports/decision-integrity.json)、[构建报告](../web/data/recommendations/build-report.json) |
| 武器及挂载 | 1163 条有弹药；421 架有 1249 套已核验整套预设；733 条有自选挂点资料 | [来源覆盖](../reports/source-coverage.json) |
| 装填与系统 | 915 条王牌装填、161 条自动装填；20 条多车记录对应 10 套系统，组件性能尚未聚合 | [来源覆盖](../reports/source-coverage.json) |
| 防护参考摘要 | 2450 组来源、58800 条摘要，积分 2696249616 像素样本；全部 reference_only；**0 已核验生产精确配对** | [摘要报告](../reports/probability-summaries.json) |
| 轻量摘要大小 | 原始 JSON 147337732 字节，gzip 9589268 字节；不需向浏览器传 GRP/射线层 | [摘要报告](../reports/probability-summaries.json) |
| 输入一致性 | 目录/方案/模型/引擎/概率摘要哈希均吻合；文件/gzip 错误与越界分数为空 | [完整性](../reports/decision-integrity.json) |

推荐是配置驱动的确定性启发式搜索；它扫描有效 BR 上限，按角色与边际覆盖组队，不保证穷举意义上的全局最优。游戏数值取快照，匹配规则、默认值、系数与阈值取版本化配置。测试中的历史 BR 与人工核验身份只用于夹具。见[引擎说明](DECISION_ENGINE.md)和[摘要契约](DECISION_PROBABILITIES.md)。

## 必须保留的交付边界

1. **客户端地面目录已批量处理。** 1,225 个构型使用 937 份不同 GRP、196,918 个碰撞节点，全部完成两份参考弹 × 24 视角的离线计算。基础资源与补丁资源按资源名合并；各车型保留独立材料。证据：[客户端覆盖](../reports/client-grp-coverage.json)、[计算状态](../reports/client-compute.json)、[材料对照](../reports/client-binding-validation.json)。原四车报告保留为历史基线。
2. **当前生产精确配对为 0。** Wiki 不公开游戏版本/提交；固定 Datamine/客户端参考并不因此与 Wiki 同版。58800 条参考摘要全部被身份门禁隔离。未来需真实目标构型、几何/材料版本、具体弹药所有权与参数哈希，以及物理和瞄准上下文一致，才能接入配对。见[摘要报告](../reports/probability-summaries.json)与[接入契约](DECISION_PROBABILITIES.md)。
3. **航空、防空和多车系统尚有数据缺口。** 雷达扫描角/周期、架上弹、完整 SP、现代合法自选混挂、武器实际包线未齐。已核验整套预设不代表自选挂点所有组合合法；已知系统成员不等于主车火力已聚合。见[来源覆盖](../reports/source-coverage.json)。
4. **毁伤状态与游戏校准未完成。** 未完整实现部件损坏、破片、殉爆、ERA 消耗及连续交战状态转移；技能、情景先验与效用系数未用实战统计拟合。穿透条件效用不是击毁率或胜率，读取 damageparts 字段不等于复现其运行时机制。见[引擎说明](DECISION_ENGINE.md)、[防护契约](PROBABILITY_CONTRACT.md)和[绑定审计](../reports/damageparts-binding-audit.json)。
5. **数值一致性检查有明确范围。** 32768 条采样射线与上传来源分层模型比较，status/flags 无差异、最大概率误差约 5.96e-8；这是模型对照，不是游戏验证。单视角全网格几何对照的 59458 条射线仍有 22 个状态差异，联合已知区域最大概率差约 3.1054e-5。不能称原生几何逐像素完全一致。见[模型对照](../reports/cpu-model-validation.json)、[几何对照](../reports/cpu-geometry-validation.json)。

## 测试、浏览器与资源证据

自动测试报告记录 **26 引擎 + 14 管线 + 12 来源 + 5 客户端 + 4 摘要 = 61 项通过、0 失败**。各套测试验证算法、语义、来源解析、身份门禁和确定性；夹具不代表新增生产精确数据。证据：[unit-validation.json](../reports/unit-validation.json)。

本次客户端后端更新的 8 项浏览器流程全部通过，完整性见 [client-backend-validation.json](../reports/client-backend-validation.json)。原 ZIP 的打包/全新解压报告保留为更新前归档证据，本次交付为当前工作区与重建后的 web 目录。

浏览器端到端最终结果统一读取 [browser-e2e.json](../reports/browser-e2e.json)，测试源码为 [browser.e2e.mjs](../tests/browser.e2e.mjs)。最终报告所列流程全部通过，无页面异常或本地资源缺失；本报告不复制通过数量。AT12 据该实际浏览器结果验收，F05 的分发包已通过全部 CRC、逐文件 SHA-256 与全新目录离线重建，见 [release-package-check.json](../reports/release-package-check.json)。单元测试不能替代浏览器证据。

首屏压缩预算与总存储见 [web-build.json](../reports/web-build.json)，实际 HTTP 请求、内存和交互采样见 [browser-e2e.json](../reports/browser-e2e.json)，全国预计算耗时见 [build-report.json](../web/data/recommendations/build-report.json)，设备/运行时见 [environment.json](../reports/environment.json)。预算与实际传输分开，字体应纳入首载；这些报告限定本次设备和输入，不保证所有设备 200 ms，也未覆盖全量客户端 GRP 的构建规模。 [重复构建报告](../reports/release-determinism.json)另外验证固定输入下全部 6049 个静态文件字节不变、增量 0；这与真实数据变化后的增量构建规模是不同测量。

## 逐项要求映射

证据列为仓库相对链接；`ET` 是引擎测试、`PT` 是管线测试、`ST` 是来源测试、`SUMT` 是摘要测试、`BT` 是浏览器测试源码，`BR` 是浏览器最终结果。测试名片段或函数名用于定位；JSON 中保留完整路径、定位说明和原始条款。

### 产品边界与默认条件

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| B01 | `implemented` | Ground RB 字段控制地面、飞机、直升机合法性；26 条未知模式 BR 不进入推荐。 [SRC·模式解析](../backend/wt_atlas/sources.py)；[ST·air_rb_is_never_used_as_ground_rb](../tests/test_sources.py)；[ET·unverified Ground RB](../tests/engine.test.mjs)；[SC·ground_rb_known](../reports/source-coverage.json) | 未知 BR 不用 Air RB 替代。 |
| B02 | `implemented` | 10 国 2677 条目录进入科技树与档案。 [SRC·目录详情](../backend/wt_atlas/sources.py)；[SC·catalog_vehicles](../reports/source-coverage.json)；[U·renderTree](../web/app.mjs) | 全目录覆盖不代表全属性或物理配对覆盖。 |
| B03 | `implemented` | 当前快照与模拟方案分开，400 条接受变更仅应用于 planned。 [E·applyScenario](../shared/engine.mjs)；[QQ·调整解析](../backend/wt_atlas/tencent.py)；[DI·applied_changes](../reports/decision-integrity.json)；[U·renderVersions](../web/app.mjs) | 计划不冒充已实装。 |
| B04 | `implemented` | 默认 s=0 来自配置；连续技能及离线校准接口可用。 [M·defaults](../config/model.json)；[E·applyCalibration](../shared/engine.mjs)；[ET·calibration imports](../tests/engine.test.mjs) | 默认行为和正态先验未经实战校准。 |
| B05 | `partial` | 人工王牌与已识别自动机制分开，选弹绑定其自身武器装填。 [E·evaluatePair](../shared/engine.mjs)；[SRC·weapon_profiles](../backend/wt_atlas/sources.py)；[ET·selected missile launch interval](../tests/engine.test.mjs)；[SC·with_ace_reload](../reports/source-coverage.json) | 915 条有王牌、161 条有自动装填；其它机制/周期保持未知。 |
| B06 | `partial` | 性能状态、航空速度高度和配对条件可追溯。 [SRC·性能状态](../backend/wt_atlas/sources.py)；[ST·air_maximum_speed](../tests/test_sources.py)；[E·evaluatePair](../shared/engine.mjs)；[U·openVehicle](../web/app.mjs) | 部分研发/构型身份未核验；Wiki 游戏提交未知，不能视为旧物理来源同版。 |
| B07 | `implemented` | 档案和矩阵显式显示缺项、资料覆盖、估计层级与区间。 [E·evaluatePair](../shared/engine.mjs)；[U·openPair](../web/app.mjs)；[ET·unknown input](../tests/engine.test.mjs)；[SC·limitations](../reports/source-coverage.json) | 缺资料会扩大区间或返回 null，不能解释为低性能。 |

### 版本影响

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| V01 | `implemented` | 国家、BR、版本、队列和交战条件驱动一致比较。 [U·switchContext / compare](../web/app.mjs)；[E·compareVersions](../shared/engine.mjs)；[ET·AT01 / AT11](../tests/engine.test.mjs) | 浏览器结果独立见 BR 报告。 |
| V02 | `implemented` | 树卡展示两版 BR 和升降标记，支持变更筛选。 [U·renderTree](../web/app.mjs)；[BT·科技树与响应式](../tests/browser.e2e.mjs)；[SA·调整审计](../reports/source-audit.json) | 旧值/新值属于冻结快照和模拟方案。 |
| V03 | `partial` | 结构化非 BR 变化可审计应用，敌我双方同版重算。 [E·applyScenario](../shared/engine.mjs)；[ET·non-BR physical changes](../tests/engine.test.mjs)；[U·applyScenarioToIndex](../web/app.mjs) | 生产接受的 400 条为 BR 调整；缺完整已核验非 BR 变更集。 |
| V04 | `implemented` | 两版固定同一模型配置，构建身份包含引擎和全部输入哈希。 [E·compareVersions](../shared/engine.mjs)；[BUILD·构建身份](../scripts/build_decisions.mjs)；[DI·input_hashes_match](../reports/decision-integrity.json) | 算法升级须新建身份，不能当作游戏平衡变化。 |
| V05 | `implemented` | 旧值冲突、重复、未知实体、文字行和非法数值形成审计。 [E·applyScenario](../shared/engine.mjs)；[ET·conflicts / invalid numeric config](../tests/engine.test.mjs)；[ST·adjustment_conflicts](../tests/test_sources.py)；[SA·审计](../reports/source-audit.json) | 拒绝行不会静默覆盖。 |
| V06 | `implemented` | 全 10 国两版 20 分片，350 当前/373 方案候选，379 国别 BR 并集。 [BUILD·全国扫描](../scripts/build_decisions.mjs)；[DI·candidate_counts](../reports/decision-integrity.json)；[ET·AT13](../tests/engine.test.mjs) | 扫描所有 BR 不等于穷举全部组合。 |
| V07 | `implemented` | 显示对手进出、威胁分类变化和实际最高 BR。 [E·compareVersions changed_threats](../shared/engine.mjs)；[ET·AT03](../tests/engine.test.mjs)；[U·renderVersions / impactMap](../web/app.mjs) | 威胁分类是条件模型输出；低 BR 仍可被高档携带。 |
| V08 | `implemented` | 修改后的实体集合同时用于方案版己方与敌方。 [E·compareVersions](../shared/engine.mjs)；[ET·AT03 / non-BR physical changes](../tests/engine.test.mjs) | 不跨版拼接双方数据。 |
| V09 | `implemented` | 全部成员含未变成员参与异步 BR 检查，提供全组合与地面替代。 [E·compareVersions memberDeltas](../shared/engine.mjs)；[ET·AT01 / unchanged member](../tests/engine.test.mjs) | 拆分表示匹配和协同成本变化。 |
| V10 | `implemented` | 公告明确仍可一起编队，解释最高 BR 和组合成本。 [E·compareVersions announcements](../shared/engine.mjs)；[ET·AT01](../tests/engine.test.mjs)；[U·announcementView](../web/app.mjs) | 不声称永久禁止编队。 |
| V11 | `implemented` | 公告包含事件、国家/成员及替换、升档或矩阵定位动作。 [E·compareVersions](../shared/engine.mjs)；[U·announcementView](../web/app.mjs)；[ET·AT03](../tests/engine.test.mjs) | 建议依赖当前资料与公开先验。 |
| V12 | `partial` | 同版敌方防空、航空窗口、目标机动和航空可用性联动。 [E·evaluateLineup / compareVersions](../shared/engine.mjs)；[U·renderVersions](../web/app.mjs)；[ET·AT06 / AT07](../tests/engine.test.mjs) | 雷达扫描、包线、装甲响应和 SP 不完整，未形成已验证实际 CAS 时空窗口。 |
| V13 | `implemented` | 固定旧队列 before/after_fixed 与 after_optimized 分开。 [E·compareVersions](../shared/engine.mjs)；[ET·AT01 / AT11](../tests/engine.test.mjs)；[U·comparisonCard](../web/app.mjs) | 原成员损失不会被新推荐掩盖。 |
| V14 | `implemented` | 全国影响图与推荐表可定位国家、BR 和成员的前后变化。 [U·impactMap / nationalRecommendations](../web/app.mjs)；[BUILD·impact rows](../scripts/build_decisions.mjs)；[DI·impact_nation_br_union_count](../reports/decision-integrity.json) | 无合法候选或缺数据不填零。 |
| V15 | `implemented` | 公告、威胁和影响单元格可进入档案、队列或矩阵。 [U·announcementView / handleClick](../web/app.mjs)；[BT·版本和矩阵流程](../tests/browser.e2e.mjs) | 最终交互结论见 BR 报告。 |

### 推荐与合法组队

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| Q01 | `implemented` | 各国所有已知合法 Ground RB 上限分别扫描两版。 [E·getBRs / recommendNation](../shared/engine.mjs)；[BUILD·全国循环](../scripts/build_decisions.mjs)；[DI·candidate_counts](../reports/decision-integrity.json)；[ET·AT13](../tests/engine.test.mjs) | 未知模式 BR 不加入上限集合。 |
| Q02 | `implemented` | 校验国家、真实 BR、槽位、地面成员与重复系统。 [E·matchEnvironment / recommendNation](../shared/engine.mjs)；[ET·unverified Ground RB / AT02](../tests/engine.test.mjs)；[SC·coverage](../reports/source-coverage.json) | 合法性范围限定已核验来源。 |
| Q03 | `implemented` | 匹配由成员实际最高 BR 确定，不用候选上限抬分。 [E·matchEnvironment](../shared/engine.mjs)；[ET·AT02 / high aircraft-only cap](../tests/engine.test.mjs) | 高档航空可使用低档地面锚点，但按真实最高 BR 分析。 |
| Q04 | `implemented` | 槽位、特种载具和拥有 ID 集合约束可操作。 [E·recommendNation](../shared/engine.mjs)；[U·renderLineup / editOwned](../web/app.mjs)；[ET·unverified Ground RB](../tests/engine.test.mjs) | 未提供拥有列表时采用明确全可用假设。 |
| Q05 | `implemented` | 角色约束和边际对手覆盖驱动组合，比较地面/均衡/空地任务顺序。 [E·recommendNation](../shared/engine.mjs)；[M·lineup](../config/model.json)；[ET·AT13](../tests/engine.test.mjs)；[ED·组队搜索](../docs/DECISION_ENGINE.md) | 角色效用权重待校准。 |
| Q06 | `implemented` | 可增删、导入导出自选队列和采用推荐，使用同一 Worker 引擎。 [U·renderLineup / adoptCandidate](../web/app.mjs)；[W·消息处理](../web/worker.mjs)；[BT·自选队列](../tests/browser.e2e.mjs) | 自定义结果按当前条件重算。 |
| Q07 | `implemented` | 公开候选剪枝和确定性启发式范围。 [E·recommendNation](../shared/engine.mjs)；[ED·组队搜索](../docs/DECISION_ENGINE.md)；[ET·AT13](../tests/engine.test.mjs) | 不保证全局组合最优。 |
| Q08 | `partial` | 相关敌方逐车配对，保留弹药/挂载/条件并独立反算。 [E·evaluatePair / evaluateLineup](../shared/engine.mjs)；[U·openPair](../web/app.mjs)；[ET·AT04 / reverse response](../tests/engine.test.mjs)；[SUMR·verified_records](../reports/probability-summaries.json) | 生产精确配对为 0；大量装甲响应仅能保持未知或明确粗模型。 |
| Q09 | `implemented` | 对下、同档、升房、不利情景及上下界使用一致情景先验。 [E·evaluateLineup](../shared/engine.mjs)；[ET·narrow uncertainty bounds](../tests/engine.test.mjs)；[M·情景先验](../config/model.json) | 比例未经排队统计验证。 |
| Q10 | `partial` | 威胁、优势、匹配边界与版本进出定位具体敌人。 [E·evaluateLineup / compareVersions](../shared/engine.mjs)；[U·threatsView](../web/app.mjs)；[ET·AT03](../tests/engine.test.mjs) | 未核验全车强敌排序与实际反制能力。 |
| Q11 | `implemented` | 规则边界、预计威胁和低 BR 携带分开说明。 [E·matchEnvironment](../shared/engine.mjs)；[ET·AT02](../tests/engine.test.mjs)；[ED·组队搜索](../docs/DECISION_ENGINE.md) | 不承诺提高降房概率。 |
| Q12 | `partial` | 合法航空预设、敌方防空、机动、数量与出击条件参与效用。 [E·evaluatePair / evaluateLineup](../shared/engine.mjs)；[ET·AT06 / AT07](../tests/engine.test.mjs)；[SC·legal_whole_presets](../reports/source-coverage.json) | 现代武器包线/质量、装甲效应和完整混挂仍缺数据。 |
| Q13 | `implemented` | AA-AA 降权只作用汇总，保留单次原始分析。 [E·evaluatePair / evaluateLineup](../shared/engine.mjs)；[ET·AT06](../tests/engine.test.mjs)；[M·配对权重](../config/model.json) | 系数是待校准配置。 |
| Q14 | `implemented` | AA-air 单独评价，不沿用 AA-AA 系数；对地按实际武器。 [E·evaluatePair](../shared/engine.mjs)；[ET·AT06](../tests/engine.test.mjs)；[ED·防空与航空](../docs/DECISION_ENGINE.md) | 角色重要性不代表资料完整。 |
| Q15 | `partial` | 支持可用 SP 和成本，缺项使用显式航空可用性先验。 [E·evaluatePair](../shared/engine.mjs)；[U·renderLineup](../web/app.mjs)；[M·航空先验](../config/model.json) | 缺完整 SP/成本集，未模拟整局获取与多次出击。 |
| Q16 | `implemented` | 对手频率、升降房和等价组质量均是可替换公开先验。 [E·matchEnvironment / evaluateLineup](../shared/engine.mjs)；[M·先验](../config/model.json)；[ET·AT05 / AT13](../tests/engine.test.mjs) | 默认分布不称实测。 |
| Q17 | `implemented` | 展示平均、三档情景、最不利表现及未知区间。 [E·evaluateLineup](../shared/engine.mjs)；[U·metricsView](../web/app.mjs)；[ET·narrow uncertainty bounds](../tests/engine.test.mjs) | 模型不利情景不等于实战尾部损失校准。 |
| Q18 | `partial` | 候选输出 BR、成员、弹药、任务和已核验整套航空预设。 [E·recommendNation](../shared/engine.mjs)；[U·candidateCards / lineupCards](../web/app.mjs)；[SC·legal_whole_presets](../reports/source-coverage.json) | 421 架有 1249 套预设；733 条自选挂点资料不等于完整合法混挂。 |
| Q19 | `implemented` | 提供最佳、备选和不同任务取向的同模型比较。 [E·recommendNation](../shared/engine.mjs)；[U·candidateCards](../web/app.mjs)；[ET·AT13](../tests/engine.test.mjs) | 取向不保证用户发挥或战局收益。 |
| Q20 | `partial` | 推荐展开优势、威胁、版本与地空条件，可进入矩阵。 [E·evaluateLineup explanations](../shared/engine.mjs)；[U·openPair](../web/app.mjs)；[ET·AT03](../tests/engine.test.mjs) | 完整反制、强敌避让与地空解释受数据缺口限制。 |
| Q21 | `implemented` | 显示方向、模型版本、覆盖、缺项和条件，声明未校准指数。 [E·evaluatePair](../shared/engine.mjs)；[U·openPair / renderAudit](../web/app.mjs)；[ET·unknown input](../tests/engine.test.mjs)；[ED·分数解释](../docs/DECISION_ENGINE.md) | 不是游戏胜率、击毁率或保证收益。 |

### 档案与逐车对抗

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| C01 | `partial` | 档案展示已有机动、弹药表、装填、炮控、观瞄、烟幕、重机枪及名义装甲。 [SRC·详情字段](../backend/wt_atlas/sources.py)；[U·openVehicle](../web/app.mjs)；[SC·coverage](../reports/source-coverage.json)；[ST·weapon_scope](../tests/test_sources.py) | 全车区域防护、弹药特异 RHAe、雷达和辅助字段尚不完整。 |
| C02 | `implemented` | 物理厚度、中间消耗量与概率分离，不用名义厚度虚构 RHAe。 [E·evaluatePair](../shared/engine.mjs)；[U·openVehicle](../web/app.mjs)；[ET·unknown input](../tests/engine.test.mjs)；[HC·字段语义](../docs/PROBABILITY_CONTRACT.md) | 复合装甲物理厚度不等于均质防护。 |
| C03 | `implemented` | 真实弹药所有权、参数哈希、构型与条件全匹配；M829A2 不复用给 XM885。 [E·findExactProbability](../shared/engine.mjs)；[ET·AT04](../tests/engine.test.mjs)；[SUMT·explicitly verified fixture](../tests/probability_summaries.test.mjs) | 测试中的核验身份是夹具，生产精确配对仍为 0。 |
| C04 | `implemented` | 正反各自计算，缺反向时不使用 1 减正向。 [E·evaluateLineup](../shared/engine.mjs)；[ET·reverse response](../tests/engine.test.mjs)；[U·openPair](../web/app.mjs) | 两向可有独立缺项与区间。 |
| C05 | `implemented` | 配对记录完整来源/构型/弹药/物理与瞄准条件，严格摘要身份校验。 [E·findExactProbability / aimContextKey](../shared/engine.mjs)；[ET·AT04 / aim identity](../tests/engine.test.mjs)；[SUMT·explicitly verified fixture](../tests/probability_summaries.test.mjs) | 缺身份拒绝精确命中，null 不作为同一身份依据。 |
| C06 | `partial` | 机动、观瞄、炮控和装填作为可解释组件与落点输入，权重统一配置。 [E·evaluatePair / integrateAim](../shared/engine.mjs)；[ET·AT09](../tests/engine.test.mjs)；[ED·地面对抗](../docs/DECISION_ENGINE.md) | 尚无完整发现、暴露、先手与持续交战因果模型或实战校准。 |
| C07 | `partial` | 瞄准与装填可重叠；射击机会、穿透与击毁概念分开。 [E·evaluatePair](../shared/engine.mjs)；[ED·射击节奏](../docs/DECISION_ENGINE.md)；[ET·selected missile launch interval](../tests/engine.test.mjs) | 受损部件、破片、殉爆、ERA 消耗及首发未达标后的状态转移未实现。 |

### 防空与航空

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| A01 | `partial` | 保留来源防空字段，25 条 SAM 射程及部分投射数据可见。 [SRC·air_defence](../backend/wt_atlas/sources.py)；[U·openVehicle](../web/app.mjs)；[SC·with_sam_range](../reports/source-coverage.json) | 过载、架上弹、扫描角/周期和全车机炮投射不全。 |
| A02 | `partial` | 20 条记录关联 10 套系统，组件不继承 BR，重复系统占槽被拒绝。 [SRC·multi_vehicle_system](../backend/wt_atlas/sources.py)；[E·matchEnvironment](../shared/engine.mjs)；[ST·multi_vehicle_membership](../tests/test_sources.py)；[ET·duplicated systems](../tests/engine.test.mjs) | 发射组件性能未聚合；架上/总弹、搜索/跟踪模式和视线数据不完整。 |
| A03 | `partial` | 按具体武器 ID、数量、质量、射程和命中条件分析，不按制导类别直接等分。 [E·evaluatePair](../shared/engine.mjs)；[LOAD·武器展开](../backend/wt_atlas/loadouts.py)；[ST·nested_container_counts](../tests/test_sources.py)；[SC·legal_whole_presets](../reports/source-coverage.json) | AGM-65D/Kh-38MT 等完整包线与质量未知时不作已验证效能排序。 |
| A04 | `partial` | 最大射程与可靠命中条件分开，不视为任何条件下的保证范围。 [E·evaluatePair](../shared/engine.mjs)；[ED·航空](../docs/DECISION_ENGINE.md)；[U·openVehicle](../web/app.mjs) | 飞行包线、导引头、锁定与遮蔽模型未完整核验。 |
| A05 | `partial` | 单次分析只选合法整套预设，不叠加互斥预设最大数量。 [E·validateLoadout](../shared/engine.mjs)；[ST·nested_container_counts](../tests/test_sources.py)；[ET·AT07](../tests/engine.test.mjs)；[SC·legal_whole_presets](../reports/source-coverage.json) | 现代自选挂点全部合法组合尚未取得/核验。 |
| A06 | `partial` | 可校验挂点、质量、冲突、依赖；ARM 依赖目标雷达辐射。 [E·validateLoadout](../shared/engine.mjs)；[ET·AT07](../tests/engine.test.mjs)；[LOAD·挂载展开](../backend/wt_atlas/loadouts.py) | 生产全目录约束与雷达状态数据不齐。 |

### 防护数据语义

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| H01 | `implemented` | 按角度/形状读取概率、status 和材料 flags 三平面。 [P·切片导出](../backend/wt_atlas/probability.py)；[DC·decodeProbability](../web/data-client.mjs)；[PT·quantization_retains_zero_unknown_and_flags](../tests/test_pipeline.py)；[SUMT·real binary integration](../tests/probability_summaries.test.mjs) | 来源为上传的条件模型结果。 |
| H02 | `implemented` | 模型消耗量保留中间量语义，不发布为 RHAe。 [P·输出选择](../backend/wt_atlas/probability.py)；[CPU·模型输出](../backend/wt_atlas/cpu_model.py)；[HC·model_required_charge_mm](../docs/PROBABILITY_CONTRACT.md)；[ET·unknown input](../tests/engine.test.mjs) | 中间量不能替代游戏校准。 |
| H03 | `implemented` | 背景、无内部目标、未知、条件估计独立；NaN 与真实零不同。 [P·量化状态](../backend/wt_atlas/probability.py)；[DC·decodeProbability](../web/data-client.mjs)；[ET·AT08](../tests/engine.test.mjs)；[PT·background_no_inner_unknown_and_true_zero](../tests/test_pipeline.py) | 未知质量和区间保留。 |
| H04 | `implemented` | 状态 1 只解释当前受保护内部目标指标。 [U·drawArmor / renderAim](../web/app.mjs)；[HC·status](../docs/PROBABILITY_CONTRACT.md)；[ET·AT08](../tests/engine.test.mjs)；[PT·background_no_inner_unknown_and_true_zero](../tests/test_pipeline.py) | 不宣称整车不可受损或必定未穿透。 |
| H05 | `implemented` | 角度、尺寸、方向、间距由元数据驱动，缩放仅影响显示。 [P·动态索引](../backend/wt_atlas/probability.py)；[DC·decodeProbability](../web/data-client.mjs)；[U·drawArmor](../web/app.mjs)；[PT·dynamic_grid_and_angle_order](../tests/test_pipeline.py) | HD 图没有新增射线。 |
| H06 | `implemented` | 通用解码/积分支持动态 shape，不固定示例维度和角数。 [E·integrateAim](../shared/engine.mjs)；[P·尺寸校验](../backend/wt_atlas/probability.py)；[PT·dynamic_grid_and_angle_order](../tests/test_pipeline.py)；[ET·AT08](../tests/engine.test.mjs) | 上传源和夹具尺寸不是永久产品常数。 |
| H07 | `implemented` | 校验 ray_id 索引、node 层绑定；无出口不作为正常厚度。 [G·索引](../backend/wt_atlas/geometry.py)；[CPU·层计算](../backend/wt_atlas/cpu_model.py)；[BIND·材料](../backend/wt_atlas/bindings.py)；[PT·bad_ray_id / solid_missing_exit](../tests/test_pipeline.py) | 已处理本地地面目录 937 份不同 GRP；其它版本和未纳入目录的类别另行验证。 |
| H08 | `implemented` | 缓存含目标、构型、精确弹药/哈希、物理条件、全部版本和瞄准身份。 [E·findExactProbability / aimContextKey](../shared/engine.mjs)；[PT·physics_key_excludes_br](../tests/test_pipeline.py)；[ET·AT04 / aim identity](../tests/engine.test.mjs) | 参考图不构成生产载具弹药所有权证明。 |
| H09 | `implemented` | 精确未命中时为有标记粗组件或未知，不借相近弹图冒充。 [E·evaluatePair](../shared/engine.mjs)；[ET·AT04 / reference-only heatmaps](../tests/engine.test.mjs)；[SUMT·explicitly verified fixture](../tests/probability_summaries.test.mjs) | 生产精确配对 0，参考摘要 192。 |
| H10 | `implemented` | 标为分层直线路径条件估计，明确不是游戏穿透/击毁/胜率。 [U·renderArmor / renderAudit](../web/app.mjs)；[HC·模型范围](../docs/PROBABILITY_CONTRACT.md)；[CV·method](../reports/cpu-model-validation.json)；[CG·scope](../reports/cpu-geometry-validation.json) | 来源模型一致性不是游戏验证；几何边缘仍有差异。 |

### 玩家与瞄准模型

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| S01 | `implemented` | s=0 默认运行，不依赖登录或实时统计服务。 [M·defaults.skill](../config/model.json)；[E·integrateAim](../shared/engine.mjs)；[ET·AT09](../tests/engine.test.mjs)；[BT·静态启动](../tests/browser.e2e.mjs) | 平均技能是默认约定。 |
| S02 | `implemented` | 连续技能与正态潜变量接口可配置并标未校准。 [M·技能配置](../config/model.json)；[E·applyCalibration](../shared/engine.mjs)；[ET·calibration imports](../tests/engine.test.mjs)；[ED·瞄准](../docs/DECISION_ENGINE.md) | 尚无统计证据证明该分布符合当前玩家群。 |
| S03 | `implemented` | 一般中心与可穿区稳健中心构成技能相关非均匀瞄准。 [E·integrateAim / robustCenter](../shared/engine.mjs)；[ET·AT09 / aim centers](../tests/engine.test.mjs) | 混合偏好是待校准假设。 |
| S04 | `implemented` | 选点与落点误差分开；厚甲、未知、背景及目标外质量不截断。 [E·integrateAim](../shared/engine.mjs)；[ET·AT08 / AT09](../tests/engine.test.mjs)；[SUMT·real binary integration](../tests/probability_summaries.test.mjs) | 质量守恒不代表散布假设已验证。 |
| S05 | `implemented` | 瞄点选在有效目标像素，环形空洞质心会替换为内部点。 [E·nearestValidCenter / robustCenter](../shared/engine.mjs)；[ET·aim centers](../tests/engine.test.mjs) | 稳健点选法是确定性启发式。 |
| S06 | `implemented` | 技能、距离、双方运动、炮控和横纵误差进入模型与摘要身份。 [E·integrateAim / aimContextKey](../shared/engine.mjs)；[ET·AT09 / aim identity](../tests/engine.test.mjs)；[U·renderArmor](../web/app.mjs) | 系数待校准，不由总体 K/D 推厘米误差。 |
| S07 | `partial` | 弱点偏好、机动及射速的技能行为倾向开放配置，不改变装甲。 [E·evaluatePair / integrateAim](../shared/engine.mjs)；[M·技能配置](../config/model.json)；[ET·AT09](../tests/engine.test.mjs) | 单发毁伤/容错与技能的完整关系缺损伤模型和统计验证。 |
| S08 | `implemented` | 完整落点与条件图积分，区分已知、未知、无内部目标与落空质量。 [E·integrateAim](../shared/engine.mjs)；[ET·AT08](../tests/engine.test.mjs)；[SUMT·real binary integration](../tests/probability_summaries.test.mjs)；[SUMR·integrated_samples](../reports/probability-summaries.json) | 单发效用不是每发击毁率。 |
| S09 | `implemented` | 瞄准与物理距离独立；改瞄距不改穿深图，旧瞄准摘要拒绝命中。 [E·aimContextKey / integrateAim](../shared/engine.mjs)；[U·renderArmor](../web/app.mjs)；[ET·AT10](../tests/engine.test.mjs)；[SUMT·explicitly verified fixture](../tests/probability_summaries.test.mjs) | 物理新距离需要对应离线结果。 |
| S10 | `implemented` | 离线校准接口要求来源、模式、时间窗、样本与验证依据。 [E·applyCalibration](../shared/engine.mjs)；[ET·calibration imports](../tests/engine.test.mjs)；[ED·接口](../docs/DECISION_ENGINE.md) | 接口完成不等于已取得 StatShark 数据或完成校准。 |

### 科技树与换皮

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| T01 | `implemented` | 官方各国树保留等级、分支、文件夹和前置关系，覆盖地面与航空。 [SRC·树结构](../backend/wt_atlas/sources.py)；[U·renderTree](../web/app.mjs)；[SC·coverage](../reports/source-coverage.json)；[BT·科技树与响应式](../tests/browser.e2e.mjs) | 战斗配置的缺项单独显示。 |
| T02 | `implemented` | 国家/BR/版本联动可编队、推荐及威胁，切换清理旧分析。 [U·switchContext / renderTree](../web/app.mjs)；[E·matchEnvironment](../shared/engine.mjs)；[BT·自选队列](../tests/browser.e2e.mjs) | 目标状态的数据加载后再计算。 |
| T03 | `implemented` | 主力范围与高 BR 携带低 BR 分开，不声称离开主力即绝不遇到。 [E·matchEnvironment / compareVersions](../shared/engine.mjs)；[ET·AT02 / AT03](../tests/engine.test.mjs)；[U·renderVersions](../web/app.mjs) | 无遥测不推断携带频率。 |
| T04 | `partial` | 显式核验等价组支持折叠和展开，保留成员档案。 [E·foldEquivalents](../shared/engine.mjs)；[U·renderMatrix](../web/app.mjs)；[ET·AT05](../tests/engine.test.mjs) | 全目录已核验生产等价组资料尚未建立。 |
| T05 | `implemented` | BMPT-72 与 T-72B 为独立实体/构型，不按底盘折叠。 [E·foldEquivalents](../shared/engine.mjs)；[ET·AT05](../tests/engine.test.mjs)；[GD·独立四车资源](../reports/grp-decode-validation.json) | 没有关键构型等价证据不合并。 |
| T06 | `implemented` | 豹 2 各代按独立 ID/构型分析，族名不替代构型。 [E·foldEquivalents](../shared/engine.mjs)；[ET·AT05](../tests/engine.test.mjs)；[SRC·载具 ID](../backend/wt_atlas/sources.py) | 共同车族只是关系信息。 |
| T07 | `implemented` | 折叠要求核验标记和等价组，同底盘/材料名/族名不足以通过。 [E·foldEquivalents](../shared/engine.mjs)；[ET·AT05](../tests/engine.test.mjs)；[U·renderMatrix](../web/app.mjs) | 真实分组覆盖缺口见 T04。 |
| T08 | `implemented` | 折叠不改矩阵；等价组先验保持总质量，不因多换皮加出现率。 [E·foldEquivalents / matchEnvironment](../shared/engine.mjs)；[ET·AT05](../tests/engine.test.mjs)；[U·renderMatrix](../web/app.mjs) | 频率仍是公开先验。 |
| T09 | `partial` | 推荐、档案和矩阵共享全局状态，敌人可展开己方结果与来源。 [U·openPair / openVehicle / switchContext](../web/app.mjs)；[W·上下文](../web/worker.mjs)；[BT·矩阵详情](../tests/browser.e2e.mjs) | 本地全地面目录两份参考弹图仍独立于生产精确配对，弹药身份门禁保持有效。 |

### 来源、版本与校准

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| D01 | `implemented` | 官方 Wiki 供目录、树、Ground RB 与属性，缓存和转换可追溯。 [SRC·采集解析](../backend/wt_atlas/sources.py)；[SD·来源](../docs/SOURCES.md)；[SC·coverage](../reports/source-coverage.json)；[ST·air_rb_is_never_used_as_ground_rb](../tests/test_sources.py) | 游戏提交未知，不补写猜测版本。 |
| D02 | `implemented` | 腾讯原始行与实体映射保留，仅进入模拟计划。 [QQ·表解析](../backend/wt_atlas/tencent.py)；[ST·tencent_public_binary / adjustment_conflicts](../tests/test_sources.py)；[SA·调整审计](../reports/source-audit.json) | 400 条接受表示基线核验通过，不代表已实装。 |
| D03 | `partial` | 本地 1,225 个地面构型的材料绑定与 CPU 计算完成，另索引 773 份武器来源及 3,567 条弹丸定义。 [J·任务](../backend/wt_atlas/jobs.py)；[G·解码](../backend/wt_atlas/geometry.py)；[BIND·绑定](../backend/wt_atlas/bindings.py)；[GD·四文件](../reports/grp-decode-validation.json)；[PE·scope](../reports/pipeline-e2e.json) | 地面目录 GRP 与材料已齐；雷达语义、全弹药弹道及生产精确配对仍需补齐。 |
| D04 | `implemented` | 来源、时间、转换、哈希和未知游戏版本可见，Wiki 与旧物理来源分开。 [SRC·provenance](../backend/wt_atlas/sources.py)；[SUM·provenance](../scripts/build_probability_summaries.mjs)；[SC·limitations](../reports/source-coverage.json)；[SUMR·bundle_sha256](../reports/probability-summaries.json) | 未知身份会阻止精确匹配，不假定 Wiki 与 Datamine 同提交。 |
| D05 | `partial` | 载具稳定 ID、归属车弹药 ID 防止同名跨车套用。 [SRC·实体 ID](../backend/wt_atlas/sources.py)；[E·selectAmmunition / findExactProbability](../shared/engine.mjs)；[ET·reference-only heatmaps](../tests/engine.test.mjs)；[ST·nested_container_counts](../tests/test_sources.py) | 弹药 ID 含显示名；历史别名/改名及跨来源 canonical 绑定未齐。 |
| D06 | `partial` | 单位、装填范围、负俯角、航空速度高度、嵌套绑定有转换和回归。 [SRC·单位状态](../backend/wt_atlas/sources.py)；[BIND·配置](../backend/wt_atlas/bindings.py)；[ST·physical_units / four_upward_only / air_maximum_speed](../tests/test_sources.py)；[PT·nested_damageparts](../tests/test_pipeline.py) | 全客户端继承、改装和运行时材料行为未全面核验。 |
| D07 | `implemented` | 来源、冲突、缺项、未映射与版本不一致有可见审计，不用零补齐。 [U·renderAudit](../web/app.mjs)；[E·applyScenario](../shared/engine.mjs)；[SA·审计](../reports/source-audit.json)；[SC·limitations](../reports/source-coverage.json)；[ET·conflicts](../tests/engine.test.mjs) | 目录与物理精度覆盖分开。 |
| D08 | `partial` | 模型版本、内容哈希、增量缓存和固定输入复现已验证，BR-only 复用物理。 [BUILD·构建身份](../scripts/build_decisions.mjs)；[J·恢复缓存](../backend/wt_atlas/jobs.py)；[PT·physics_key_excludes_br](../tests/test_pipeline.py)；[ET·AT13](../tests/engine.test.mjs)；[DI·input_hashes_match](../reports/decision-integrity.json)；[RD·same_input_rebuild](../reports/release-determinism.json) | 完整历史归档和跨历史回滚操作尚未独立端到端验证。 |
| D09 | `needs-data` | 具备要求统计元数据与验证证据的离线校准入口。 [E·applyCalibration](../shared/engine.mjs)；[ET·calibration imports](../tests/engine.test.mjs)；[M·calibration_status](../config/model.json) | 未取得合格真实统计集，尚无偏差控制、外部验证与参数拟合。 |

### 静态架构与资源

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| L01 | `implemented` | 普通静态 HTTP 与 Worker 运行查询及自定义分析，无常驻业务依赖。 [U·boot](../web/app.mjs)；[W·运算](../web/worker.mjs)；[WB·构建](../scripts/build_web.py)；[BT·静态启动](../tests/browser.e2e.mjs)；[WR·业务依赖字段](../reports/web-build.json) | 浏览器最终结果单独见 BR 报告。 |
| L02 | `implemented` | 采集、GRP 解码、射线、摘要、全国扫描和公告有离线入口。 [J·管线](../backend/wt_atlas/jobs.py)；[SUM·摘要](../scripts/build_probability_summaries.mjs)；[BUILD·预计算](../scripts/build_decisions.mjs)；[PE·scope](../reports/pipeline-e2e.json)；[DR·elapsed_ms](../web/data/recommendations/build-report.json) | 本地地面目录的两份参考弹全视角计算已完成；其它弹药与毁伤机制未由此推定完成。 |
| L03 | `implemented` | 发布轻量推荐和按需切片，首页无 GRP/原始 NPZ/射线层。 [WB·发布内容](../scripts/build_web.py)；[P·导出](../backend/wt_atlas/probability.py)；[BT·静态启动](../tests/browser.e2e.mjs)；[WR·raw_geometry_first_load_files](../reports/web-build.json) | 离线原始资料不进入启动依赖。 |
| L04 | `implemented` | 版本/国家推荐分片内索引 BR，档案按车，概率按车/弹/角惰性取用。 [BUILD·分片](../scripts/build_decisions.mjs)；[WB·档案分片](../scripts/build_web.py)；[DC·fetchJSON / fetchBytes](../web/data-client.mjs)；[BT·按需热力图](../tests/browser.e2e.mjs) | 不是每个 BR 独立文件；粒度以相关视图与传输为准。 |
| L05 | `implemented` | 研究图进入研究页才下载，科技树和推荐无需四车热力图启动。 [U·loadArmor / loadArmorSlice](../web/app.mjs)；[BT·静态启动 / 按需热力图](../tests/browser.e2e.mjs)；[WR·首屏组成](../reports/web-build.json) | 完整原始物理资料单独保留。 |
| L06 | `implemented` | 只发布有限候选与解释，不物化全条件稠密矩阵；自选队列按需算。 [E·recommendNation](../shared/engine.mjs)；[BUILD·轻量结果](../scripts/build_decisions.mjs)；[DR·full_matrices_materialized](../web/data/recommendations/build-report.json)；[ET·AT13](../tests/engine.test.mjs) | 未构造全部角度/距离/技能/版本笛卡尔积。 |
| L07 | `implemented` | BR-only 复用物理，武器/防护变化标记精确缓存失效。 [E·applyScenario / findExactProbability](../shared/engine.mjs)；[PT·physics_key_excludes_br](../tests/test_pipeline.py)；[ET·AT04](../tests/engine.test.mjs) | 新物理输入仍须离线重算。 |
| L08 | `implemented` | 矩阵、版本比较、局部搜索和瞄准积分在 Worker 调用共享引擎。 [W·消息处理](../web/worker.mjs)；[U·workerCall / evaluate](../web/app.mjs)；[BT·自选队列 / 按需热力图](../tests/browser.e2e.mjs) | 完整全国自定义重算可能耗时，GRP 物理不在浏览器计算。 |
| L09 | `implemented` | 偏好/成员存 localStorage，可文件导入导出，无账号数据库。 [U·readSaved / save / import-lineup](../web/app.mjs)；[BT·自选队列](../tests/browser.e2e.mjs) | 用户清理浏览器会删除本地偏好。 |
| L10 | `implemented` | 内容哈希、gzip、有限请求缓存和概率量化保留真零/未知并验证误差。 [DC·fetchBytes / decodeProbability](../web/data-client.mjs)；[P·量化](../backend/wt_atlas/probability.py)；[PT·quantization_retains_zero_unknown_and_flags / hash_checked](../tests/test_pipeline.py)；[SUMT·compressed source hashes](../tests/probability_summaries.test.mjs) | 误差结论限定实际导出数据。 |
| L11 | `implemented` | 静态目录、源码、可复现命令可由普通 HTTP 服务运行。 [README·构建启动](../README.md)；[WB·构建](../scripts/build_web.py)；[BT·静态启动](../tests/browser.e2e.mjs)；[WR·报告](../reports/web-build.json) | 最终分发包完整性与浏览器收尾单列 F05。 |
| L12 | `implemented` | 正式站点为多文件目录与惰性分片，未迫使首屏全量打包。 [WB·目录输出](../scripts/build_web.py)；[DC·按需请求](../web/data-client.mjs)；[WR·首屏组成](../reports/web-build.json) | 可选单文件离线导出本轮未提供。 |
| L13 | `partial` | 记录构建、存储、首载及浏览器请求/内存/交互；固定输入重复构建的全部静态文件字节不变、增量为零。 [WR·资源计量](../reports/web-build.json)；[DR·构建耗时](../web/data/recommendations/build-report.json)；[BR·最终资源测量](../reports/browser-e2e.json)；[BT·资源测量](../tests/browser.e2e.mjs)；[RD·same_input_rebuild](../reports/release-determinism.json) | 实测限定当前设备/样本；未测全量 GRP 任务或真实数据变更的增量规模，不能据此保证所有设备性能。 |
| L14 | `implemented` | 静态发布清单隔离源数据/凭据，下载入口限制主机与嵌入凭据。 [WB·发布内容](../scripts/build_web.py)；[J·下载约束](../backend/wt_atlas/jobs.py)；[PT·download_hosts_and_embedded_credentials](../tests/test_pipeline.py)；[PK·文件选择](../scripts/package_release.py) | 部署应发布指定站点目录，不公开运行者私有工作目录。 |

### AT01–AT13 验收案例

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| AT01 | `implemented` | 夹具验证地面/航空异步上调、最高 BR、固定成员、优化与地面替代。 [ET·AT01 / AT11](../tests/engine.test.mjs)；[E·compareVersions](../shared/engine.mjs)；[BT·自定义方案](../tests/browser.e2e.mjs) | 历史 BR 示例只作夹具，不声明当前游戏值。 |
| AT02 | `implemented` | 实际最高 BR、可配置跨度、含上界与排除外部对手的断言通过。 [ET·AT02](../tests/engine.test.mjs)；[E·matchEnvironment](../shared/engine.mjs) | 生产 BR 与规则来自快照/配置。 |
| AT03 | `implemented` | 仅敌方 BR 改变也更新环境、进出和公告。 [ET·AT03](../tests/engine.test.mjs)；[E·compareVersions](../shared/engine.mjs)；[DI·impact_nation_br_union_count](../reports/decision-integrity.json) | 威胁强度仍受物理缺项限制。 |
| AT04 | `implemented` | M829A2/XM885、错来源/距离/瞄准/所有权/哈希均独立检查。 [ET·AT04 / reference-only heatmaps / aim identity](../tests/engine.test.mjs)；[SUMT·explicitly verified fixture](../tests/probability_summaries.test.mjs)；[E·findExactProbability](../shared/engine.mjs) | 测试核验身份是夹具，生产精确记录 0。 |
| AT05 | `implemented` | 等价夹具可折叠且汇总不变，BMPT/T-72 与豹系差异保留。 [ET·AT05](../tests/engine.test.mjs)；[E·foldEquivalents](../shared/engine.mjs)；[U·renderMatrix](../web/app.mjs) | 验证了机制，生产等价分组缺口见 T04。 |
| AT06 | `implemented` | AA-AA 原分保留/汇总降权，AA-air 权重不一起降低。 [ET·AT06](../tests/engine.test.mjs)；[E·evaluatePair / evaluateLineup](../shared/engine.mjs) | 系数为待校准配置。 |
| AT07 | `partial` | 互斥预设拒绝、单选整套、ARM 辐射条件夹具通过。 [ET·AT07](../tests/engine.test.mjs)；[E·validateLoadout](../shared/engine.mjs)；[ST·nested_container_counts](../tests/test_sources.py)；[SC·legal_whole_presets](../reports/source-coverage.json) | 真实现代全目录自选混挂与约束未齐，不能全量验收。 |
| AT08 | `implemented` | 状态、NaN、背景/画布外质量和区间保留，质量守恒有断言。 [ET·AT08](../tests/engine.test.mjs)；[PT·background_no_inner_unknown_and_true_zero](../tests/test_pipeline.py)；[SUMT·real binary integration](../tests/probability_summaries.test.mjs)；[BT·按需热力图](../tests/browser.e2e.mjs) | 条件效用不是击毁概率。 |
| AT09 | `implemented` | 默认 s=0；改技能影响瞄点/误差而不改源装甲概率。 [ET·AT09](../tests/engine.test.mjs)；[E·integrateAim](../shared/engine.mjs)；[BT·按需热力图](../tests/browser.e2e.mjs) | 行为仍未校准。 |
| AT10 | `implemented` | 改瞄距不改物理穿深，拒绝复用旧瞄准上下文摘要。 [ET·AT10](../tests/engine.test.mjs)；[SUMT·explicitly verified fixture](../tests/probability_summaries.test.mjs)；[BT·按需热力图](../tests/browser.e2e.mjs) | 物理射距变化需要离线对应结果。 |
| AT11 | `implemented` | 固定旧成员迁移与新优化分开，同一算法和配置。 [ET·AT01 / AT11](../tests/engine.test.mjs)；[E·compareVersions](../shared/engine.mjs)；[BT·自定义方案](../tests/browser.e2e.mjs) | 原队列损失不会被新成员结果替代。 |
| AT12 | `implemented` | 最终浏览器报告所列静态 HTTP 流程全部通过；外部服务不可用时，核心页面、矩阵、科技树和按需热力图均可运行。 [BT·静态启动 / 自选队列 / 科技树](../tests/browser.e2e.mjs)；[BR·最终执行结果](../reports/browser-e2e.json)；[WR·业务依赖字段](../reports/web-build.json) | 通过结论限定报告中的设备、输入和流程；详细网络、内存与交互数据以 reports/browser-e2e.json 为准。 |
| AT13 | `implemented` | 固定输入重复结果确定；产物哈希/gzip一致、分数落入区间，构建身份完整。 [ET·AT13 / narrow uncertainty bounds](../tests/engine.test.mjs)；[SUMT·summaries reproduce exactly](../tests/probability_summaries.test.mjs)；[DI·完整性](../reports/decision-integrity.json)；[DR·deterministic_payloads](../web/data/recommendations/build-report.json)；[RD·same_input_rebuild](../reports/release-determinism.json) | 确定性不等于来源和物理已游戏校准。 |

### F01–F05 最终完成定义

| ID | 状态 | 实现与证据 | 未完项 / 当前限制 |
|---|---|---|---|
| F01 | `partial` | 已知合法国家/BR/旧队列的版本影响工作流齐备，能定位最高 BR、敌人和固定/优化差异。 [U·renderVersions / impactMap](../web/app.mjs)；[E·compareVersions](../shared/engine.mjs)；[ET·AT01 / AT03 / AT11](../tests/engine.test.mjs)；[BR·最终浏览器](../reports/browser-e2e.json) | 防空/CAS、雷达和装甲资料不全，不能按完整物理质量宣告全部达标。 |
| F02 | `partial` | 10 国全 BR 两版推荐、合法约束、任务组合、相关配对和备选已生成。 [E·recommendNation](../shared/engine.mjs)；[DI·candidate_counts](../reports/decision-integrity.json)；[ET·AT02 / AT04 / AT06 / AT13](../tests/engine.test.mjs)；[SC·coverage](../reports/source-coverage.json) | 0 生产精确配对；现代混挂、系统性能和统计校准缺口阻止完整科学匹配验收。 |
| F03 | `implemented` | 来源、版本、参数、缺项和模型边界可见；研究摘要与生产严格隔离。 [U·renderAudit / openPair](../web/app.mjs)；[E·findExactProbability](../shared/engine.mjs)；[SC·limitations](../reports/source-coverage.json)；[SUMR·verified_records](../reports/probability-summaries.json)；[ET·unknown input](../tests/engine.test.mjs) | 此项确认不冒充精度，不声称所有源数据已经完成验证。 |
| F04 | `implemented` | 静态访问和 Worker；重计算在离线后端，无常驻业务 API/数据库/在线 GPU。 [WB·静态输出](../scripts/build_web.py)；[J·离线任务](../backend/wt_atlas/jobs.py)；[W·局部计算](../web/worker.mjs)；[WR·依赖字段](../reports/web-build.json)；[BR·最终网络资源证据](../reports/browser-e2e.json) | 架构分离已实现；性能与全量规模限制见 L13。 |
| F05 | `implemented` | 站点、源码、构建/增量脚本、配置、契约、审计、自动测试与资源报告齐备；完整 ZIP 已验证并从全新目录离线重建。 [README·运行](../README.md)；[PK·发布包](../scripts/package_release.py)；[VR·完整性](../scripts/verify_release.py)；[RC·全新解压重建](../reports/release-package-check.json)；[UNIT·测试](../reports/unit-validation.json)；[BR·最终端到端](../reports/browser-e2e.json) | 交付完整性通过；全量物理输入与游戏校准仍按对应条款保留未完成状态。 |

## 继续完成所需输入与步骤

本地地面目录的 GRP、单位、材料和武器来源已接入。后续需继续完成具体弹丸到完整物理参数、射距和目录弹药身份的核验，再按实际可用弹药和相关匹配环境安排额外任务。任务产出通过格式、来源哈希和几何/模型验证后，导出切片和轻量摘要；仅核验完成的记录可标为可参与生产精确配对。随后用同一摘要重建全国两版推荐和静态目录。命令与缓存失效规则见[管线说明](PIPELINE.md)、[摘要契约](DECISION_PROBABILITIES.md)。

与资源获取并行补充雷达、SP、现代合法混挂和多车系统能力；毁伤状态模型与实战统计校准需独立实施和验证。最终浏览器结果及分发包完整性按最终交付报告签收，不改变本报告对数据和科学有效性缺口的判断。


## 2026-10-26 本车弹药与多候选主页增量

原表中的「0 生产精确配对」仍指参考图严格缓存键匹配数量；新增路径是明确标注的 GRP 抽样近似，不能与完整精确物理混同。1,225 个地面构型均有目标采样，候选用弹归属与输入哈希检查通过。测试覆盖不同弹药/机制/距离、目标方位、源身份失效、未知层、独立反向用弹和评分单调性。

主页多个不同成员的队列、旗帜切换、BR 滑条、候选采用、二级自定义菜单，以及浏览器 Worker 与离线得分一致性，均由 `tests/browser.e2e.mjs` 验证。结果见 `reports/browser-e2e.json`、`reports/owned-ammunition-validation.json` 和 `reports/client-backend-validation.json`。

## 2026-10-27 十槽容量与版本榜单增量

默认容量改为 10 槽，目标 BR 滑条移入自定义队列。首页只显示本系全 BR 候选的评分前三，并以成员集合去重。版本页显示新版评分前三，以及成员 BR 调整不同步的旧队列前三；被拆榜按旧版评分排列，同时列出固定原成员的新分数和变动成员。

35 项引擎测试、33 项 Python 测试、4 项概率摘要测试和 11 项浏览器流程均通过。新增覆盖包括十槽搜索、全 BR 排序与去重、同幅变动排除、未变成员参与拆队判定、目标 BR 不影响首页、采用新版队列自动切换版本，以及从被拆榜打开原成员版本对比。10 国实际预计算榜单的排序、去重、容量、拆队资格和固定成员一致性另行验证，结果见 `reports/rankings-validation.json`。桌面与移动端截图位于 `reports/screenshots/`，预计算与来源完整性见 `reports/decision-integrity.json`、`reports/client-backend-validation.json`。
