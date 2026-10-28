# 轻量托管与概率数据发布

## 选择部署边界

生产网站只上传 `dist/site/` 的内容。10+GB 的概率数据上传到独立的对象存储/CDN 或静态数据服务器。前端和数据源可以使用同一服务商，也可以分别部署；协议只要求浏览器能通过 HTTP(S) GET 读取文件。

不要把原始 GRP、NPZ、客户端提取文件或整个仓库上传到网站。`package_release.py` 只处理生成站点，不再包含源码、vendor、快照和计算缓存。

| 目标 | 配置 | 数据发布 |
|---|---|---|
| 先上线推荐、图鉴、版本与对抗 | `disabled`，默认 | 无参考热图；保留轻量计算资料 |
| 完整功能且站点轻量 | `external` + 版本目录 URL | 热图单独发布，按需访问 |
| 全部数据同源或离线交付 | 显式 `embedded` | 复制被索引引用的数据，需要相应容量与预算 |

GitHub Pages 的官方限制列明已发布站点不能超过 1 GB，因此 10+GB 概率数据不应塞进 Pages 站点。其他静态托管也应分别核对当前的总容量、文件数、单文件和流量限制。本文没有把任何对象存储假定为免费或无限容量。参考：[GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)。

## 1. 在计算端完成导出并核验

流水线默认写到 `data/exports/probability/`，路径来自 `config/pipeline.json`。已有完整导出可以直接使用，不必为了迁移重新计算。

```bash
python scripts/prepare_probability_release.py --source data/exports/probability --report reports/probability-release.json --list-files reports/probability-files.txt
```

验证器只读取索引引用的文件，流式计算哈希并验证 gzip 解压后的长度与内容，不复制整份数据、不上传、不删除旧分片。报告包含：

- `release_id` / `index_sha256`：完整索引 SHA-256。
- `publish_prefix`：建议的 `probability/<index_sha256>/`。
- `files`：每个引用文件的相对路径、SHA-256、字节数。
- `file_count`、`total_bytes`：实际需要上传的唯一文件数量和总量。
- `all_referenced_hashes_verified`、`gzip_payloads_verified`：验证结果。

`probability-files.txt` 按数据文件在前、`index.json.gz` 和 `index.json` 在后的顺序输出。报告与列表写在 `reports/`，不写进不可变的数据目录。

## 2. 发布独立的数据版本

把文件清单中列出的相对路径上传到新的、不可变的版本前缀，例如：

```text
https://data.example.com/probability/<index_sha256>/index.json
https://data.example.com/probability/<index_sha256>/index.json.gz
https://data.example.com/probability/<index_sha256>/shards/<target>/<ammo>/<sha256>.bin
https://data.example.com/probability/<index_sha256>/shards/<target>/<ammo>/<sha256>.bin.gz
```

先传分片及预览，再传索引。使用已有的对象存储客户端或静态目录上传方式；验证脚本不需要云账户密钥，站点也不保存上传凭据。按清单上传可排除已失效但还保留在本地的旧分片。不要对线上版本目录执行带删除语义的同步覆盖。

索引中的 `url`、`gzip_url` 和 `preview_url` 保持导出时的相对路径，浏览器相对于索引的地址解析。站点位于 `/wt-ground-atlas/` 等子路径不会改变数据地址。索引可选的 `shards_base_url` 适用于进一步把大文件放在单独前缀的场景，但普通部署无需设置。

**同一个版本目录一经发布就保持不变。** 数据更新产生新索引哈希和新前缀；保留旧版本直到没有站点引用。跨版本硬链接、服务器文件系统去重或对象存储策略可以进一步优化容量，但不要通过覆写旧对象破坏回滚。

### CORS 与响应类型

若站点和数据 URL 不同源，数据源需要允许站点 Origin 的 GET/HEAD。可以参考 [deploy/cors.example.json](../deploy/cors.example.json)。公开只读数据也可以按托管规则选择 `*`，本项目不携带用户凭据。

| 资源 | 建议 Content-Type | Content-Encoding |
|---|---|---|
| `*.json` | `application/json` | 服务器按普通 JSON 策略处理 |
| `*.bin` | `application/octet-stream` | 通常不设置 |
| `*.gz` sidecar | `application/gzip` | **不额外声明 gzip**；文件本身已压缩 |
| `*.mjs` | `text/javascript` | 普通静态资源策略 |

客户端兼容 HTTP 层已经解压 gzip 的响应，也会在 gzip 请求失败后回退到原始 URL；仍应正确配置类型，避免重复压缩或错误地返回 HTML。跨域错误响应也尽量包含 CORS 头，使浏览器能取得真实状态。

例如 R2 的 CORS 与公开访问是分开的配置；官方文档说明自定义域名会按策略响应跨域请求，修改策略后可能需要刷新已缓存对象的响应头。参考：[Configure CORS](https://developers.cloudflare.com/r2/buckets/cors/)。用命令行检查时携带真实的 `Origin`，仅能直接打开 URL 不代表浏览器跨域 fetch 已获准。

## 3. 构建轻量站点

准备站点所需快照后：

```bash
python build.py --probability-base-url https://data.example.com/probability/RELEASE_ID/
```

把 `RELEASE_ID` 替换成上一步的 `release_id`。仅给 `--probability-base-url` 就会选择 external；也可固定配置：

```json
{
  "schema_version": 1,
  "probability": {
    "mode": "external",
    "base_url": "https://data.example.com/probability/RELEASE_ID/",
    "index_sha256": null
  },
  "budgets": {"site_bytes": 104857600, "file_bytes": 26214400}
}
```

此文件是 `config/deployment.json` 的结构。若计算端有本地索引，构建器自动记录该索引的 SHA-256 和长度，远端必须与之匹配；若构建机不持有概率数据，可把发布报告的 `index_sha256` 填入配置，固定远端内容。外部模式不会访问网络验证远端上线状态，也不会读取所有分片；上线后仍需实际浏览器检查。

不持有概率文件并不意味着可以省略推荐构建输入。仍需要目录、方案、目标抽样层及报告；也可在数据准备环境预先生成推荐，用 `--skip-decisions` 组装，缓存身份仍会校验。详见 [DEVELOPMENT.md](DEVELOPMENT.md)。

组装器会投影浏览器决策摘要：只保留显式 `reference_only: false` 记录、模型身份和目标存在信息，并记录原快照哈希与省略计数。完整参考研究记录不会原样发送给 Worker，数据发布清单的 `files` 也不进入公开报告；离线原文件保持不变。

默认站点总量不超过 100 MiB、单文件不超过 25 MiB。预算计算实际产物字节，包括 JSON 与 gzip 两份。超限会失败并保留旧站点，先检查是否误内嵌大数据或轻量摘要规模异常，再有依据地调整预算。

## 4. 打包、校验与托管

```bash
python scripts/package_release.py --output dist/wt-atlas-site.zip
python scripts/check_package.py --archive dist/wt-atlas-site.zip
# 若已解压到某目录：
python scripts/verify_release.py --root /path/to/extracted-site
```

ZIP 根目录直接是 `index.html`，可以上传解压后的全部站点文件；不需要把外层项目目录也上传。也可以直接上传 `dist/site/` 内容。使用原始 GitHub 分支作为 Pages 根目录时，不能直接选择 `web/`；应通过自己的构建/发布流程提供生成目录。

站点不要求应用 API、Python/Node 常驻进程或服务端数据库。静态服务器必须支持 ES module 的 MIME 类型与 Worker；请通过 HTTP(S) 打开，不能双击 `file://`。仓库提供 [Nginx 示例](../deploy/nginx.site.conf.example)；托管平台可在其控制台配置同等规则。

### 缓存规则

- `index.html`、`app.mjs`、`worker.mjs`、`data-client.mjs`、`shared/*.mjs`、`styles.css`、`data/manifest.json` 及其 gzip：`no-cache` 或短 TTL。主 manifest 的 fetch 显式使用 `no-cache`。
- 带内容哈希的 JSON/二进制分片：`Cache-Control: public, max-age=31536000, immutable`。
- 概率索引位于不可变的版本前缀时，可随该版本长缓存；不要把可变的 `latest/index.json` 与长期 immutable 缓存结合。
- 资源不存在时返回 404；不要让数据或 `.mjs` 路由返回 SPA 的 HTML 首页。

发布整套站点版本时，避免新 manifest 先可见、它引用的文件尚未上传。优先用托管端的版本化部署或整体目录切换。仓库本地构建的临时目录校验与替换不等于远端 CDN 已完成原子发布。

## 显式嵌入与本地离线交付

```bash
python build.py --probability-mode embedded
```

这个模式复制索引引用的完整数据，随后可用同一静态 ZIP 流程打包。若数据超过默认预算，应明确调整 `deployment.json` 的预算；0 表示关闭对应检查。计算端需要同时容纳原导出、站点副本、临时新版本和已有产物，10+GB 数据可能产生显著额外占用。

因此 embedded 只用于确实需要完整离线包或足够大的同源主机，常规轻量站点使用 external。`.gz` 以外的原始切片保留用于兼容和回退，不在本次重构中删除。

## 从旧目录迁移

旧版将推荐与概率输出写在 `web/data/`，并把整个项目打成离线包。新路径如下：

| 旧路径/行为 | 新路径/行为 |
|---|---|
| `web/data/probability/` | `data/exports/probability/` |
| `web/data/recommendations/` | `data/derived/recommendations/` |
| `web/` 同时作源码和部署目录 | `web/` 源码，`dist/site/` 部署产物 |
| `web/shared/engine.mjs` 源码副本 | `shared/engine.mjs` 唯一源码；构建复制 |
| 全仓库 release ZIP | 站点 ZIP 与单独概率上传清单 |

迁移时先停止仍在写输出的离线任务，将旧概率目录移动到新路径，同一文件系统内移动可避免复制 10+GB。保持索引及相对分片路径不变，然后运行发布验证器。也可暂时在 `pipeline.paths.static_probability` 与 `summary.input_index` 显式保留旧路径，构建器不会把 `web/data/` 混入新站点。

推荐缓存可以移动到新路径后校验复用。解码器提取改变了摘要工具的代码身份，下一次 `summarize` 可能重新生成轻量摘要；物理内核文件和物理 job fingerprint 输入没有因存储层重构而改变，完成且配置一致的物理计算缓存继续复用。无需为了目录迁移删除 `data/work/` 或重新跑全部射线计算。

验证新站点后，原来的 `web/data/` 可以按你的数据保留策略归档或清理。新构建不会自动删除旧计算数据或旧远端版本。

## 上线检查与回滚

用实际浏览器从你的正式站点打开推荐页，再进入防护研究：确认首屏没有热图请求，研究索引/切片来自预期版本域名，gzip 或原始路径可用，跨域没有被阻止。断开数据源时应只影响研究页，并显示重试。真实生产覆盖和模型数值验收另行执行，合成冒烟测试不能替代它。

回滚时恢复上一版站点产物；它的 manifest 指向仍保留的旧概率版本。修改数据必须发布新版本，避免旧站点在同一 URL 下突然读取不同物理内容。
