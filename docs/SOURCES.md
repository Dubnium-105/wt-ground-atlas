# 来源、快照与解析边界

## 离线更新命令

```bash
PYTHONPATH=backend python -m wt_atlas sources --help
PYTHONPATH=backend python -m wt_atlas sources
PYTHONPATH=backend python -m wt_atlas sources --refresh --changes
PYTHONPATH=backend python -m wt_atlas.sources --config config/sources.json
PYTHONPATH=backend python -m wt_atlas.tencent --config config/sources.json
PYTHONPATH=backend python -m wt_atlas.loadouts --config config/sources.json --vehicles <stable-aircraft-id>
```

统一入口 `python -m wt_atlas sources` 读取官方 Wiki 的 ground、aviation、helicopters 三个完整目录，再读取所有详情；缓存命中时不用网络，但仍重新生成快照。`--help` 只显示帮助，不采集、不写入文件。`--refresh` 重新采集；`--changes` 同时读取并审计腾讯调整表；`--source-config` 可指定来源配置；`--catalog-only` 只更新目录，不能据此宣称完整属性覆盖。刷新完成后还需运行站点导出命令，浏览器不会自行下载或重算。

独立入口 `python -m wt_atlas.sources` 提供相同 Wiki 采集功能，配置参数为 `--config`；它还提供仅供调试的 `--limit-details`。`python -m wt_atlas.tencent` 单独读取用户指定腾讯表格的陆战、空战页，按表头选择陆战历史列。`python -m wt_atlas.loadouts` 是可选 Datamine 航空增强；不提供 `--vehicles` 时扫描目录内所有航空载具，可能产生大量离线下载。当前交付快照的合法航空预设来自 Wiki，未声称已经完成全量 Datamine 挂载增强。

## 官方 Wiki

目录来源为 https://wiki.warthunder.com/ground 、https://wiki.warthunder.com/aviation 和 https://wiki.warthunder.com/helicopters 。HTML 内的 `WT_UnitList` 提供稳定 ID、名称、国家、rank、编码 BR、状态和角色。航空只读取 `trb`，不拿 `rb` 代替陆战 BR；地面、直升机读取 `rb`。BR 编码的换算公式经过官网同版本 `common.js` 核验，定义放在 `config/sources.json`。没有 BR 的活动实体仍保留目录记录，但不进入合法推荐。

科技树分支、folder、前置关系读取实际 DOM；文件夹前置解析为该 folder 的成员关系。右侧非研发树载具，包括特殊活动载具，不会误当作常规研发。`equivalence_group` 没有证据时为 null，不按同底盘猜换皮。

详情属性只取 `Realistic/reference`，人工装填取 `aces` 端点。武器属性在每个武器 DOM 内单独读取，避免主炮继承机枪射速或弹链容量。自动装弹机只有明确 Autoloader 标记才识别；导弹发射间隔不冒充人工装填。`weapon_profiles` 和弹药的 `weapon_profile` 保留对应关系。

垂直射界保存来源带符号的 `min_elevation_deg`；`depression_deg` 为它的相反数。仅能向上抬升的火箭发射架会得到负俯角，不能因取绝对值获得虚假的向下射界。航空最高速度取明确的 RB/reference 数值，并在 `speed_altitude_m` 保留对应高度；该速度不能解释为任意高度下都能达到。

弹药穿深按实际表头解析。KE 的距离/角度表保持两维；HEAT 的角度表使用 `distance_m:null`、`range_invariant:true`，不会把 0 度穿深当作射距。Wiki 的 Hull/Turret 厚度是名义物理厚度，明确 `physical_thickness_only:true`，RHAe 为 null。热像世代来自 NVD 模块说明，ERA、内衬、烟雾仅在明确出现时记录 true；没有图标不能自动断言 false。lb、g、kg 使用定义明确的单位换算。

官方 Setup 行是一套同时挂载，单独导出 `legal:true`；不同 Setup 永不合并。现代自选挂点表导出 `hardpoint_options`，只代表挂点兼容，未验证依赖之前不能作为合法完整混挂。重生点、雷达扫描周期、架上即用弹等源数据不足项保持 unknown；总携弹不等于架上弹。最大导弹射程是源包线值，不是保证命中距离。

多载具系统仅根据 Wiki 明确的 `Multi-Vehicle system` 区块关联：`system_id` 指向被标为 `Basic unit` 的主车，`system_role` 区分 `basic` 与 `component`，`multi_vehicle_system.members` 保存源组件 ID、数量及标签。组件缺少 BR 时保持 null，不自动继承主车 BR。`capabilities_aggregated:false` 明确说明主车尚未合并组件的雷达与导弹能力；这不是完整系统性能模型。

## 腾讯调整表

源表：https://docs.qq.com/sheet/DRURScE9ueWVRa0pq?tab=000002 。读取公开页面自身声明的 opendoc 链接，使用元数据给出的行数获取完整范围。公开二进制单元格协议的数字池解码，与腾讯提供的 `initial_chunk_pb_4` 客户端模块 458782 核对：内联整数上界由配置记录，余下索引指向浮点字典。只输出必要原始行，不把匿名会话元数据带入站点。

地面选择“历史性能”，航空选择“陆战历史及全真”，再核对“更新前/更新后”子标题。中文与英文名称通过固定 Datamine `units.csv` 关联。唯一匹配并且旧 BR 与基线一致才接受；歧义、未映射、重复、旧值冲突、文字变化分别进入 `changes.json.audit`。`changes-raw-rows.json` 保留原始行和列。所有调整始终是模拟计划，不写回当前基线。

## Datamine 和版本兼容

固定仓库提交与游戏版本见 `config/sources.json`，version 文件已与该提交核对。官方 Wiki 不提供游戏提交号，所以 Wiki 的 game_version/commit 为 null，并单独记录 datamine_reference。不能因为采集日期相近就声称 Wiki 与 GRP 概率图物理同版。

可选航空适配器解析飞机声明的完整预设，逐挂点查找对应选项，递归解析容器的真实弹数与具体弹药。源预设缺少节点、容器不支持或出现环时进入审计，不能拼出合法假预设。`weapons` 包含精确 Datamine ID、导引头参数、飞行包线和来源提交；其与 Wiki 的版本一致性仍为未证实。

## 保留原始数据

`data/raw/wiki` 保存三份目录 HTML、压缩详情和公开调整数据；`data/raw/datamine` 保存使用过的固定提交文件。这些属于离线缓存，不应发布到静态目录。分发源码包可以省略大缓存，`data/snapshots` 足够离线构建站点，回归测试所需最小公开片段放在 `tests/fixtures`。来源审计在 `reports/source-audit.json`。
