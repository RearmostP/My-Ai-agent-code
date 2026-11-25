"""Application configuration and persistent, user-editable agent rules."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "agent_rules.json"

DEFAULT_RULES: dict[str, Any] = {
    "ai_branch_name": "ai-agent",
    "main_branch_name": "main",
    "system_rules": [
        "Make only changes requested by the user. Preserve unrelated behavior.",
        "Never modify secrets, credentials, generated files, or ignored paths.",
        "Return a JSON object with summary, reasoning, and changes. Each change "
        "must contain a project-relative path and the complete new file content.",
        "Do not include markdown fences or instructions to run shell commands.",
    ],
    "external_rules_file_path": "",
    "ignored_paths": [
        ".venv", "venv", ".git", "__pycache__", ".env", "key1.env",
        "key.env", "node_modules", ".idea",
    ],
}


class ConfigManager:
    def __init__(self) -> None:
        load_dotenv(PROJECT_ROOT / ".env")
        self.rules_path = RULES_PATH
        if not self.rules_path.exists():
            self.save_rules(DEFAULT_RULES)

    def get_settings(self) -> dict[str, Any]:
        return {
            "api_key_configured": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
            "api_base_url": os.getenv("API_BASE_URL", "https://openrouter.ai"),
            "model_name": os.getenv(
                "MODEL_NAME", "nvidia/nemotron-3-ultra-550b-a55b:free"
            ),
            "rules": self.get_rules(),
        }

    def get_rules(self) -> dict[str, Any]:
        try:
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read agent rules: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("agent_rules.json must contain a JSON object.")
        return {**DEFAULT_RULES, **data}

    def save_rules(self, rules: dict[str, Any]) -> None:
        if not isinstance(rules, dict):
            raise ValueError("Rules must be a JSON object.")
        merged = {**DEFAULT_RULES, **rules}
        for field in ("ai_branch_name", "main_branch_name"):
            value = merged.get(field)
            if not isinstance(value, str) or not value.strip() or value.startswith("-"):
                raise ValueError(f"{field} must be a non-empty branch name.")
            if any(char in value for char in ("\n", "\r", "\0")):
                raise ValueError(f"{field} contains an invalid character.")
        if not isinstance(merged["system_rules"], (str, list)):
            raise ValueError("system_rules must be text or a list of strings.")
        if isinstance(merged["system_rules"], list) and not all(
            isinstance(item, str) for item in merged["system_rules"]
        ):
            raise ValueError("Every system rule must be text.")
        if not isinstance(merged["external_rules_file_path"], str):
            raise ValueError("external_rules_file_path must be text.")
        if not isinstance(merged["ignored_paths"], list) or not all(
            isinstance(item, str) and item.strip() for item in merged["ignored_paths"]
        ):
            raise ValueError("ignored_paths must be a list of non-empty strings.")

        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.rules_path.parent,
                delete=False, suffix=".tmp",
            ) as handle:
                temp_name = handle.name
                json.dump(merged, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.rules_path)
        except OSError as exc:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
            raise RuntimeError(f"Unable to save agent rules: {exc}") from exc


config = ConfigManager()
