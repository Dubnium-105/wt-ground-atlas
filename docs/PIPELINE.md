# 离线 GRP 后端与轻量发布

**本地完整客户端入口已补齐。** `python -m wt_atlas client all` 从安装目录与补丁目录自动匹配车型、提取同版材料、计算并发布。全目录流程、实际数据范围与恢复命令见 [CLIENT_BACKEND.md](CLIENT_BACKEND.md)。以下 `acquire/run` 保留为原有显式 profile 工作流，四车描述指原始参考验收范围。

这个后端运行在你的构建机、工作站或批处理容器内。公众网站只读取已经生成的静态数据，不需要 Python、数据库或 GPU 常驻。

## 已完成的处理链

`可信本地客户端目录 / 显式哈希清单 → SHA256 内容仓库 → 通用支持格式的 GRP 碰撞解码 → 同版逐节点材料绑定 → CPU 或 CUDA 几何/概率计算 → 动态形状校验 → 按目标、精确弹药、角度拆分 → 轻量静态结果`

后端按文件内容识别载具，没有把四辆样本的名称写成处理名单。新载具没有材料配置时仍可解码碰撞网格，任务会停留在 `waiting_profile`；它不会套用另一个坦克的防护数值。

### 自包含文件

`backend/vendor/grp_probability_v2/` 保留上传脚本包的原始内容，包括四个 GRP、四份逐节点配置、原运行时 BLAS 数据和原代码来源清单。它是固定版本输入与兼容性参考。主站绝不会请求这个目录。

生产默认路径都在 `config/pipeline.json`。输入来源、像素间距、角度、数值模型参数、重试限制和量化预算都在配置或来源数据中。代码只保留算法关系、数组索引、数学恒等式与二进制格式定义。此前已接受的 CUDA 参考实现保留原样，文件哈希仍可与上传清单对照；新 CPU 实现从版本化配置读取模型系数。

## 运行环境

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export PYTHONPATH=backend
python -m wt_atlas --help
```

Windows PowerShell 中激活 `.venv\Scripts\Activate.ps1` 后用 `$env:PYTHONPATH="backend"` 设置模块目录。无需 GPU 即可运行新 CPU 后端。CUDA 兼容后端需要另行安装与驱动匹配的 CuPy；当前交付环境没有 CUDA，该兼容路径没有在此环境执行。

## 一、采集与解码

默认配置可直接处理源码包内四辆样本：

```bash
PYTHONPATH=backend python -m wt_atlas acquire
PYTHONPATH=backend python -m wt_atlas decode
PYTHONPATH=backend python -m wt_atlas status
```

处理真实客户端的全部 GRP 时，将 `source.path` 指向客户端资源目录，或使用本次运行覆盖：

```bash
PYTHONPATH=backend python -m wt_atlas acquire --source /mnt/war-thunder-client
PYTHONPATH=backend python -m wt_atlas decode
```

扫描是递归的。每个文件先计算 SHA256，再放入 `data/work/raw/` 内容仓库；改名不会产生重复任务。单个资源格式不支持、数据损坏或材料未匹配不会中止其它文件。`data/work/jobs.json` 记录状态与事件，`status` 返回可读摘要。

支持的当前二进制范围是上传解码器实现的 GRP2 / collision v3 / Zstandard / 对应 BVH 编码。已经真实解码验证四份上传文件；没有声称验证整个游戏客户端全部历史/未来格式。碰撞资源还可导出为包含世界坐标顶点、三角形与节点索引的私有 NPZ，便于后续检查绑定。

### 显式远端清单

后端也支持 `source.kind="manifest"`。清单可以是本地 JSON；远端清单则需要 `source.manifest_url` 和 `source.manifest_sha256`。下载域名必须存在于 `source.allowed_hosts`；跳转后的域名也检查。不会猜测游戏客户端资源的下载地址。

清单结构：

```json
{
  "files": [
    {
      "vehicle_id": "稳定载具ID",
      "source_version": "实际客户端版本",
      "url": "https://你的已配置资源域名/vehicle.grp",
      "sha256": "该文件实际64位十六进制SHA256"
    }
  ]
}
```

示例中的说明文字必须替换成实际值；它们不是可直接运行的生产数据。下载采用分块写入、长度上限、重试与哈希校验，失败的半成品不会成为已获取资源。重试命中已校验文件会复用本地内容。

## 二、注册新载具与版本

完整 GRP 不包含可直接恢复的全部 DamageParts 语义、装甲材料和弹药机制。因此新增载具还需在 `paths.profiles` 指定目录下添加匹配版本的配置，不能只改文件名或复制相似载具的配置。

逐车 JSON 的接口由上传配置扩展而来：

| 字段 | 要求 |
|---|---|
| `vehicle`、`display_name` | 稳定实体 ID、可读名称 |
| `grp_sha256` | 与原文件完全匹配 |
| `collision_resource` | GRP 内实际碰撞资源名称 |
| `collision_nodes` | 完整有序节点身份：index、name、indices_count、behavior_flags |
| `materials` | 与节点索引一一对应；每项保留 part_id、loaded_class、class_flags、behavior_flags、decoded_flags、source_armor_classes 及来源 |
| `view_group` | 由几何导出的旋转中心和 XZ 半包围盒，单位米 |
| `view_plan` | 可选逐车画布与采样元数据；新增大尺寸载具应提供完整几何范围，覆盖通用默认画布 |
| `provenance` | 实际 client_version、Datamine commit、绑定与材料来源哈希 |

`loaded_class.armor` 保留厚度、质量、有效厚度上限、各机制数组及相应标志，不把类名相同视为物理等价。材料尚未映射时可明确为未知；计算会保留未知状态。

CPU 几何路径可直接使用解码后的 authored-pose 三角网格。当注册目录具有该载具经过校验的原运行时顶点记录时，优先使用该记录；缺少记录则明确标注源顶点路径。CUDA 兼容路径另需原包 `data/runtime/loader.json` 及其引用的完整 BLAS 案例/二进制数据。

绑定配置的客户端版本/Datamine 提交必须与 `config/pipeline.json` 声明的弹药/模型来源一致；不一致的任务进入 `waiting_version_alignment`。切换数据版本时更新配置、材料、弹药与来源一起构建，保留旧输出便于回滚。

### 自动绑定审计

新增 `bind-audit` 可将解码节点与实际 Datamine `DamageParts` 按名称精确关联，展开嵌套来源字段并保留每个字段的继承路径：

```bash
PYTHONPATH=backend python -m wt_atlas bind-audit \
  --grp /path/to/vehicle.grp \
  --unit-config /path/to/vehicle.blkx \
  --output data/work/damageparts-audit.json
```

未匹配、重复候选和外部继承都列入报告；不会凭名称制造运行时材料默认值。实测 Leopard 2A5 的 244 个节点，225 个命中唯一原始配置名称，19 个仍需结合绑定/排除信息处理。已有上传的完整 profile 独立保存了这类运行时绑定结果。此审计是全量新载具的配置准备步骤，不冒充可直接计算的最终材料表。

## 三、计算与增量复用

```bash
PYTHONPATH=backend python -m wt_atlas run
PYTHONPATH=backend python -m wt_atlas run --vehicle 稳定载具ID
PYTHONPATH=backend python -m wt_atlas status
```

默认视角数组来自上传计划的完整视角；采样间距读取 `data/view.json`。在 `compute.angles_deg`、`compute.pixel_pitch_m`、`compute.view` 中改变采样。所有分辨率均由米制画布和采样间距导出，不写死为某个样本网格。

`compute.backend="cpu"` 使用新的 NumPy 层栈核；`"cuda"` 使用上传的兼容实现。CPU 层栈实现可独立于几何重算，且不要求安装 CuPy。

```bash
PYTHONPATH=backend python -m wt_atlas recalculate \
  --layers /path/to/vehicle.layers.json \
  --ammo /path/to/exact-ammunition-profiles.json \
  --output data/work/recalculated
```

每个弹药配置必须是具体弹药及指定物理条件下的完整输入，包括 `penetration_mm`、`dispersion`、`damage_caliber_mm`、`mechanism_id`、`air_loss_mm_per_m`、`slope_rows`、`ricochet_rows`、`normalized_distance_curve` 等。**仅修改 `distance_m` 不会产生新的弹道穿深**；需要提供该距离对应的实际穿深/曲线，或者由来源转换步骤生成新的完整弹药参数。瞄准距离的变化由浏览器行为模型单独处理。

几何缓存键含原文件、节点配置、视角、分辨率、运行时数据、解码/几何代码与数值参数哈希。计算键进一步包含弹药与计算核。仅 BR 或显示名称变化不会出现在物理键中。任务在输入未变且结果哈希完整时复用；变更任一物理依赖则重新计算。

任务状态包括 `acquired`、`decoding`、`decoded`、`waiting_profile`、`waiting_version_alignment`、`geometry`、`calculating`、`complete` 与隔离的失败状态。等待补全的任务会保留已解码结果，配置补齐后再次 `run` 即可继续。

## 四、导出轻量结果

已有上传 NPZ 数据直接导出：

```bash
PYTHONPATH=backend python -m wt_atlas import-atlas \
  --input /path/to/unpacked/wt_four_vehicle_probability_data
```

后端计算完成后：

```bash
PYTHONPATH=backend python -m wt_atlas export
# 或完成计算后立即导出
PYTHONPATH=backend python -m wt_atlas run --export
```

默认导出到 `data/exports/probability/`，与轻量网站产物 `dist/site/` 独立；`import-atlas` 和 `export` 仍支持 `--output` 指定其他数据目录。普通站点构建不会复制这些大分片。不同物理网格或版本分成不同 atlas，防止无说明混合。发布前校验来源文件哈希、维数、角度顺序、状态、概率范围与量化误差。

发布物只有索引、按需单视角二进制、gzip 副本及原生分辨率缩略图。原始 GRP、NPZ、射线层、全部 HD 图片、材料大表和中间 `model_required_charge_mm` 都留在构建端。静态服务器支持 gzip 时可直接复用压缩副本；普通静态服务器下前端请求 `.bin.gz` 并用浏览器 `DecompressionStream` 解码。

### 独立数据发布与磁盘预算

全量分片按 10+GB 数据集独立管理。`data/work/` 中的 GRP、NPZ 与射线缓存只在离线计算机保存；`data/exports/probability/` 只包含可公开的概率导出物。不要把整个工作目录打包到静态站点。

CPU 结果使用原子替换写入，导出中间目录优先以硬链接复用已验证 NPZ；跨磁盘或不支持硬链接时使用原子复制。原始 CUDA 兼容生产器不保证原子替换，因此仍使用复制。单个导出进程一次读取一个目标/弹药 NPZ，包含该文件的全部视角；并行进程数仍需按本机内存调整。

重复导出会校验并复用已有 `.bin` 和 gzip 分片；不自动删除旧内容寻址文件，避免破坏仍在使用旧索引的站点。发布前生成只包含当前引用文件的上传清单：

```bash
python scripts/prepare_probability_release.py \
  --source data/exports/probability \
  --report reports/probability-release.json \
  --list-files reports/probability-files.txt
```

验证器按块计算 SHA-256，并解压检查 gzip 的长度与原始哈希，拒绝目录越界、符号链接、损坏文件与冲突元数据。报告写入前会完成全部验证，过程不复制 10+GB 数据，也不执行上传。`release_id` 是完整 `index.json` 的 SHA-256；`files` 记录每个相对路径、字节数和哈希，顺序为分片/预览在前、索引在后。

将清单内文件发布到对象存储或独立静态数据服务的不可变版本前缀 `probability/<release_id>/`，完成后再切换站点所引用的数据地址。保留仍被现有站点引用的版本。具体站点配置见 [轻量部署](DEPLOYMENT.md)。

## 模型与验证边界

输入、CPU 实现与上传参考都属于分层直线路径的条件估计，未标定为游戏穿透率，更不代表击毁率或胜率。NERA 动态失稳、ERA 激活/破坏、有限口径射线分组、串联/引信、破片和伤害状态仍未完整实现。`model_required_charge_mm` 仅为栈消耗中间量，导出不称作 RHAe。

CPU 层模型与原数组核对，四车 × 两弹药共采样 32,768 条射线，状态和材料标志没有差异，最大概率差约 `5.96e-8`。这是模型实现对照，不是游戏验证。CPU 几何另有独立版本，在 T-72B 正面原分辨率测试中，59,458 条射线有 22 个边界状态与上传原始运行时参考不同；不会把两套几何标成同一精度/同一缓存。

量化测量、解码报告、CPU 对照与断点复用的真实结果位于 `reports/` 和 `data/exports/probability/export-report.json`。测试命令：

```bash
PYTHONPATH=backend python -m unittest discover -s tests -p 'test_*.py' -v
```

所有测试里的人造数值只作为验收夹具，不能流入生产目录。

复现上传原始数组的数值核对（需要将原始数据 ZIP 解包到指定目录）：

```bash
PYTHONPATH=backend python -m wt_atlas.validation \
  --atlas /path/to/unpacked/wt_four_vehicle_probability_data \
  --report reports/cpu-model-validation.json
```

## 来源刷新

默认采集复用已捕获的来源响应。主动更新 Wiki 与腾讯调整表：

```bash
PYTHONPATH=backend python -m wt_atlas sources --refresh --changes
```

仅复用固定响应重建目录可省略 `--refresh`；`--changes` 明确要求重新映射腾讯方案。修改来源后重新运行决策构建和站点组装，以防目录与推荐快照不一致。

## 轻量结果进入队列决策

导出切片后执行 `PYTHONPATH=backend python -m wt_atlas summarize`。该命令用共享瞄准积分模型将每个视角压缩为带完整条件身份的摘要，输出 `data/snapshots/decision-probabilities.json`。随后全国推荐构建和浏览器 Worker 读取同一 bundle。当前上传图生成的摘要始终 `reference_only:true`，不会推断任何载具的弹药归属或伪造当前版本绑定。未来经过实际核验的记录通过完整身份门禁进入精确配对；接口、命令与验证契约见 [DECISION_PROBABILITIES.md](DECISION_PROBABILITIES.md)。
