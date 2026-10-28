# 本地客户端数据后端

输入为 `D:/steam/steamapps/common/War Thunder` 的 2.59.0.86 客户端，以及原上传 `wt_grp_probability_scripts.zip`。原包保留在 `backend/vendor/grp_probability_v2`，新增代码位于 `backend/wt_atlas/client.py`、`client_sources.py`、`native_binding/` 和 `raster.py`。

## 重建

在项目根目录的 PowerShell 中执行：

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = 'backend'
python -m wt_atlas client all --source 'D:\steam\steamapps\common\War Thunder' --workers 8
python -m wt_atlas summarize
python scripts/build_target_layers.py --workers 6
python build.py
```

可以分步执行 `client prepare`、`client sources`、`client compute`、`client publish`、`client link`。`client status` 只读取任务状态。`prepare` 和 `compute` 支持 `--vehicle` 后跟一个或多个目录 ID；完整发布会校验所有已完成任务是否匹配当前输入。

`client all` 完成提取、绑定、武器资料索引、概率计算、切片导出和目录来源关联；`summarize` 生成瞄准积分摘要，`build.py` 更新全国推荐与静态网页。模型输入不变时计算任务会校验并复用结果，进程中断后重新运行 `client compute` 即可恢复。

## 资源与材料匹配

扫描安装目录 `content/**/*.grp`，随后让 `patch/content/**/*.grp` 中的同名碰撞资源优先。补丁包可能只含部分资源，未被覆盖的资源继续从基础包读取。下载缓存、历史资源和 UserMods 不参与当前构型匹配。GRP 目录中的资源名编号与资源序号独立解析，支持资源名数量不同于资源数量的包。

每个车型读取客户端单位配置的 `model`，精确寻找对应的 `<model>_collision`。多辆车可以共用一份 GRP，但保持各自的 `DamageParts`、材料参数、构型哈希和计算结果。未知、冲突或缺失资源会记录为任务状态。

材料绑定保留 BLK 参数顺序、类型、全局部件默认值、层级覆盖、机制数组和未损伤部件绑定规则。解析器来源及哈希记录在 `backend/wt_atlas/native_binding/provenance.json`，其适用客户端配置包由 SHA-256 固定。更换客户端版本需同步验证材料解析器与参考弹数据。

四辆原始样本共 1,079 个节点的生成结果与原包逐项对照，部件 ID、材料、标志和装甲类别一致。新增 Numba 栅格器保持 CPU 原有重心坐标、边缘合并和入射判定；独立测试比较正面、斜向和侧面射线数组。

## 数据文件

| 路径 | 内容 |
|---|---|
| `data/work/client/extracted/` | 保留参数顺序和类型的客户端 BLK |
| `data/work/client/raw/` | 去重、固定哈希的 GRP 副本 |
| `data/work/client/profiles/` | 各车型完整节点与材料配置 |
| `data/work/client/jobs/` | 独立任务状态及结果哈希 |
| `data/work/client/geometry/` | 私有射线层缓存 |
| `data/work/client/results/` | 私有概率 NPZ |
| `data/work/client/source-index.json` | 单位、挂载、预设、武器与弹丸来源关系 |
| `data/work/client/source_units/`、`source_weapons/` | 完整解析后的单位和武器配置 |
| `data/snapshots/client-ground.json` | 可复核的逐车客户端覆盖摘要 |
| `web/data/probability/` | 网站按需读取的概率切片 |
| `reports/client-*.json` | 资源覆盖、材料对照、计算、导出和武器来源报告 |

逐车客户端来源也写入 `catalog.json` 的 `source_attributes.client`，与 Wiki 属性分开归因。公共网站不加载原始 GRP、BLK、材料大表或 NPZ。

## 计算范围

默认沿用原脚本的 **M829A2、M830 两份参考弹输入、0 m 物理射距、25 mm 采样间距，以及 0°–345° 每 15° 一个视角**。各车型画布按完整几何包围范围生成，NPZ 和公开切片保留各自的尺寸与米制边界。

武器索引中的弹丸定义是原始输入资料，并不表示每条弹丸都已生成完整弹道配置或完成对所有目标的计算。参考图不建立 Wiki 弹药别名或生产精确配对。新增实际弹药/射距计算应提供相应完整物理参数，设置 `compute.ammunition_file` 后重算；仅改变距离标签不能改变穿深。

计算仍采用原脚本的分层直线路径条件模型，不包含完整毁伤、碎片、爆压、ERA 消耗和 NERA 动态，也不代表击毁率。这里完成的是客户端数据与既有模型的批处理接入；具体完成数和失败项以 `client status`、`reports/client-compute.json` 与 `reports/client-probability-export.json` 为准。

## 本次构建结果

已完成 1,225 个构型、937 份不同 GRP、196,918 个碰撞节点；两份参考弹各 24 个视角，共 2,450 份数组、58,800 个切片。所有任务完成，材料绑定未解决项为 0。切片 gzip 总计 101,605,934 字节，最大量化误差 7.629627368999081e-6。

武器来源索引包含 773 份文件、3,567 条弹丸定义和 1,225 套单位预设，无缺失引用。61 项单元测试与 8 项浏览器流程全部通过。最终目标覆盖、输入哈希与发布一致性可运行 `python scripts/validate_client_build.py` 复核，结果记录于 `reports/client-backend-validation.json`。


## 本车弹药评分数据

`data/snapshots/target-layers.json` 保存与参考弹无关的目标分层采样。当前每构型每方向最多 64 条路径，共 1,881,600 条；其中 184 条保留未知。导出器根据原材料机制索引分别保存动能与破甲响应，并保留几何、构型、源版本与层数组哈希。

静态构建按 24 个方向拆分 `web/data/physics/target-layers-*.json`，Worker 仅加载当前方向。推荐页本身只读取已计算的候选；自定义计算会使用自己的弹药列表与抽样路径。细节和近似边界见 [决策引擎](DECISION_ENGINE.md)。原两种参考弹的完整热力图继续独立提供。

新增验证：`node scripts/validate_owned_ammo.mjs` 检查真实 T-72B 弹药对 T-90M 的结果差异；`python scripts/validate_client_build.py` 同时检查所有候选的用弹归属、全目标覆盖与抽样数据哈希。
