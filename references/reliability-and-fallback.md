# 全网可用性、语义质量与失败降级

## 1. 三层成功

必须区分：

```text
transport success   网络/HTTP 成功
parse success       能抽出结构化候选
semantic success    候选真正满足查询意图
```

只有第三层满足时，入口才进入 `useful_engines`。

## 2. 网络区域差异是运行时状态

Skill 不再假设“只在中国大陆网络”，也不假设 Google / DuckDuckGo / Bing 一定可达。

可能出现：

```text
某国内入口可达，国际入口超时
国际入口可达，国内入口反爬
Bing 被地区重定向
Google 返回 challenge/sorry
DuckDuckGo 连接失败
```

这些都应记录为某个入口的状态，然后继续其他入口，而不是把整个任务直接判死。

### 网络失败快速探测（v1.5.2）

网络层失败（没有任何 HTTP 响应）与反爬/限流是两类问题：

| 现象 | 判定 | 处理 |
|---|---|---|
| timed out / DNS 失败 / connection reset/refused / TLS unexpected EOF | 纯网络失败，`network_unreachable=true` | 同 routing scope 后续入口改用短 `--probe-timeout`；默认仍逐个探测 |
| HTTP 403/429、captcha、antispider | 反爬/限流，`blocked` | 同搜索域后续入口可跳过，不计网络失败 |
| HTTP 200 但低信号/解析为空 | 内容问题 | 语义门槛判失败，继续后续引擎 |
| 主机发生变化 | `host_redirected=true` | 记录 from/to host |
| 已知地理区域化跳转 | `geo_localized=true` | 正常解析，但不能据此宣称全球覆盖 |

`scope=cn/global` 只是路由标签，不应被当作整个区域网络必然同时可达/不可达。默认 `--network-fail-skip 0`，不会因为两个国际引擎失败就直接跳过其他国际引擎。若用户更看重速度，可显式设置正数启用激进 scope 跳过。

## 3. semantic_status

| 状态 | 含义 | useful |
|---|---|---:|
| useful | 相关性、噪音、摘要、链接质量可接受 | 是 |
| degraded-summary | 结果相关，但摘要覆盖低 | 是，需谨慎 |
| degraded-links | 结果相关，但原始 URL 解析率低 | 是，需先开原文 |
| intent-drift | 查询约束被忽略或结果明显漂移 | 否 |
| site-constraint-unverified | 无法确认 site 目标域名 | 否 |
| too-noisy | 导航/广告/法务等噪音过高 | 否 |
| low-relevance | 相关结果不足 | 否 |
| parse-empty | 没有结构化结果 | 否 |

## 4. 验证码、403/429 与反爬

- 403/429：不连续重试，换源；
- 验证码/challenge：本轮停用该入口，不绕过；
- DNS / timeout / SSL EOF：有限重试后换源；
- 同一搜索域触发明确反爬后，本轮可跳过同域后续入口。

## 5. TLS / CA

TLS 默认严格校验：

```text
系统 CA / --ca-bundle / MSE_CA_BUNDLE / 可选 certifi
        ↓
仍发生 CERTIFICATE_VERIFY_FAILED
        ↓
默认明确失败并留痕
        ↓
只有显式 --allow-insecure-fallback 才允许不校验证书重试一次
```

禁止默认静默 insecure。发生降级必须记录：

```text
tls_mode
tls_verified
tls_fallback_used
ca_source
security_warning
```

## 6. URL 安全 / SSRF 防护

公开 Agent Skill 默认拒绝：

```text
localhost / 127.0.0.0/8
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
::1 / fc00::/7 / fe80::/10
以及其他 private / link-local / reserved / multicast / unspecified 地址
```

初始 URL 与每次 HTTP redirect 都会重新检查。只有用户明确希望访问本机或内网服务时才使用 `--allow-private-network`。该开关与 `--insecure` 完全不同：前者控制**网络地址范围**，后者控制 **TLS 证书校验**。

## 7. 任意网站直连

`fetch.py --engine direct` 不限制网站国家/地区。若网站要求 JavaScript、Cookie、登录或浏览器挑战，裸 HTTP fetch 失败不等于网站不存在；此时优先宿主浏览器/网页能力。
