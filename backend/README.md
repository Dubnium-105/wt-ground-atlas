# 离线数据后端

`wt_atlas` 只在数据准备/计算环境运行，浏览器访问与静态托管不启动此后端。采集、客户端几何、材料绑定、概率计算和验证的产物在 `data/`；原始数据与工作缓存不进入站点。

```bash
python -m pip install -r requirements.txt
# Linux/macOS；PowerShell 使用 $env:PYTHONPATH="backend"
export PYTHONPATH=backend
python -m wt_atlas --help
```

完整流程见 [客户端后端](../docs/CLIENT_BACKEND.md) 和 [流水线](../docs/PIPELINE.md)。概率默认导出到 `data/exports/probability/`，单独发布；网站构建只复制轻量摘要与当前推荐。

`vendor/` 保留固定版本的参考代码、提取工具、样例和来源记录，不作为生产网站依赖。核算源码克隆体积时仍需包括这些审计资料；它们不会进入 `dist/site/` 或站点 ZIP。

`storage.py` 管理计算结果的原子发布，`common.py` 管理已校验的 staging 复用。存储改动与 `cpu_model.py` 物理内核分开，避免纯 I/O 重构让已完成物理缓存失效。
