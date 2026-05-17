---
name: daily-arxiv-ai-enhanced
version: 0.2
description: 获取 AI 系统 / MoE 方向的 arXiv 论文 JSON 数据
---

# arXiv 论文数据 API（AI Systems + MoE）

## 触发条件
用户想要获取 daily-arXiv-ai-enhanced 项目中的论文数据，重点关注 AI 系统与 MoE 相关工作。

## 基础仓库 URL
部署后替换为你的 GitHub Pages 地址，例如：
`https://boyuxiao.github.io/daily-arXiv-ai-enhanced/`

## 推荐配置

### GitHub Actions Variables
```
CATEGORIES=cs.LG, cs.DC, cs.AI
LANGUAGE=Chinese
MODEL_NAME=deepseek-chat
```

### 默认关注关键词（见 `js/user-preferences.js`）
MoE、vLLM、FSDP、speculative decoding、KV cache、Triton、quantization、GPU scheduling 等。

## URL 参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `category` | arXiv 类别 | `cs.LG`, `cs.DC`, `cs.AI` |
| `author` | 作者姓名 | `Smith` |
| `keywords` | 关键词，逗号分隔 | `MoE,vLLM,KV cache` |

## 样例

```bash
# 按类别 + MoE 关键词筛选
bash scripts/fetch.sh "https://boyuxiao.github.io/daily-arXiv-ai-enhanced/?category=cs.LG&keywords=MoE,mixture of experts"

# 推理系统相关
bash scripts/fetch.sh "https://boyuxiao.github.io/daily-arXiv-ai-enhanced/?category=cs.DC&keywords=vLLM,LLM serving"

# 分布式训练
bash scripts/fetch.sh "https://boyuxiao.github.io/daily-arXiv-ai-enhanced/?keywords=FSDP,tensor parallelism"
```

> 不能直接 `curl`/`wget` 该 URL，需通过 `fetch.sh`（Puppeteer 执行页面 JS 后返回 JSON）。

## 筛选逻辑

```
category AND (keywords OR author)
```

- **category**：硬筛选，只返回指定类别
- **keywords**：在标题和摘要中搜索（关键词之间为「或」）
- **author**：在作者字段中搜索
- **keywords** 与 **author** 为「或」关系

## JSON 响应结构

```json
{
  "category": "cs.LG",
  "author": null,
  "keywords": ["MoE", "vLLM"],
  "count": 10,
  "papers": [
    {
      "id": "2401.00001",
      "title": "标题",
      "authors": "作者1, 作者2",
      "categories": ["cs.LG"],
      "summary": "tldr",
      "date": "2024-01-01",
      "url": "https://arxiv.org/abs/2401.00001"
    }
  ]
}
```
