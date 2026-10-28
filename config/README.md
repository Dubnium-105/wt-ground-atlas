# 配置职责

| 文件 | 职责 |
|---|---|
| `hosting.json` | 本地服务 bind/port、生成站点目录、不可变资源缓存秒数 |
| `deployment.json` | 热图发布模式、外部版本地址、可选索引哈希、站点体积预算 |
| `pipeline.json` | 离线来源、计算并发/算法、数值配置、工作和概率输出路径 |
| `sources.json` | 采集入口、固定提交、解析与单位换算 |
| `model.json` | 决策参数、经验权重与上下文默认值 |
| `ui.json` | 呈现、分页、配色、交互范围；不存储车辆结论 |
| `font.json` | 字体来源与子集构建 |
| `qa.json` | 完整生产浏览器验收地址与预算 |

`deployment.probability.mode` 可为 `disabled`、`external`、`embedded`。默认 disabled。external 的 `base_url` 是绝对 HTTP(S) 版本目录，不能包含凭据、查询字符串或片段；线上应使用 HTTPS。`index_sha256` 可固定远端索引，即使站点构建机没有本地索引也能校验浏览器下载。

`budgets.site_bytes` 和 `budgets.file_bytes` 是实际文件字节（包括保留的 gzip 副本），不是网络压缩后的估算。正数为上限，0 为显式关闭该项限制。默认 100 MiB / 25 MiB；完整 embedded 交付必须在确认容量后调整，常规 10+GB 数据应选择 external。

命令行 `--probability-mode` / `--probability-base-url` / `--output` 覆盖相应构建配置。仅提供 `--probability-base-url` 即选择 external。离线概率路径仍由 `pipeline.paths.static_probability` 管理，保留既有键兼容，默认值已改为 `data/exports/probability`；`summary.input_index` 必须指向相同导出索引。

无需把私有凭据写入这些 JSON。站点构建只复制必要的模型和 UI 公开配置；数据上传由独立的计算/发布环境执行。
