# 全网检索来源与入口能力

## 1. 国内与国际入口

| 入口 | 范围 | 类型 | 任意 `site:` | 常见风险 |
|---|---|---|---:|---|
| 百度 | 国内 | 通用网页 | 是，必须结果验证 | 安全验证、商业结果、opaque redirect |
| 搜狗微信 | 国内 | 公众号垂直 | 否 | `/antispider/`、验证码、临时签名链 |
| 今日头条 | 国内 | 资讯垂直 | 否 | SSR 数据变化、页面体积大 |
| 360 | 国内 | 通用网页 | 是，必须结果验证 | SSL/访问验证 |
| 搜狗网页 | 国内 | 通用网页 | 是，必须结果验证 | `/antispider/`、验证码 |
| Bing | 国际 | 通用网页 | 是，必须结果验证 | 地区重定向、结果降级、挑战页、无登录态下中文词组查询可能被拆成单字字典页 |
| DuckDuckGo HTML | 国际 | 通用网页 | 是，必须结果验证 | 网络不可达、bot challenge、跳转链 |
| Google | 国际 | 通用网页 | 是，必须结果验证 | 网络限制、`/sorry/`、HTML 结构频繁变化 |
| Brave Search | 国际 | 通用网页 | 是，必须结果验证 | 网络限制、挑战页、页面结构变化 |

“支持”只表示 Skill 有 URL 模板和解析策略，不表示当前网络环境一定能访问。

## 2. profile

### `auto`（默认）

同时允许国内与国际入口，只改变顺序：

- 中文/CJK 查询：国内入口优先，然后国际入口补充；
- 英文/Latin 查询：国际入口优先，然后国内入口补充。

### `cn`

只使用：百度、搜狗微信、今日头条、360、搜狗网页。

### `global`

只使用：Bing、DuckDuckGo、Google、Brave。

### `all`

固定混合顺序尝试国内与国际入口。

### 语言与地理市场（分离）

查询语言只决定入口顺序、界面语言提示和 `Accept-Language`：

- `cjk`：中文优先；
- `latin`：英文优先；
- `mixed`：国内/国际入口交错，中文与英文权重接近。

默认 `--market none`，**不会**因为中文自动加 `gl=cn`，也不会因为英文自动加 `gl=us`。只有用户明确需要某一国家/地区市场时才传：

```text
--market cn
--market us
--market jp
```

Bing / Google / DuckDuckGo / Brave 会在各自支持范围内把显式 market 转换为请求参数。

### 主机重定向与区域化

`host_redirected=true` 只表示请求主机与最终主机不同。只有已知的地理区域化跳转（例如 `www.bing.com → cn.bing.com`，或 Google 落到区域域名）才同时记录 `geo_localized=true`。任意 CDN、规范域名或普通跨主机跳转不再误标为地理区域化。

### 网络快速探测

`cn / global` 是**路由 scope**，不是严格网络故障域。某 scope 出现一次纯网络失败后，后续同 scope 引擎使用较短的 `--probe-timeout`，但默认 `--network-fail-skip 0`，因此每个入口仍至少探测一次。只有用户显式设置正数时，才会在达到阈值后跳过该 scope 的剩余入口。

### Bing 中文查询单字字典页（运行时状态）

无登录态对 `cn.bing.com` 发送形如 `中文词组1 中文词组2` 的查询时，Bing 服务端可能返回首字的字典/字源卡片（例如"具身智能 产业报告"返回"具"字字典页），与 mkt / setlang / Accept-Language 无关，且随时间波动（同一查询连续请求更容易触发，间隔后可恢复）。Skill 不尝试修复 Bing 行为：语义质量门槛会把这类结果判定为 `low-relevance` 并拒绝进入 merged；多源架构下由其他入口补位。若确实依赖 Bing 中文结果，可改用 `--engines bing,baidu,sogou` 组合或稍后重试。

## 3. 搜索入口不是证据来源

```text
Bing  → Reuters article A
Google → Reuters article A
```

仍然只有一个发布来源 `reuters.com`。

最终多源交叉验证按真实 `publisher_domain` 计算。

## 4. 已知原文优先直达

如果已经知道公司官网、论文、政府页、媒体原文或具体 URL，应优先打开原文，不要为了“多引擎”而继续搜索。

全网模式允许任意可访问 HTTP/HTTPS 域名；不存在“中国区域名白名单”。
