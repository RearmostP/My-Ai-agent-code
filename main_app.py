"""FastAPI routes and orchestration for the coding agent."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.config_manager import PROJECT_ROOT, config
from core.file_manager import FileManager, load_external_rules
from core.git_manager import GitManager
from core.llm_client import LLMClient, build_messages, parse_response

app = FastAPI(title="סוכן קוד מקומי", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "web"), name="static")
git_manager = GitManager()
project_locks: dict[str, threading.Lock] = {}
project_locks_guard = threading.Lock()
pending_approvals: dict[str, dict[str, Any]] = {}


class ChatRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    prompt: str = Field(min_length=1, max_length=20_000)
    mode: str = Field(pattern="^(ai|main)$")


class ApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=1)


class RulesRequest(BaseModel):
    ai_branch_name: str
    main_branch_name: str
    system_rules: str | list[str]
    external_rules_file_path: str = ""
    ignored_paths: list[str]


def _event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _lock_for(path: Path) -> threading.Lock:
    repo = git_manager.repository(path)
    if repo:
        try:
            key = str(Path(repo.working_tree_dir).resolve())
        finally:
            repo.close()
    else:
        key = str(path.resolve())
    with project_locks_guard:
        return project_locks.setdefault(key, threading.Lock())


@app.get("/")
def home() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return config.get_settings()


@app.put("/api/settings")
def save_settings(payload: RulesRequest) -> dict[str, str]:
    try:
        config.save_rules(payload.model_dump())
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "ההגדרות נשמרו."}


@app.get("/api/git-status")
def get_git_status(path: str) -> dict[str, str | bool | None]:
    try:
        target = Path(path).expanduser().resolve(strict=True)
        if target.is_file():
            target = target.parent
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"נתיב לא תקין: {exc}") from exc
    return git_manager.status(target)


@app.get("/api/changes/{approval_id}")
def get_changes(approval_id: str) -> dict[str, Any]:
    pending = pending_approvals.get(approval_id)
    if not pending:
        raise HTTPException(status_code=404, detail="השינויים לא נמצאו או כבר טופלו.")
    return {"diff": pending["diff"], "files": pending["paths"]}


@app.post("/api/approve")
def approve(payload: ApprovalRequest) -> dict[str, str]:
    pending = pending_approvals.get(payload.approval_id)
    if not pending:
        raise HTTPException(status_code=404, detail="בקשת האישור אינה זמינה.")
    repository_path = Path(pending["repo_path"])
    with _lock_for(repository_path):
        repo = git_manager.repository(repository_path)
        if repo is None:
            raise HTTPException(status_code=409, detail="מאגר Git כבר אינו זמין.")
        try:
            repository_root = Path(repo.working_tree_dir).resolve()
            if repo.head.is_detached or repo.active_branch.name != pending["branch"]:
                raise HTTPException(
                    status_code=409, detail="הענף השתנה מאז יצירת בקשת האישור."
                )
            for relative, expected_hash in pending["expected_files"].items():
                try:
                    current_hash = hashlib.sha256(
                        (repository_root / relative).read_bytes()
                    ).hexdigest()
                except OSError as exc:
                    raise HTTPException(
                        status_code=409, detail=f"לא ניתן לאמת את {relative}: {exc}"
                    ) from exc
                if current_hash != expected_hash:
                    raise HTTPException(
                        status_code=409,
                        detail=f"הקובץ {relative} השתנה מאז יצירת בקשת האישור.",
                    )
            git_manager.commit_and_push(
                repo, pending["paths"], pending["summary"], pending["branch"]
            )
            pending_approvals.pop(payload.approval_id, None)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Commit/Push נכשל: {exc}") from exc
        finally:
            repo.close()
    return {"message": "השינויים אושרו ונשלחו."}


@app.post("/api/cancel-approval")
def cancel_approval(payload: ApprovalRequest) -> dict[str, str]:
    pending = pending_approvals.pop(payload.approval_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="בקשת האישור אינה זמינה.")
    return {"message": "השינויים נשארו מקומית ללא Commit או Push."}


@app.post("/api/chat")
def chat(payload: ChatRequest) -> StreamingResponse:
    def generate():
        lock: threading.Lock | None = None
        repo = None
        try:
            rules = config.get_rules()
            file_manager = FileManager(rules["ignored_paths"])
            root, target, is_file = file_manager.resolve_target(payload.path)
            repo = git_manager.repository(root)
            lock = _lock_for(Path(repo.working_tree_dir)) if repo else _lock_for(root)
            lock.acquire()
            branch: str | None = None
            if repo:
                branch = rules["ai_branch_name"] if payload.mode == "ai" else rules[
                    "main_branch_name"
                ]
                git_manager.checkout(repo, branch)
                if is_file:
                    target = target.resolve(strict=False)
            external_rules = load_external_rules(rules["external_rules_file_path"])
            files = file_manager.gather_context(root, target, is_file)
            client = LLMClient(
                config.get_settings()["api_base_url"],
                config.get_settings()["model_name"],
            )
            messages = build_messages(payload.prompt.strip(), rules, external_rules, files)
            raw_parts: list[str] = []
            for kind, text in client.stream_completion(messages):
                if kind == "reasoning":
                    yield _event("reasoning", {"text": text})
                else:
                    raw_parts.append(text)
            result = parse_response("".join(raw_parts))
            changed, diff = file_manager.apply_changes(root, files, result["changes"])
            git_paths = (
                [
                    (root / relative).resolve().relative_to(
                        Path(repo.working_tree_dir).resolve()
                    ).as_posix()
                    for relative in changed
                ]
                if repo else changed
            )
            if changed and repo and payload.mode == "ai":
                git_manager.commit_and_push(repo, git_paths, result["summary"], branch or "")
            elif changed and repo and payload.mode == "main":
                approval_id = uuid.uuid4().hex
                pending_approvals[approval_id] = {
                    "repo_path": repo.working_tree_dir,
                    "paths": git_paths,
                    "expected_files": {
                        relative: hashlib.sha256(
                            (Path(repo.working_tree_dir) / relative).read_bytes()
                        ).hexdigest()
                        for relative in git_paths
                    },
                    "diff": diff,
                    "summary": result["summary"],
                    "branch": branch,
                }
                yield _event("approval", {
                    "approval_id": approval_id,
                    "message": (
                        f"השינויים בוצעו בענף הראשי ({branch}). "
                        "האם לאשר ביצוע Commit ו-Push?"
                    ),
                    "summary": result["summary"],
                    "files": changed,
                })
            yield _event("complete", {
                "summary": result["summary"],
                "files": changed,
                "diff": diff,
                "reasoning": result.get("reasoning", ""),
                "git": git_manager.status(root),
            })
        except Exception as exc:
            yield _event("error", {"message": str(exc)})
        finally:
            if lock and lock.locked():
                lock.release()
            if repo:
                repo.close()

    return StreamingResponse(generate(), media_type="text/event-stream")
