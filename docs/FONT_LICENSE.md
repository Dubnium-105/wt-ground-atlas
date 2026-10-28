# 本地中文字体与可复现构建

站点自带 `web/assets/wt-atlas-sans-sc.woff2`，浏览器从本地静态目录加载，不依赖 Google Fonts 在线 CSS 服务。该字体是官方 Noto Sans SC 的文字子集，派生字体名为 **WT Atlas Sans SC**，保留可变字重。CSS 使用 `font-display: swap`，并将此字体放在 `--sans` 的首位。

## 官方来源与许可

- 仓库：https://github.com/google/fonts
- 固定提交：`a85815a42757630ce188fdad368c2dfc444d4773`
- 官方文件目录 API：https://api.github.com/repos/google/fonts/contents/ofl/notosanssc?ref=a85815a42757630ce188fdad368c2dfc444d4773
- 原字体：`ofl/notosanssc/NotoSansSC[wght].ttf`
- 原字体 SHA-256：`a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`
- 许可：SIL Open Font License 1.1；原文完整保存在 `FONT_LICENSE.txt` 和静态资源中的 `assets/OFL-NotoSansSC.txt`。

构建脚本同时校验 SHA-256 与官方 GitHub API 给出的 Git blob SHA-1。原始版权声明和 OFL 原文保持完整。派生字体使用独立字体名，没有使用许可证中的保留字体名。该字体的修改和分发继续适用 OFL；这不改变项目其他代码的许可。

## 重新生成

```bash
python -m venv /tmp/wt-atlas-font-env
/tmp/wt-atlas-font-env/bin/pip install fonttools==4.61.1 Brotli==1.2.1
/tmp/wt-atlas-font-env/bin/python scripts/build_font.py
/tmp/wt-atlas-font-env/bin/python scripts/build_font.py --check
```

首次构建从上述固定提交下载原始 TTF 和许可证到临时缓存目录；之后可以离线重建。`--cache-dir` 可指定缓存位置，`--check` 只验证已生成子集的哈希与当前界面字符覆盖，不下载、不写入。完整 TTF 不复制进项目或静态发布目录。

文字集合来自当前网页和共享引擎代码、配置、目录中的实际车辆/国家/分支/文件夹/弹药/挂载名称，以及模拟调整表。完整原始属性转储不用于扩充字体。所有已请求的中日韩统一表意文字必须存在，否则构建失败；源字体本身不含的装饰字符仍使用系统后备字体，并在来源清单中逐个记录。界面或名称增加字符后应重新运行构建。

`web/assets/font-provenance.json` 记录上游提交、原始文件与许可证哈希、每份文字输入的哈希、依赖版本、字符覆盖、可变轴，以及最终 WOFF2 的字节数和 SHA-256。运行脚本时会输出当前子集大小，避免将旧统计误当成新文件的结果。
