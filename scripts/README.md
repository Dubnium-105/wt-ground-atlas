# 工具入口

| 脚本 | 输入 → 输出 |
|---|---|
| `../build.py` | 快照 → 推荐派生缓存 → 有界静态站点；普通构建入口 |
| `build_decisions.mjs` | 快照与共享引擎 → `data/derived/recommendations/` |
| `build_web.py` | 已验证的快照/推荐 → `dist/site/`；标准库组装器 |
| `decision_bundle.py` | 完整研究摘要 → 浏览器运行所需配对投影；保留来源哈希，不修改原快照 |
| `site_lib.py` | 构建与打包共用的路径、文件和预算检查；不是 CLI |
| `build_target_layers.py` | 客户端层缓存 → 目标抽样快照与报告 |
| `build_probability_summaries.mjs` | 概率导出 → 决策轻量摘要 |
| `prepare_probability_release.py` | 概率索引/分片 → 哈希校验后的上传清单；不复制、不上传 |
| `package_release.py` | 已生成站点 → 可直接解压部署的 ZIP；不运行离线计算 |
| `verify_release.py --root ...` | 解压站点 → 逐文件 SHA-256 校验 |
| `check_package.py --archive ...` | ZIP → 临时解压、CRC、哈希与发布边界检查 |
| `serve.py` | 仅服务生成站点；不暴露源码或计算数据 |
| `validate_client_build.py` | 真实客户端全量批次与当前站点 → 生产一致性报告 |
| `validate_owned_ammo.mjs` | 推荐与目录 → 弹药归属检查 |
| `build_font.py` | 字体来源/字符输入 → 浏览器字体资产 |

日常路径见 [开发指南](../docs/DEVELOPMENT.md)，发布选项见 [部署指南](../docs/DEPLOYMENT.md)。`package_release.py` 现在只打包站点，旧的“整个工作区离线交付包”语义已取消。源码备份和计算数据保留各自的版本，不进入网站托管包。
