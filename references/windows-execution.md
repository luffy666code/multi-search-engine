# Windows 执行速查

## 可选运行诊断

```cmd
python scripts/self_check.py
```

正常检索：

```cmd
python scripts/search.py "查询词"
```

虽然 Windows 接受反斜杠，但 Skill 文档统一使用 `/`，这样同一条相对路径可以直接复制到 Windows 与 Linux，避免 POSIX shell 把 `\` 当转义字符。

所有脚本使用 `Path(__file__)` 锚定 Skill 根目录；不要先 `cd temp_search`。

最低 Python 版本：**3.7**。

## stdout 原则

结构化文件使用 UTF-8；stdout 只返回短小 ASCII JSON（状态、计数、文件路径），避免 GBK/UTF-8 破坏结构化输出，也避免完整中文 JSON 多层 `\uXXXX` 转义造成巨大 Token 消耗。

不要：

```cmd
type temp_search\runs\...\results.json
```

优先读 `results-preview.md`；shell-only 环境需要少量结构化结果时：

```cmd
python scripts/show.py <run-id> --top 5
```

## TLS / CA

默认严格校验证书。自定义企业/容器 CA：

```cmd
python scripts/search.py "查询词" --ca-bundle C:\certs\company-ca.pem
```

也可设置环境变量 `MSE_CA_BUNDLE`。

只有在明确接受风险时才使用：

```cmd
python scripts/search.py "查询词" --allow-insecure-fallback
python scripts/search.py "查询词" --insecure
```

`--allow-insecure-fallback` 只会在证书校验失败后降级一次，并记录在运行清单中；不会静默关闭 TLS 校验。

## 低层诊断

```cmd
python scripts/fetch.py "<url>" --engine direct --name debug-01 --out-dir temp_search/debug
python scripts/parse.py baidu temp_search/debug/raw/debug-01.html --name debug-01 --out-dir temp_search/debug --query "查询词"
```

复杂逻辑不要写 `python -c`，不要把 curl / PowerShell / Python 混成一条长命令。
