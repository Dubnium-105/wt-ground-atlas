# 防护摘要到决策模型的接口

## 默认离线流程

```bash
# 一：从已取得的 GRP/材料计算，或导入既有概率数组
PYTHONPATH=backend python -m wt_atlas run --export
# 二：对实际发布切片积分，生成轻量决策输入
PYTHONPATH=backend python -m wt_atlas summarize
# 等价的直接 Node 入口
node scripts/build_probability_summaries.mjs
# 三：用相同摘要输入构建全国两版推荐
node scripts/build_decisions.mjs --probabilities data/snapshots/decision-probabilities.json
# 四：组装独立静态网站
python scripts/build_web.py
```

`summarize` 默认读取 `web/data/probability/index.json`、索引对应的切片和 `config/model.json`，调用网站同一份 `integrateAim()`。输出 `data/snapshots/decision-probabilities.json` 及 gzip 副本，报告写到 `reports/probability-summaries.json`。

不需要重新计算原始弹道物理，也不把一个像素均值当成每发击穿概率。摘要使用中心/弱点混合瞄准与完整高斯落点质量，包含背景、未知、无受保护内部目标和目标外质量。默认技能与瞄准距离来自模型配置；每张图的物理距离、采样间距与视角来自该图元数据。

后端的获取、解码、数值计算、静态切片、摘要积分和全国推荐因此都有可独立复用的输入/输出；浏览器不需要携带 GRP 或射线层。

## 当前上传数据的状态

生成器生成的记录**始终是 `reference_only: true`**。它不会因为一张图使用某颗参考弹，就认为目标车或任何别的载具拥有该弹；不会向 catalog 自动加入武器、几何版本或配置版本。

当前上传 atlas 的全部 192 个视角已实际积分成 192 条摘要。它们会作为轻量数据被决策管线加载，但严格身份门禁会阻止其被当成当前 Wiki 载具的已核验精确配对。

物理射距仍是来源弹药档案声明的距离。若只更改 `--context` 中的瞄准距离，改变的是落点误差，不会生成新的物理穿深。

## 摘要格式

```text
bundle
  schema_version
  model_version                 物理防护模型版本
  decision_model_version        瞄准/决策模型版本
  entries[]                     各源图配置的小型目录
  records[]                     逐条件积分结果
  provenance                    输入、模型、解码器和生成器哈希
```

每条 `records` 的字段：

| 字段 | 意义 |
|---|---|
| `target_id`、`configuration_id` | 目标实体与实际战斗构型 |
| `ammo_id`、`ammo_profile_sha256` | 精确弹药实体与完整参数指纹 |
| `physical_distance_m`、`angle_deg` | 已算防护图的物理射距和视角 |
| `geometry_version`、`configuration_version` | 几何及材料/绑定配置版本 |
| `model_version`、`source_version` | 物理模型与来源客户端版本 |
| `aim_context_key` | 决策模型、完整瞄准参数、技能、瞄准距离、自身/目标运动、炮控及可选误差输入的规范化身份 |
| `mean`、`lower`、`upper` | 含明确未知先验的条件效用及未知上下界 |
| `coverage`、`known_mass` | 落点分布中有已知模型结果的质量 |
| `unknown_mass` | 未知几何/材料的落点质量 |
| `no_protected_target_mass` | 当前保护目标指标下，射线无受保护内部目标的质量 |
| `miss_mass` | 背景和画布外落点质量 |
| `reference_only` | 是否仅为研究参考；默认必须 true |
| `source_entry_id`、`source_shard_sha256`、`source_npz_sha256` | 可追溯的源图及文件身份 |

`aim_context_key` 使用共享函数 `aimContextKey(context, config)` 生成，必须完整保留，不得自行删除技能或运动条件以提高缓存命中。技能、瞄准距离或模型参数改变时，摘要不再匹配；应重新积分对应图，或者回退为有标记的粗模型。

## 接入未来已核验的完整车型结果

可以把后端产出的已核验记录放入一个遵守上述契约的独立 bundle，并通过 `build_decisions.mjs --probabilities` 指定。主站从构建清单加载同一个 bundle。**现有参考记录不会自动升级。**

发布已核验记录的前提：

1. catalog 中目标自身已保存真实的 `configuration_id`、`geometry_version`、`configuration_version`、`source_version`，与该物理结果逐项一致。
2. catalog 中攻击载具自身真实拥有该 `ammo_id`，且该弹的 `ammo_profile_sha256`/`profile_sha256` 与结果一致。不能凭国家、BR、口径或弹种类别推断。
3. 物理距离、角度、物理模型版本和 `aim_context_key` 全部匹配当前请求。
4. 每条可用于配对的记录显式声明 `reference_only: false`，并在来源记录中保留材料/配置转换方法、源文件哈希和验证依据；发布者负责提供这些核验依据，引擎负责核对身份，不会替外部数据制造真实性证明。
5. 未匹配或未知身份保持未知/粗模型，不能让 `null` 匹配另一个 `null`。

用于已核验记录的索引只能由实际输入和来源生成。测试中的虚构身份仅用于验证门禁，不会写入生产 catalog 或默认 bundle。

## 可选参数与复现

```bash
node scripts/build_probability_summaries.mjs \
  --index /path/to/atlas/index.json \
  --config config/model.json \
  --context /path/to/explicit-aim-context.json \
  --out /path/to/decision-probabilities.json \
  --report /path/to/summary-report.json
```

输入哈希、模型/解码器/生成器身份与瞄准条件未变时复用已有摘要。`--force` 强制重新读取并核验所有切片，重复计算应产生相同 bundle 字节。运行耗时仅在命令输出中报告，避免把计时噪声加入结果身份。

自动测试：

```bash
node tests/probability_summaries.test.mjs
```

测试包括真实二进制积分、未知与落空质量守恒、默认研究记录不参与精确配对、完整核验夹具被实际 `evaluatePair()` 使用，以及技能、物理距离或弹药所有权变化后拒绝错误命中。
