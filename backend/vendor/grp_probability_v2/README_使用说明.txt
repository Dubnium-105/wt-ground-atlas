GRP 多角度击穿概率脚本包 · 当前 V2 算法
====================================

一、用途与内容

直接读取 GRP，解码碰撞三角网格，按指定水平角度生成完整装甲区间，
再按弹药配置计算逐像素击穿概率，输出 PNG、NPZ 原始数组和 HTML 图集。

包含 BMPT-72、T-72B、Leopard 2A4、Leopard 2A5 的原始 GRP、逐节点
DamageParts/材料绑定、原加载器输出的 BLAS 配置，以及 M829A2/M830 示例弹药。
不需要运行游戏，不需要原工作区，不包含已算好的射线层缓存或概率图。

固定源版本：客户端 2.59.0.86；Datamine：
aa326f10c6ba7d523647572368ead2e4d51ffb4d

GRP 本身不包含全部材料和弹药数据。四车按完整文件 SHA256 绑定配置。
其他车辆或其他版本 GRP 会明确报错，不会套用四车的旧材料或几何。

二、环境

当前项目使用 Python 3.14、NVIDIA GPU、CUDA 13.x（含 NVRTC）。
依赖的固定版本在 requirements.txt；CUDA/驱动不是本脚本包的一部分。
在解压目录中打开终端，使用已有 Python 或自己的虚拟环境：

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

下列命令中的 python 均可替换为 .venv\Scripts\python.exe 或已安装依赖的
Python 完整路径。仅查看帮助、车辆列表不需要加载 GPU：

python grp_probability.py --help
python grp_probability.py --list-vehicles

三、直接运行

1. 四车 × 两种弹药 × 每 15°，完整一周 24 个视角：

python grp_probability.py --out results

默认从 examples/grp 读取四个随包原始 GRP。生成 192 张采样 PNG、
192 张高清展示 PNG、8 个 NPZ、run.json 和 index.html。

2. 使用自己提供的同版本 GRP，计算一辆车：

python grp_probability.py --grp "D:\models\germ_leopard_2a5.grp" --ammo M829A2 --step 15 --out results_a5

车辆由文件 SHA256 识别，文件名可以改变。

3. 只处理包内某辆车，或指定多个任意角度：

python grp_probability.py --vehicle ussr_bmpt_72 --ammo M829A2 --angles 0 15 22.5 90 180 270 --out results_bmpt

可用 ID：ussr_bmpt_72、ussr_t_72b、germ_leopard_2a4、germ_leopard_2a5。
--grp 可接多个文件；--grp-dir 可读取指定目录中的 *.grp；--vehicle 可接多个 ID。
--step 为小于 360 且能整除 360 的正整数；--angles 范围为 [0,360)，保留指定顺序。

4. 增加真实采样密度，而非仅放大图片：

python grp_probability.py --vehicle germ_leopard_2a4 --ammo M829A2 --pitch 0.0125 --hd-scale 4 --out results_dense

--pitch 单位米。默认 0.025 m，434×137 真实射线／视角。
0.0125 m 为 868×274；0.005 m 为 2170×685，计算量、显存和内存需求均明显增加。
所有载具/角度使用同一米／像素、物理画布和色标。默认物理画布约 10.85×3.425 m。
--hd-scale 默认 8，是最近邻展示放大；0 表示关闭高清 PNG。
默认采样下高清画布为 4050×1500，其有效射线仍为 434×137。

5. 更换弹药参数并复用几何缓存：

复制 data/ammunition.json 为 my_ammo.json，修改后执行：

python grp_probability.py --ammo-json my_ammo.json --cache-dir results/cache --out results_new_ammo

配置可为单个弹药对象或对象列表。--ammo 可按 name 过滤。
name 必须使用英文、数字、下划线或短横线，且不能为 Windows 保留名称。
penetration_mm 是所选条件下的穿深输入；dispersion 为均匀相对波动半宽，
damage_caliber_mm 为伤害口径，不能用炮口径任意替代。
mechanism_id 必须与 data/mechanisms.json 的索引一致，不能仅凭弹种名称猜测。
slope_rows / ricochet_rows 保存对应口径比与入射余弦曲线。
normalized_distance_curve 为 KE 逐层剩余穿深预算使用的归一化距离曲线。
distance_m 只作为原配置元数据保留；修改它不会自动重新计算远距离穿深或曲线。
新的实际弹药需要提供其对应版本的材料机制与曲线，简单改名称不等于完成移植。

6. 分离首次建缓存与之后的计算：

python grp_probability.py --build-only --cache-dir geometry_cache --out cache_build
python grp_probability.py --cache-dir geometry_cache --ammo-json my_ammo.json --hd-scale 0 --out new_maps

缓存指纹包含 GRP、配置、角度、采样间距、运行时几何数据与代码哈希。
弹药变化可以复用；角度、分辨率、配置或代码变化会建立新缓存。
--rebuild 强制重建。没有按弹药缓存概率结果。

--no-images 只输出数组；--uncompressed-arrays 关闭 NPZ 压缩以减少磁盘导出开销。
--workers 4 控制图片导出线程；--device 0 选择 GPU；--ray-batch 128 为几何查询批量。
同一输出目录会覆盖同名数组/PNG；图集和 run.json 只列出本次运行的结果。
需要保留不同参数结果时请用不同 --out。

四、原始数据与坐标

arrays/<vehicle>.<ammo>.npz：

penetration_probability : float32 [视角, 高, 宽]，范围 0～1；无数值处为 NaN。
model_required_charge_mm : float32 同形，中间装甲消耗量；不是 RHAe，也不是完整游戏等效防护。
status : uint8 同形：0 背景；1 无内部目标；2 几何/材料未知；3 当前算法可计算。
material_response_flags : uint8 同形位掩码：
  bit 0 (1) NERA 类；bit 1 (2) 反应装甲；bit 2 (4) 源间隙/栅格空气类；bit 3 (8) 变厚度。
angles_deg : float64，视角顺序；pixel_pitch_m / world_width_m / top_y_m / bottom_y_m 为米制参数。
center_xz_m : float64 [2]，该车旋转中心。

读取示例：

import numpy as np
with np.load("results/arrays/germ_leopard_2a5.M829A2.npz", allow_pickle=False) as z:
    probability = z["penetration_probability"]
    valid = z["status"] == 3
    angles = z["angles_deg"]

坐标：+Y 向上；yaw 0° 摄像机在 +X 方向，射线沿 -X；正角度绕向 +Z。
yaw 0° 图片左侧为 +Z。像素取中心点。0° 为本项目采用的正面。
背景、无内部目标和未知分别保留掩码，不能当作 0% 击穿。
PNG 红色 0%、黄色 50%、绿色 100%；紫色未知；灰色无内部目标。
NERA/ERA 区域带浅色斜纹，表示完整动态行为未验证，不表示缺失材料数据。

五、当前算法范围

保留同节点所有分离的入射段，配对下一条不同距离的出射；共享三角面重复交点
按 10 微米聚类。使用原始静止绑定姿态，数学逆矩阵与原始运行时网格局部求交。
按材料机制的质量、厚度上限、入射角曲线和跳弹曲线逐层处理。
KE 保留薄部件判断、反应装甲几何分支、源距离曲线的逐层预算和 16 次阈值搜索；
CE 保留装甲层间空气损耗，终点为第一内部目标前的装甲堆栈出口，不计舱内末段空气。
穿深离散为整发均匀分布，跳弹存活率按各层独立相乘。

这是用户接受的简化条件概率算法。未实现完整 NERA 偏转/失稳/完整性变化、
ERA 激活/破坏、有限口径 19 射线分组、引信/前级/串联、破片/致命伤害和游戏概率标定。
不能将这里的颜色或中间消耗量宣称为完整游戏概率或经验证 RHAe。
未增加猜测的 NERA 倍率；未知仍保留 NaN。

六、嵌入交互应用

首次从 GRP 建缓存后，可常驻 GPU，复用同一 PreparedVehicle 实例：

from pathlib import Path
import sys
sys.path.insert(0, str(Path("lib").resolve()))
from wt_armor.layered_penetration_v2 import PreparedVehicle
import json
ammo = json.loads(Path("data/ammunition.json").read_text(encoding="utf-8"))[0]
model = PreparedVehicle("geometry_cache/<vehicle>/<fingerprint>/<vehicle>.layers.json")
new_ammo = dict(ammo, penetration_mm=600.0)
rgb = model.calculate_rgb(new_ammo)  # 全部缓存视角的 [角度, 高, 宽, RGB]
probability, charge, status = model.calculate(new_ammo)

calculate_rgb 使用同一数值核，返回基础颜色；图片导出模块额外叠加 NERA/ERA 斜纹。
同一 model 会复用 GPU 输出缓冲区，不能由多条线程同时调用；高频更新不应每次重建实例。
设备数组模式 model.calculate(new_ammo, return_host=False) 可避免立刻回传数值数组。
首次 GRP 解码/射线求交、CUDA 初始化和 PNG/NPZ 磁盘编码属于额外耗时。
亚秒级交互依赖已准备的几何/GPU，并不承诺任意分辨率的首次 GRP 构建或磁盘导出亚秒完成。

七、源码与来源

grp_probability.py 为入口；lib/grp_geometry.py 为可移植几何构建器；
lib/grp_export.py 为图片导出；lib/grp_decoder.py 为原 GRP 解码函数的无路径副作用摘录。
lib/wt_armor 中数值核与局部几何模块按原版本逐字节复制。
data/vehicles/*.json 为四车已解析的逐节点材料/绑定配置，保留源路径、类名和来源哈希。
data/runtime 为原加载器产生的几何数据，不是射线答案或概率答案。
provenance/source_manifest.json 记录所有原文件与导出转换；MANIFEST.json 记录包内文件哈希。

扩展其他车辆必须准备匹配版本的完整 vehicle JSON、GRP、runtime loader 的节点记录与
BLAS 数据，加入同一结构的数据目录后通过 --data 指定；本包没有通用的任意车辆配置提取器。
仅凭一个新 GRP 不能恢复它的 DamageParts、装甲材料和弹药机制。
