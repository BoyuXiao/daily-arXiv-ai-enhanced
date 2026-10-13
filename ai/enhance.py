import os
import json
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Protocol
import requests
from pathlib import Path
import dotenv
import argparse
from tqdm import tqdm

from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from structure import Structure
from content_filter import is_sensitive
from runtime import build_chat_openai_kwargs, raise_if_processing_failed

if os.path.exists('.env'):
    dotenv.load_dotenv()
template = Path(__file__).with_name("template.txt").read_text(encoding="utf-8")
system = Path(__file__).with_name("system.txt").read_text(encoding="utf-8")


class PaperAnalyzer(Protocol):
    def invoke(self, values: Dict[str, str]) -> Structure:
        ...


class ResponsesAPIAnalyzer:
    """Minimal OpenAI Responses API client for Responses-only relays."""

    def __init__(self, model: str, api_key: str, base_url: str):
        model = model.strip()
        api_key = api_key.strip()
        base_url = base_url.strip()
        if not model:
            raise ValueError("MODEL_NAME is required")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        if not base_url:
            raise ValueError("OPENAI_BASE_URL is required")

        self.model = model
        self.api_key = api_key
        self.endpoint = f"{base_url.rstrip('/')}/responses"
        self.timeout = int(os.environ.get("OPENAI_API_TIMEOUT", "180"))

    @staticmethod
    def _extract_output_text(payload: Dict) -> str:
        # Some compatible relays expose the SDK convenience field directly.
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]

        text_parts = []
        for output_item in payload.get("output", []):
            for content_item in output_item.get("content", []):
                if content_item.get("type") in {"output_text", "text"}:
                    text = content_item.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)

        if not text_parts:
            raise ValueError(
                f"Responses API returned no output text; response id: "
                f"{payload.get('id', 'unknown')}"
            )
        return "\n".join(text_parts)

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        text = text.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        return match.group(1) if match else text

    def invoke(self, values: Dict[str, str]) -> Structure:
        language = values["language"]
        content = values["content"]

        schema = Structure.model_json_schema()
        json_instruction = (
            "\n\nReturn only one valid JSON object with exactly these string fields: "
            "tldr, motivation, method, result, conclusion. Do not use Markdown fences."
        )
        body = {
            "model": self.model,
            "store": False,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system.format(language=language),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": template.format(content=content) + json_instruction,
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "paper_analysis",
                    "strict": True,
                    "schema": schema,
                }
            },
        }

        max_output_tokens = os.environ.get("OPENAI_MAX_OUTPUT_TOKENS")
        if max_output_tokens:
            body["max_output_tokens"] = int(max_output_tokens)

        reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT")
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}

        request_args = {
            "headers": {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            "timeout": self.timeout,
        }
        response = requests.post(self.endpoint, json=body, **request_args)

        # Some Responses-compatible relays support /responses but not
        # Structured Outputs. Retry once using the explicit JSON instruction.
        if response.status_code in {400, 404, 422}:
            fallback_body = dict(body)
            fallback_body.pop("text", None)
            response = requests.post(
                self.endpoint,
                json=fallback_body,
                **request_args,
            )

        if not response.ok:
            raise RuntimeError(
                f"Responses API request failed ({response.status_code}): "
                f"{response.text[:1000]}"
            )

        output_text = self._extract_output_text(response.json())
        return Structure.model_validate_json(self._strip_json_fence(output_text))


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="jsonline data file")
    parser.add_argument("--max_workers", type=int, default=1, help="Maximum number of parallel workers")
    return parser.parse_args()

def process_single_item(chain: PaperAnalyzer, item: Dict, language: str) -> Dict:
    def check_github_code(content: str) -> Dict:
        """提取并验证 GitHub 链接"""
        code_info = {}

        # 1. 优先匹配 github.com/owner/repo 格式
        github_pattern = r"https?://github\.com/([a-zA-Z0-9-_]+)/([a-zA-Z0-9-_\.]+)"
        match = re.search(github_pattern, content)
        
        if match:
            owner, repo = match.groups()
            # 清理 repo 名称，去掉可能的 .git 后缀或末尾的标点
            repo = repo.rstrip(".git").rstrip(".,)")
            
            full_url = f"https://github.com/{owner}/{repo}"
            code_info["code_url"] = full_url
            
            # 尝试调用 GitHub API 获取信息
            github_token = os.environ.get("TOKEN_GITHUB")
            headers = {"Accept": "application/vnd.github.v3+json"}
            if github_token:
                headers["Authorization"] = f"token {github_token}"
            
            try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                resp = requests.get(api_url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    code_info["code_stars"] = data.get("stargazers_count", 0)
                    code_info["code_last_update"] = data.get("pushed_at", "")[:10]
            except Exception:
                # API 调用失败不影响主流程
                pass
            return code_info

        # 2. 如果没有 github.com，尝试匹配 github.io
        github_io_pattern = r"https?://[a-zA-Z0-9-_]+\.github\.io(?:/[a-zA-Z0-9-_\.]+)*"
        match_io = re.search(github_io_pattern, content)
        
        if match_io:
            url = match_io.group(0)
            # 清理末尾标点
            url = url.rstrip(".,)")
            code_info["code_url"] = url
            # github.io 不进行 star 和 update 判断
                
        return code_info

    # 检查 summary 字段
    if is_sensitive(item.get("summary", "")):
        return None

    # 检测代码可用性
    code_info = check_github_code(item.get("summary", ""))
    if code_info:
        item.update(code_info)

    try:
        response: Structure = chain.invoke({
            "language": language,
            "content": item['summary']
        })
        item['AI'] = response.model_dump()
        if not all(isinstance(value, str) and value.strip() for value in item['AI'].values()):
            raise ValueError("AI response contains empty fields")
    except Exception as e:
        print(f"Unexpected error for {item.get('id', 'unknown')}: {e}", file=sys.stderr)
        raise RuntimeError(f"AI request failed for {item.get('id', 'unknown')}") from e

    # 检查 AI 生成的所有字段
    for v in item.get("AI", {}).values():
        if is_sensitive(str(v)):
            return None
    return item

def process_all_items(data: List[Dict], model_name: str, language: str, max_workers: int) -> List[Dict]:
    """并行处理所有数据项"""
    configured_api_mode = (os.environ.get("OPENAI_API_MODE") or "auto").strip().lower()
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

    if configured_api_mode == "auto":
        # This Codex relay is Responses-only. Other existing providers keep the
        # repository's original Chat Completions behavior.
        api_mode = (
            "responses"
            if "codexapi.pkudair.site" in base_url.lower()
            else "chat_completions"
        )
    else:
        api_mode = configured_api_mode

    if api_mode == "responses":
        chain = ResponsesAPIAnalyzer(
            model=model_name,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=base_url,
        )
    elif api_mode in {"chat", "chat_completions"}:
        llm = ChatOpenAI(**build_chat_openai_kwargs(
            model_name=model_name,
            base_url=base_url,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
        )).with_structured_output(
            Structure,
            method="function_calling",
        )
        prompt_template = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(system),
            HumanMessagePromptTemplate.from_template(template=template)
        ])
        chain = prompt_template | llm
    else:
        raise ValueError(
            "Unsupported OPENAI_API_MODE. Use 'chat_completions' or 'responses'."
        )

    print(f"Connect to: {model_name} via {api_mode}", file=sys.stderr)
    
    # 使用线程池并行处理
    processed_data = [None] * len(data)  # 预分配结果列表
    processing_errors = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_idx = {
            executor.submit(process_single_item, chain, item, language): idx
            for idx, item in enumerate(data)
        }
        
        # 使用tqdm显示进度
        for future in tqdm(
            as_completed(future_to_idx),
            total=len(data),
            desc="Processing items"
        ):
            idx = future_to_idx[future]
            try:
                result = future.result()
                processed_data[idx] = result
            except Exception as e:
                print(f"Item at index {idx} generated an exception: {e}", file=sys.stderr)
                processing_errors.append(str(e))

    raise_if_processing_failed(processing_errors)
    
    return processed_data

def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", "deepseek-chat")
    language = os.environ.get("LANGUAGE", 'Chinese')

    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')

    # 读取数据
    data = []
    with open(args.data, "r") as f:
        for line in f:
            data.append(json.loads(line))

    # 去重
    seen_ids = set()
    unique_data = []
    for item in data:
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_data.append(item)

    data = unique_data
    print('Open:', args.data, file=sys.stderr)
    
    # 并行处理所有数据
    processed_data = process_all_items(
        data,
        model_name,
        language,
        args.max_workers
    )
    
    retained_data = [item for item in processed_data if item is not None]
    if not retained_data:
        raise RuntimeError("No enhanced papers were produced; refusing to publish an empty file")

    # Preserve an existing successful output if processing or writing fails.
    temporary_file = target_file + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as f:
        for item in retained_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    os.replace(temporary_file, target_file)
    print(f"Enhanced {len(retained_data)} / {len(data)} papers", file=sys.stderr)

if __name__ == "__main__":
    main()
