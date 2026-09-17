---
name: multi-search-engine
description: >-
  面向可访问互联网环境的全网多源检索 Skill。支持中国大陆与国际搜索入口、任意可访问 HTTP/HTTPS 网站、中文与英文查询、site 定向意图校验、结果去噪、摘要补全、原始链接识别、多入口语义质量判定、反爬/限流/DNS/SSL/TLS 失败降级、结构化证据留痕，以及 Windows / Linux 双平台和 Python 3.7+ 兼容。
license: MIT
metadata:
  version: "1.5.0"
  displayName: "全网多搜索引擎"
  region: "GLOBAL"
  platforms: "Windows,Linux"
  python: ">=3.7"
---

# 全网多搜索引擎

用于**国内外全网检索**。不再限制“只能访问中国大陆网站”，也不假设国外搜索引擎一定可达。默认根据查询语言调整入口顺序，同时保留国内与国际入口；每个入口是否真正可用，由当前运行环境和语义质量结果决定。

目标不是“HTTP 200 就算成功”，而是以**可访问、符合查询意图、可核验、可追溯**的方式发现来源。

## 0. 执行契约

1. **正常检索只调用 `scripts/search.py`**。不要让 Agent 临时拼长 `curl`、复杂 `python -c` 或一次性抓取脚本。
2. **Windows + Linux**：文档路径统一使用 `/`。Linux 优先 `python3`，Windows 使用 `python`。
3. **Python >= 3.7**：核心脚本不得使用 3.8/3.9+ 才支持的语法或运行时特性。
4. **路径固定**：所有运行目录以 Skill 根目录为基准；每轮创建 `temp_search/runs/<run-id>/`，不依赖当前 CWD。
5. **编码固定**：结构化文件 UTF-8；stdout 只输出精简 ASCII JSON。不要把完整 `results.json` 直接输出到 shell。
6. **TLS 默认严格**：系统 CA / `--ca-bundle` / `MSE_CA_BUNDLE` / 可选 certifi；只有显式参数才允许降低证书校验，并必须留痕。
7. **允许任意可访问网站**：`fetch.py --engine direct` 可抓取任意 HTTP/HTTPS URL；不再做“中国区域名白名单”限制。
8. **搜索入口只是发现通道**：百度、Bing、Google 等都不是“证据来源等级”。最终证据独立性按真实 `publisher_domain` 计算。
9. **传输成功 ≠ 解析成功 ≠ 语义成功**。只有经过语义质量门槛的入口才能进入 `useful_engines`。
10. **`site:` 必须验证**：目标域名未命中时不得静默降级。
11. **验证码/403/429 不绕过**：记录、降频、换源。
12. **搜索摘要默认只是 C 级发现证据**；关键结论必须打开原文核验。
13. **语言与市场分离**：语言只决定入口顺序、`Accept-Language` 与界面语言提示；不会再自动把中文绑定中国市场、英文绑定美国市场。只有显式 `--market cn|us|jp...` 才增加地理市场参数。中英混合查询使用 `mixed`。
14. **网络失败默认只降超时，不硬跳过**：某 routing scope 出现纯网络失败后，后续同 scope 引擎使用 `--probe-timeout` 快速探测；默认 `--network-fail-skip 0`，每个引擎仍至少尝试一次。只有用户显式设置正数时才启用激进 scope 跳过。
15. **合并结果必须指向外部发布方**：搜索引擎自身链接（词典卡、导航、`/aclk` 广告、`search-host-link`）即使标题命中查询词，也不进入 `results.json` 候选。
16. **重定向分层记录**：`host_redirected` 只表示主机发生变化；只有已知区域化主机变化才标 `geo_localized=true`。不再把任意跨主机跳转都误称为地理重定向。
17. **公网 URL 安全边界**：`fetch.py` / `resolve_link.py` 默认拒绝 loopback、private、link-local、metadata 等非公网地址，降低公开 Agent Skill 的 SSRF 风险；只有明确需要内网时才使用 `--allow-private-network`。

## 1. 默认调用

### 自动全网路由（推荐）

```text
Windows: python scripts/search.py "具身智能 报告"
Linux:   python3 scripts/search.py "具身智能 报告"
```

`--profile auto` 是默认值：

- 中文/CJK 查询：国内入口优先，但仍会尝试国际入口；
- 英文/Latin 查询：国际入口优先，但仍保留国内入口；
- 入口不可访问、被验证码、语义质量差时自动继续后续入口。

网络不可达时使用**快速探测**而不是默认硬熔断：同一 routing scope 第 1 次纯网络失败后，后续入口使用 `--probe-timeout`（默认 6 秒），但默认仍会逐个至少尝试一次。`--network-fail-skip 0` 是默认；只有明确追求速度时才设置正数启用激进 scope 跳过。`scope` 仅用于路由和探测策略，不代表一个严格的网络故障域。

### 显式路由

```text
--profile auto     国内外都允许，按查询语言调整顺序（默认）
--profile all      国内外固定混合顺序
--profile cn       只使用国内搜索入口
--profile global   只使用国际搜索入口
```

例如：

```text
python scripts/search.py "OpenAI agent research" --profile global
python scripts/search.py "新能源汽车 出海" --profile all
```

也可完全指定：

```text
python scripts/search.py "query" --engines bing,duckduckgo,baidu
```

## 2. 当前搜索入口

### 国内入口

```text
baidu
weixin
sogou
so
toutiao
```

### 国际入口

```text
bing
duckduckgo
google
brave
```

这些只是**候选发现入口**。某入口在当前网络环境不可达时，属于运行时状态，不代表整个 Skill 失败。

详细能力矩阵见 `references/search-sources.md`。

## 3. site 定向

```text
python scripts/search.py "site:arxiv.org embodied intelligence"
python scripts/search.py "site:leadleo.com 具身智能 报告"
```

对 `site:`：

- 搜狗微信、今日头条等垂直入口会自动跳过；
- 国内/国际通用搜索入口均可参与；
- 至少一个结构化结果必须确认属于目标域名；
- 若搜索引擎忽略或改写 `site:`，返回 `semantic_ok=false` / `site-intent-unsatisfied`。

## 4. 已知 URL 直接访问

若已经知道原文地址，不要继续依赖搜索引擎召回。可直接使用：

```text
Windows: python scripts/fetch.py "https://example.com/article" --engine direct --name direct-01 --out-dir temp_search/direct
Linux:   python3 scripts/fetch.py "https://example.com/article" --engine direct --name direct-01 --out-dir temp_search/direct
```

允许国内外任意**公网** HTTP/HTTPS URL。默认拒绝 localhost、私网、link-local、云 metadata 等非公网目标及重定向；只有用户明确需要内网资源时才加 `--allow-private-network`。若目标需要 JavaScript 渲染、登录或复杂浏览器状态，裸 HTTP fetch 可能不足，应优先使用宿主浏览器/网页读取能力。

`fetch.py` 默认 `Accept-Language: zh-CN,...`；`search.py` 会根据 `cjk / latin / mixed` 自动生成语言头。地理市场与语言独立，默认不强制 `gl=cn/us`、`mkt=zh-CN/en-US`；需要指定国家市场时使用 `--market us`、`--market cn` 等。

## 5. 语义质量门槛

每个入口都会记录：

```text
raw_item_count
valid_item_count
relevant_item_count
site_match_count
excluded_item_count
summary_coverage
direct_url_coverage
opaque_redirect_count
noise_rate
semantic_status
semantic_usable
```

常见状态：

```text
useful
degraded-summary
degraded-links
intent-drift
site-constraint-unverified
too-noisy
low-relevance
parse-empty
```

只有 `semantic_usable=true` 才进入 `useful_engines`。

## 6. 链接、摘要与去重

结果区分：

```text
url
redirect_url
url_state
publisher_domain
display_domain
summary
summary_state
```

`opaque-redirect` 不是原始 URL。只对 shortlist 按需调用：

```text
python scripts/resolve_link.py "<redirect-url>" --referer "<search-url>"
```

同一文章被多个搜索引擎找到，仍然只算一个发布来源；跨入口合并时优先保留更好的真实 URL 和摘要。

## 7. 输出

```text
temp_search/runs/<run-id>/
├── raw/
├── *.meta.json
├── *.results.json
├── search-run.json
├── results.json
├── results-preview.md
└── evidence.json
```

不要直接 `cat/type results.json`。优先查看 `results-preview.md`，或：

```text
Windows: python scripts/show.py <run-id> --top 5
Linux:   python3 scripts/show.py <run-id> --top 5
```

## 8. 参数、TLS / CA

`search.py` 常用参数：

```text
--profile auto|all|cn|global   路由（默认 auto，按查询语言排序）
--engines a,b,c                显式引擎，覆盖 profile
--timeout 20                   单次抓取超时（秒）
--probe-timeout 6              某 routing scope 已有网络失败后的快速探测超时（秒）
--network-fail-skip 0          默认不硬跳过；设置正数才启用激进 scope 跳过
--market none                  可选地理市场提示，如 us/cn/jp；默认语言不绑定地理市场
--interval 4 --jitter 1        引擎间隔与随机抖动（秒）
--stop-after-families N        N 个可用引擎族后早停（默认 0，不早停）
--ca-bundle <path>             显式 CA 文件
--allow-insecure-fallback      仅证书校验不可用时一次性降级（留痕）
```

Linux/容器缺 CA 时优先指定可信 CA：

```bash
python3 scripts/search.py "query" --ca-bundle /path/to/ca.pem
```

也可设置 `MSE_CA_BUNDLE`。

只有明确接受风险时：

```bash
python3 scripts/search.py "query" --allow-insecure-fallback
```

不会默认静默关闭证书校验。

## 9. Reference 路由

按需读取：

- 国内外入口、profile 与全网路由：`references/search-sources.md`
- 关键词消歧、site 约束、语言与高级操作符：`references/query-strategy.md`
- 验证码、限流、DNS/SSL/TLS、语义质量与失败降级：`references/reliability-and-fallback.md`
- 来源等级、结论等级与 evidence schema：`references/evidence-schema.md`
- Windows 运行、编码、TLS 与诊断：`references/windows-execution.md`
- Linux/Python 3.7/容器 CA/TLS：`references/linux-execution.md`

## 10. 自检

`scripts/self_check.py` 是**可选诊断工具**。它不是每次检索前的必跑步骤。

```text
Windows: python scripts/self_check.py
Linux:   python3 scripts/self_check.py
```

它检查包结构、Python 3.7 兼容性、CA 环境、引擎/解析器一致性、跨区域自动路由、语言/市场分离、URL 安全与 site 语义回归；它不要求搜索引擎实际联网成功。
