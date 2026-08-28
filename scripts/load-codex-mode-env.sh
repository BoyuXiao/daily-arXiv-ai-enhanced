#!/usr/bin/env bash

# Source this file to reuse an existing Codex mode:
#   source scripts/load-codex-mode-env.sh /path/to/.codex/modes/gpt

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "Please source this script so exported variables remain in your shell:" >&2
    echo "  source $0 /path/to/.codex/modes/gpt" >&2
    exit 1
fi

mode_dir="${1:-${CODEX_MODE_DIR:-}}"
if [[ -z "$mode_dir" ]]; then
    echo "Usage: source scripts/load-codex-mode-env.sh /path/to/.codex/modes/gpt" >&2
    return 1
fi

if [[ ! -f "$mode_dir/auth.json" || ! -f "$mode_dir/config.toml" ]]; then
    echo "Codex mode must contain auth.json and config.toml: $mode_dir" >&2
    return 1
fi

eval "$(
python3 - "$mode_dir" <<'PY'
import json
import shlex
import sys
import tomllib
from pathlib import Path

mode_dir = Path(sys.argv[1])
auth = json.loads((mode_dir / "auth.json").read_text())
with (mode_dir / "config.toml").open("rb") as f:
    config = tomllib.load(f)

provider_name = config["model_provider"]
provider = config["model_providers"][provider_name]

values = {
    "OPENAI_API_KEY": auth["OPENAI_API_KEY"],
    "OPENAI_BASE_URL": provider["base_url"],
    "OPENAI_API_MODE": provider.get("wire_api", "chat_completions"),
    "MODEL_NAME": config["model"],
    "OPENAI_REASONING_EFFORT": config.get("model_reasoning_effort", ""),
}

for key, value in values.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)" || return 1

echo "Loaded Codex mode: $mode_dir"
echo "  OPENAI_BASE_URL=$OPENAI_BASE_URL"
echo "  OPENAI_API_MODE=$OPENAI_API_MODE"
echo "  MODEL_NAME=$MODEL_NAME"
echo "  OPENAI_API_KEY is loaded but not displayed."
