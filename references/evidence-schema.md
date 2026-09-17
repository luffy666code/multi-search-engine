# 证据与结论 Schema

## 1. 来源等级

- A：原始/官方来源
- B：可靠二手媒体原文
- C：搜索摘要、未打开原文、聚合或待核验线索

搜索候选一律从 C 开始。

## 2. 验证状态

```text
search-snippet
opened
content-verified
conflicted
unavailable
```

## 3. 搜索候选新增字段

```json
{
  "title": "...",
  "url": "当前最佳 URL",
  "redirect_url": "搜索引擎中间跳转，可为空",
  "url_state": "direct|extracted-direct|opaque-redirect|resolved-direct",
  "publisher_domain": "已确认原始域名，可为空",
  "display_domain": "结果块显示域名提示，可为空",
  "summary": "...",
  "summary_state": "extracted|fallback-visible-text|missing",
  "intent_status": "matched|partial|mismatch|unknown|site-only|site-unverified",
  "site_target_match": true,
  "source_grade": "C",
  "verification_state": "search-snippet"
}
```

`opaque-redirect` 不能据此判断真实发布方或升级 A/B 级。

## 4. claim_level

```text
confirmed
multi-source-consistent
reasonable-inference
unverifiable
```

两个搜索入口发现同一篇文章仍然只有一个独立发布来源。

## 5. run / attempt 网络诊断字段

`search-run.json` 与 stdout 记录网络与区域状态，用于事后追溯"为什么某引擎没有结果"：

```text
network_failures_by_scope   {"cn": n, "global": m}，各区域纯网络失败次数
language_hint               cjk | latin | mixed
attempt.scope                cn | global
attempt.fast_probe           true 表示该引擎因同区域已有失败而使用短超时探测
attempt.network_unreachable true 表示该引擎为纯网络失败（超时/DNS/重置/TLS EOF）
attempt.host_redirected      true 表示请求主机与最终主机不同
attempt.redirect_from_host   原请求主机
attempt.redirect_to_host     最终主机
attempt.geo_localized        true 仅表示已知地理区域化跳转
skipped_engines[].reason     regional-network-unreachable:<scope>:<n>（仅显式启用 aggressive skip 时）
                             same-domain-blocked:<domain> | site-constraint-unsupported
```

`geo_localized=true` 的结果应被视为区域化召回，不能仅据此宣称覆盖了国际来源。`geo_redirect` 仅保留为兼容旧消费者的别名。

## 6. 三层状态与质量排序

`search-run.json` 的每个 attempt 同时提供：

```json
{
  "transport": {"ok": true, "http_status": 200},
  "parse": {"ok": true, "item_count": 8},
  "semantic": {"ok": true, "status": "useful", "relevant_item_count": 5}
}
```

最终 `results.json.status` 也汇总 transport / parse / semantic 三层。

`quality_score` 只用于**候选结果排序**，依据意图匹配、是否真实 URL、摘要完整度、site 命中和多入口发现等透明信号；它不是网站可信度、事实真实性或政治立场评分。
