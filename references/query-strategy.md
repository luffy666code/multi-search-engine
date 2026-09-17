# 查询策略、语言路由、site 约束与语义漂移

## 1. 查询语言只影响顺序，不限制范围

`--profile auto` 会粗略识别 CJK/Latin：

- 中文查询先尝试国内搜索入口；
- 英文查询先尝试国际搜索入口；
- 两类入口仍然都允许。

这不是地理限制，也不代表中文问题只能搜中文网站。

## 2. site 约束必须反向验证

例如：

```text
site:arxiv.org embodied intelligence
site:leadleo.com 具身智能 报告
```

搜索引擎可能忽略、降级或改写 `site:`。因此：

1. 垂直入口（搜狗微信、今日头条）不参与任意域名 site 检索；
2. 通用国内/国际搜索入口都可以尝试；
3. 至少一个结果必须能确认 `publisher_domain` / `display_domain` 属于目标域名；
4. 有结果但目标域名 0 命中：`intent-drift`；
5. 只有中间跳转、无法判断真实域名：`site-constraint-unverified`；
6. 所有兼容入口都未满足时：整轮 `semantic_ok=false`。

## 3. 关键词消歧

高歧义简称不要单独搜索。例如：

```text
不推荐：字节 AI 战略
推荐："字节跳动" AI 战略
推荐："字节跳动" 豆包 商业化
```

英文同样适用。例如 Apple 可能是公司也可能是水果，应结合产品、公司、事件等限定词。

## 4. 高级操作符不是事实

`site:`、引号、减号、`filetype:` 等都只是发给搜索引擎的请求。是否真正生效，要看结果反向验证，不能只看请求 URL。

## 5. 词面相关性只是质量门槛

当前 Skill 使用保守词面匹配，不假装自己是向量语义搜索：

```text
matched
partial
mismatch
unknown
site-only
site-unverified
```

`mismatch` 不进入全局候选集。
