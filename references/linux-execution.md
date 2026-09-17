# Linux 执行速查

## 可选运行诊断

需要排查环境时可执行：

```bash
python3 scripts/self_check.py
```

核心运行要求：

```text
Python >= 3.7
路径基准 = Skill 根目录
临时目录 = <skill-root>/temp_search/runs/<run-id>/
结构化文件 = UTF-8
stdout = 短 ASCII JSON
TLS = 默认严格校验证书
```

脚本全部使用 `Path(__file__)` 锚定 Skill 根目录，不依赖调用者当前工作目录。不要先 `cd temp_search`。

## 正常检索

```bash
python3 scripts/search.py "具身智能 报告"
python3 scripts/search.py "site:leadleo.com 具身智能 报告"
```

路径统一使用 `/`。这个写法在 Linux 原生可用，也避免把 Windows `scripts\search.py` 复制到 POSIX shell 后被当成转义字符。

## Python 版本

Skill 的最低运行版本是 **Python 3.7**。核心代码不得使用仅 Python 3.8/3.9+ 提供的语法或运行时特性，例如：

```text
字典合并 d1 | d2
海象运算符 :=
list[str] / dict[str, ...]
str.removeprefix / removesuffix
```

`self_check.py` 会做包内脚本编译、自检和 3.7 目标语法检查。

## Linux/容器 CA 证书

默认行为是严格 TLS 校验。若容器缺少根证书，优先顺序：

1. 安装/挂载系统 CA；
2. 指定 PEM bundle：

```bash
python3 scripts/search.py "查询词" --ca-bundle /etc/ssl/certs/ca-certificates.crt
```

也可以设置：

```bash
export MSE_CA_BUNDLE=/path/to/company-ca.pem
```

如果运行环境安装了 `certifi`，Skill 会在没有显式 CA bundle 时把它作为可选候选；它不是强制依赖。

仅在明确接受证书校验降级风险时使用：

```bash
python3 scripts/search.py "查询词" --allow-insecure-fallback
```

此模式先按严格 TLS 请求；只有错误明确属于 `CERTIFICATE_VERIFY_FAILED` 时才重试一次不校验证书，并在 `search-run.json` 中记录 `tls_fallback_used=true`。

完全关闭校验：

```bash
python3 scripts/search.py "查询词" --insecure
```

`--insecure` 不应作为默认配置。

## 诊断

单独测试抓取：

```bash
python3 scripts/fetch.py "<url>" --engine direct --name debug-01 --out-dir temp_search/debug
```

自定义 CA：

```bash
python3 scripts/fetch.py "<url>" --engine direct --name debug-01 --out-dir temp_search/debug --ca-bundle /path/to/ca.pem
```

查看少量结果：

```bash
python3 scripts/show.py <run-id> --top 5
```

不要直接 `cat` 完整 `results.json` 到 Agent stdout；优先读 `results-preview.md` 或使用 `show.py`。
