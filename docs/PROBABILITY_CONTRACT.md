# 轻量概率数据契约 v1

## 索引

`data/exports/probability/index.json` 是独立概率数据发布的入口。轻量站点通过 `manifest.probability.url` 指向它的外部版本地址；分片 URL 相对于该索引解析，与站点所在子路径无关。仅显式 `embedded` 模式会把数据复制到 `dist/site/data/probability/`。`entries` 每项是一个**目标构型 × 精确参考弹药 × 物理条件**，而不是攻击载具的装备证明。

索引保存稳定目标 ID、构型 ID、弹药 ID、弹药参数 SHA256、原客户端版本/提交、GRP 哈希、配置哈希、物理射距、模型版本、原 NPZ 哈希、坐标约定、米制采样间距、实际宽高及有序角度。静态缓存键不包含 BR。使用逐车配对前须同时验证攻击车辆实际拥有这颗弹、目标构型/版本、物理射距、姿态与模型配置；缺一个必要身份就不能标为该配对的精确计算。

`shards` 每项含 angle_index、angle_deg、url、gzip_url、SHA256、压缩/解压长度及状态摘要。角度顺序和网格尺寸来自源元数据，不能依据文件名猜测；显示缩放不会增加射线样本。

## 二进制布局

设 `N = width × height`。文件无额外头部，由索引描述：

1. N 个 little-endian uint16：量化概率。
2. N 个 uint8：原 `status`。
3. N 个 uint8：原 `material_response_flags`。

所有平面按照 `image_y, image_x` 行优先存储，左至右、上至下。读取量化最大值和未知哨兵时必须使用索引的 `encoding.quantization_max` 与 `encoding.unknown_value`。有限值 `q / quantization_max`；未知哨兵变回 NaN。真正 0 概率仍为数值 0。

```javascript
const n = entry.width * entry.height;
const view = new DataView(buffer);
const p = new Float64Array(n);
for (let i = 0; i < n; i++) {
  const q = view.getUint16(i * Uint16Array.BYTES_PER_ELEMENT, true);
  p[i] = q === index.encoding.unknown_value ? NaN : q / index.encoding.quantization_max;
}
const status = new Uint8Array(buffer, n * Uint16Array.BYTES_PER_ELEMENT, n);
const materialFlags = new Uint8Array(buffer,
  n * (Uint16Array.BYTES_PER_ELEMENT + Uint8Array.BYTES_PER_ELEMENT), n);
```

## 状态与材料

| status | 原始语义 | 展示与积分 |
|---|---|---|
| 0 | 背景 | 保留落空质量；不能在目标内重新归一化 |
| 1 | 当前射线上没有被保护内部目标 | 单独统计，不声称载具不可受损 |
| 2 | 几何或材料未知 | 保留 NaN，给出结果不确定区间 |
| 3 | 模型条件估计可计算 | 使用有限概率，不称作击毁率 |

材料位分别记录来源 NERA 家族、反应装甲、来源间隙/栅格空气、变厚度标志；索引给出位值说明。它们只是来源响应标记，不是已验证的动态机制或性能加成。

几何层中的 `ray_id = angle_index × width × height + image_y × width + image_x`，材料索引 `node` 必须对应同一 `.layers.json`。没有出口的正无穷在后端保留，固体几何遇到它时归为未知，不当作可加和的正常厚度。

## 发布精度

当前交付输出真实量化误差来自全量有限像素测量，报告中分别列最大和平均绝对误差。压缩不影响像素采样；不发布 `model_required_charge_mm` 为物理防护厚度。各输入文件和输出切片都有内容哈希，可追踪变更、增量更新和回滚。

## 传输与发布

纯解码实现位于 `shared/probability-codec.mjs`，离线摘要和浏览器共享这一份代码。`web/data-client.mjs` 只负责 HTTP、解压、哈希校验与有界缓存，修改缓存策略不会改变解码器的来源哈希。

`.gz` 是显式 gzip sidecar，按 `gzip_url` 原样访问；不要通过原始 URL 猜测压缩文件名。浏览器检查响应字节，兼容 HTTP 层已解压的情况；压缩加载失败后尝试原始文件。未知值、状态和材料标志在两种传输路径中完全一致。

发布前运行 `scripts/prepare_probability_release.py`，按生成的文件清单上传到不可变版本前缀，先传分片，再传索引。详细步骤、CORS 与缓存策略见 [DEPLOYMENT.md](DEPLOYMENT.md)。
