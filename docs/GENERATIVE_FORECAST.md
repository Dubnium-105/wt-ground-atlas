# V3：由本地 V2 物理图集生成交战结果，再自动发现版本队列

这是可执行的独立离线流水线，不再调用 `shared/engine.mjs` 的属性加权评分。输出是默认无需手工选车的静态预测站点。旧版研究站点和旧引擎保持兼容，不将仍在草稿中的 PR #2 / #3 当成依赖。

## 1. 一条命令运行提供的样例

```bash
python -m pip install -r requirements.txt
python scripts/build_forecast.py \
  --sample-root /path/to/analysis-sample-2.59.0.92 \
  --diagnostics --out dist/forecast
python -m http.server 8080 --directory dist/forecast
```

Windows 的路径可以用带引号的绝对路径。`--sample-root` 指向**已经解压**的目录；不执行压缩包内的脚本。样例的 32 个文件按 manifest 核对大小与 SHA-256，然后解析 V2 原始 NPZ。不要把 `reference_slice` 作为 `--atlas`。

样例只有 2 辆目录载具、1 个有向目标—弹药结果、24 个角度；404 份弹药配置不是 404 组已计算对抗。其弹药 `distance_m` 是 **10 m**。程序会输出原生距离下 72 条技能诊断，但不发布全服榜，不补造 Khalid 对 BMP-2M 的反向结果。

全量输入：

```bash
python scripts/build_forecast.py \
  --catalog data/snapshots/catalog.json \
  --ammunition /data/ammunition/all_resolved_profiles_final_with_apcr_gun_caliber.json \
  --atlas /data/v2 \
  --scenario data/snapshots/changes.json \
  --config config/forecast.json \
  --cache data/cache/forecast.sqlite \
  --out dist/forecast
```

`--atlas` 下应有 `arrays/*.json` 及元数据引用的 NPZ。无需复制 10+ GB 原始图集到站点。没有 `--scenario` 时只有当前快照；不能复制当前数据并冒充“下一版”。

涉及弹药或装甲物理变化时，补丁会使原有图失效。已有下一版重新计算的全量物理快照时，使用三个独立输入，而不是继续套用旧图：

```bash
python scripts/build_forecast.py --catalog current/catalog.json \
  --ammunition current/ammo.json --atlas current/v2 \
  --planned-catalog planned/catalog.json --planned-ammunition planned/ammo.json \
  --planned-atlas planned/v2 --planned-status proposed --out dist/forecast
```

完整下一版快照与 `--scenario` 互斥，避免不明确的二次应用。两版本使用同一份决策配置、同一套搜索算法。

## 2. 输入身份与不可越过的数据边界

每幅 V2 图以 `map_id + target_id + target_sha256 + ammo_id + ammo_profile_sha256 + source_version + datamine_commit + array_sha256` 识别。

* 弹药身份用排序键、无空格、UTF-8 的完整配置 JSON SHA-256 验证。样例中的完整配置确实与 `ammo_profile_sha256` 匹配，不删除 `provenance` 后再算哈希。
* 归属只接受 `provenance.penetration_source_ammo_ids` 与载具实际 `ammo[].id` 的精确匹配。没有名称猜测，也不把参考 M829A2 图当成其他弹药。
* 同时检查弹药、目标、图集的客户端版本和 Datamine 提交。样例的 `target_sha256` 无法与目录的 geometry/configuration SHA 直接等同，因此标为 `source_declared_id_version_only`；不伪称几何身份已独立验证。完整输入可提供 `analysis_target_sha256` 建立这一绑定。
* NPZ 校验 float32/uint8、形状、角度、坐标边界、概率范围和状态计数。`allow_pickle=False`。有缓存也要核验源 NPZ，不能用暖缓存掩盖损坏。
* `model_required_charge_mm` 不作为 RHAe，不用于缩放距离穿深。缺少目标距离或精确 yaw 时返回缺项，不使用最近角度代替。

保留四类状态：0 背景；1 当前射线无受保护内部目标；2 未知；3 有条件穿透估计。状态 1 对“到达受保护内部目标”事件没有贡献，但不代表车辆不会受损；在毁伤端点中它仍增加未知上界。

目录分类为 ground / air；地面分 light_tank、medium_tank（保留 mbt 子类）、heavy_tank、tank_destroyer、spaa；空中分 fixed_wing / helicopter。无证据的直升机子类型保持 unknown，不把 CAS 标签强制解释为攻击直升机。

## 3. 第一层：物理效果

输入 `P_pen[x,y]` 直接用于条件穿透事件，不能再次通过一个随意的 Effective=0.85 映射把已有数值变成虚假击毁率。

默认端点 `penetration`：观察谁先产生“受保护内部目标穿透”事件。这是透明的近似价值指标，不是击毁、胜率或完整战场贡献。

可执行的 `damage` 端点：接受经来源声明的穿后条件核，支持火力失能、机动失能、两者同时失能以及毁灭状态。每个格点的条件结果互斥，概率总和不超过 1。未提供核时**不生成击杀概率**，也不能让 damage 排名静默退回 penetration 排名。

穿后核示例（数字是合成演示，不是实测值）：

```json
{
  "records": [{
    "map_id": "exact-map-id",
    "map_array_sha256": "exact-source-NPZ-SHA256",
    "status": "assumed",
    "source": "explicit sensitivity assumption, not observed game damage",
    "constant": [0.2, 0.1, 0.1]
  }]
}
```

顺序为 `fire_disabled / immobilized / destroyed`。常量只是公开的敏感性假设。真实空间核使用 `array_file`、`array_sha256`，NPZ 字段 `conditional_outcomes` 形状 `[angle,height,width,3]`；角度顺序须与绑定物理图一致。命令添加 `--effects effects.json`，配置 `endpoint=damage`。程序已经执行该状态模型，不是只留下接口；但本样例没有 DamageParts 穿后核，不能声称已复刻碎片、爆压、乘员或弹药架毁伤。

## 4. 第二层：弱点选择与落点分布

对满足阈值的可计算像素做四连通分区。每个区域保存物理面积、平均收益、内部最大距离、与目标中心的距离；面积使用平方米，避免改分辨率后奖励虚增。

\[
U_k=\alpha_V\bar V_k+\alpha_A\log(1+A_k/A_0)
 +\alpha_C\log(1+C_k/C_0)-\alpha_D D_k/D_0.
\]

候选瞄点选在距离边界最远的内部像素；质心落在环形弱点空洞时不会误把空洞当弱点。

\[
P(k\mid s)=\operatorname{softmax}_k(\tau_0e^{\beta s}U_k),\quad
\eta(s)=\operatorname{sigmoid}(a-bs).
\]

实际落点为中心瞄准和各弱点瞄准的**全平面**高斯混合。横纵方差分别组合距离、技能、炮控、稳定器残余、目标/本车平面速度及目标角速度产生的误差。像素质量使用正态 CDF 差的乘积，而不是把高斯中心值当像素概率。

\[
E_{shot}(s)=\sum_c p_c(s)P_{pen,c},\quad
E_{damage}(s)=\sum_c p_c(s)P_{pen,c}\,q_c.
\]

`known_mass + unknown_mass + no_protected_target_mass + miss_mass = 1`，其中落到背景和画布外的质量始终保留；**不把命中分布截断后重归一化到弱点内**。未知带来上下界，不填成 0。

`population` 是显式的正态假设；Gauss-Hermite 求积真正用于总体积分。双方水平独立积分，不把同一个 s 强加给两个玩家。可以另设 `opponent_population`，在保持对手分布不变时构建新手/高手推荐配置。

## 5. 第三层：双向、连续交战事件

不再用“高手给机动加权、降低装甲权重”代替操作机制。速度和功重比影响到位时间；炮塔转速影响转向耗时；观瞄影响发现耗时；技能影响发现/瞄准/修正时间；倒车影响退出暴露的时间。

\[
T_{first}=T_{arrival}+T_{acquire}+T_{slew}+T_{aim},\quad
T_{cycle}=\max(T_{reload},T_{reaim}).
\]

自动炮一个跟踪连发内使用真实 RPM 间隔，不每发额外叠加完整找目标时间。装填机制不明的导弹发射间隔不冒充人工装填。已知待发架/弹链容量用作上限，不凭总弹量捏造无限待发弹；补弹机制未知时停止该轮发射。

事件队列按时间排序，传播双方联合状态质量。同一时刻的射击从事件前状态同时结算，防止固定先计算 A 获得优势。穿透端点只把条件事件作为终止；毁伤端点中断炮者停止射击，断履带者不能执行原先的退回掩体计划。没有实现维修、战斗中 ERA 消耗或每发命中后动态重建装甲，所以假设明确记录为固定地图、条件独立后续射击。

射速价值来自真实增加的事件机会；先手价值来自首次事件时序；不是最后再加一项装填或机动属性分。

## 6. 连续出击与队列搜索

队列不是“平均几辆车的分数”。有限阶段规划状态是 `(phase, used_vehicle_mask, spawn_points)`，每阶段选择可支付、尚未出击、符合当前任务的载具，或等待。点数奖励按配置给定的转移分布积分；**不得把一个效用分数当作赚到重生点的概率**。

收益上下界沿同一条以均值选出的策略传播。无法再出击的阶段为已知的零贡献，不会被错误计为资料缺失。默认点数规则只是公开假设，配置明确标为 `assumed_not_official_rules`；航空出击成本未知时不能把它当免费火力。

防空、CAS、制空、侦察等扩展阶段使用 `--missions` 的版本化任务证据：记录必须包含 `variant_key / target_vehicle_id / scenario_id / task / lower / mean / upper / source_version / source / status=validated`。没有这类数据时，不能从导弹最大射程或飞机存在与否硬凑任务收益；默认榜单范围明确限定为原生距离地面对抗。出击规划可执行这些任务与合法航空配置，但本地地面样例不证明它们的效果。

匹配模型先枚举**整场 BR 上限**，再对对应窗口的对手求条件分布。假设频率不是实测匹配频率。等价构型组 → 载具父 ID → 配置逐层分配质量，增加同一架飞机的 BR 选项不会使其出现频率变成数倍。

风险评分：

\[
R(L)=100[(1-\lambda)\mathbb{E}_z Q(L,z)
 +\lambda\operatorname{CVaR}^{lower}_{\alpha}(Q(L,z))].
\]

CVaR 按情景质量分割边界样本，不是对不同概率情景取未加权分位数。参数、场景和假设均在 `config/forecast.json`。

每个国家、实际 BR、槽位数量、获取范围独立搜索：多个地面/最高 BR 锚点 → beam 保留多个中间组合 → 一槽/两槽交换。每步重算队列真实最高 BR 和对手环境。不同 BR 配置、完整合法挂载、地面弹药选择都是候选身份的一部分；同一载具/多车系统不能占两槽。没有先裁成每类最强单车，也没有强制填满无用槽位。

有明确搜索预算，并输出是否耗尽。该实现是有测试的组合启发式，不声称全局最优；小规模完整组合的穷举测试用于验证搜索结果。

## 7. 更新、校准、静态发布

BR 补丁只改变匹配与搜索，不使射线图或瞄准缓存失效；弹药/防护变化使旧物理图失效。配置 BR 变化必须指定 configuration_id，禁止用一个 vehicle.br 覆盖所有档位。可使用明确标为 proposed/announced/hypothetical 的独立方案模拟新规则，而不会把猜测写成已实装事实。

同一旧队列在新版重新评价；再与**同一新版 BR、槽位和获取范围**内重新优化后的阵容比较。配置消失或非法则输出不可迁移状态，不静默替换掉它。

提供可执行拟合器，而非只有参数导入：

```bash
PYTHONPATH=backend python -m wt_atlas.forecast.calibrate \
  --catalog data/catalog.json --atlas /data/v2 --ammunition /data/ammo.json \
  --config config/forecast.json --observations observations.json --out reports/aim-fit
```

观测包需有 source、client_version、mode、skill_source、time_window，以及 shots。每条 shot 有 match_id、map_id、attacker_id、catalog_ammo_id、skill、x_pixel、y_pixel、scenario。落空坐标可以在画布之外。按 match_id 分组分离训练和验证，不在验证集拟合。有限参数有配置化边界；输出样本外负对数似然、优化器状态和新配置，始终需要独立审查，不自动宣布“实战已验证”。物理样例本身不是玩家瞄准观测，不能拿它作真实训练集。

站点输出采用内容哈希 JSON、gzip、每国家/BR/槽位分片。发布目录原子替换，有标记文件防误覆盖；原 NPZ、缓存 SQLite、弹药全集不进入网站。`web/forecast` 是此流水线的正式前端，直接部署 `dist/forecast`，不是让用户先手动组队再算分。

## 8. 验收

```bash
python -m unittest discover -s tests -p test_forecast.py -v
node tests/forecast.e2e.mjs
```

覆盖源哈希与归属、物理距离/角度拒绝、未知语义、softmax、各向异性误差、守恒、毁伤核、先手和射速、同时开火对称、自动炮容量、SP 规划、同车配置不重复、对手频率守恒、beam/穷举、双版本迁移、暖缓存、确定性发布、按战局拆分校准，以及浏览器自动榜单/切版本/筛选/子路径/移动端。

普通静态站点 CI 仍保留，新 workflow 另外验证这条真实生产路径。合成 fixture 从不混入生产目录。

**当前实现完成的是以上数据契约与可执行算法链路；没有数据支持的穿后毁伤、航空任务、真实匹配频率与玩家技能分布不会被代码凭空“补全”。** 本样例不够产出有证据的全服最强队列；接入全量、同版本、足够双向覆盖的数据后，命令会自动搜索、生成并发布条件预测。
