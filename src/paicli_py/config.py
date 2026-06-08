from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PaiCliConfig:
    provider: str
    model: str
    api_key: str
    api_url: str

    @staticmethod
    def load(project_dir: Path | None = None) -> "PaiCliConfig | None":
        project_dir = project_dir or Path.cwd()
        _load_dotenv(project_dir / ".env")

        provider = os.getenv("PAICLI_PROVIDER", "").strip().lower()
        candidates = [
            ("glm", "GLM_API_KEY", os.getenv("GLM_MODEL", "glm-5.1"),
             os.getenv("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")),
            ("deepseek", "DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
             os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")),
            ("step", "STEP_API_KEY", os.getenv("STEP_MODEL", "step-3.5-flash"),
             os.getenv("STEP_API_URL", "https://api.stepfun.com/v1/chat/completions")),
            ("kimi", "KIMI_API_KEY", os.getenv("KIMI_MODEL", "kimi-k2.6"),
             os.getenv("KIMI_API_URL", "https://api.moonshot.cn/v1/chat/completions")),
        ]

        for candidate_provider, key_name, model, url in candidates:
            key = os.getenv(key_name, "").strip()
            if key and (not provider or provider == candidate_provider):
                return PaiCliConfig(candidate_provider, model, key, url)
        return None


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def user_config_dir() -> Path:
    return Path.home() / ".paicli-py"


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

