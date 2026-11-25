import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from git import Repo

import main_app


class FakeLLM:
    def __init__(self, base_url, model):
        pass

    def stream_completion(self, messages):
        response = {
            "summary": "עודכן הקובץ",
            "reasoning": "השינוי תואם לבקשה.",
            "changes": [{"path": "app.py", "content": "value = 2\n"}],
        }
        yield "content", json.dumps(response, ensure_ascii=False)


class ChatRouteTests(unittest.TestCase):
    @staticmethod
    def initialize_repo(root, remote_path=None):
        repo = Repo.init(root, initial_branch="main")
        with repo.config_writer() as writer:
            writer.set_value("user", "name", "Test Agent")
            writer.set_value("user", "email", "agent@example.invalid")
        repo.index.add(["app.py"])
        repo.index.commit("initial")
        if remote_path:
            repo.create_remote("origin", str(remote_path))
            repo.git.push("-u", "origin", "main")
        return repo

    def test_home_and_health_are_available(self):
        client = TestClient(main_app.app)
        self.assertIn('dir="rtl"', client.get("/").text)
        self.assertEqual(client.get("/api/health").json(), {"status": "ok"})

    def test_chat_edits_non_git_project_and_streams_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            with patch("main_app.LLMClient", FakeLLM):
                response = TestClient(main_app.app).post(
                    "/api/chat",
                    json={"path": str(root), "prompt": "עדכן את הערך", "mode": "ai"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertIn("event: complete", response.text)
            self.assertIn("-value = 1", response.text)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "value = 2\n")

    def test_main_branch_changes_wait_for_explicit_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            repo = self.initialize_repo(root)
            with patch("main_app.LLMClient", FakeLLM):
                client = TestClient(main_app.app)
                response = client.post(
                    "/api/chat",
                    json={"path": str(root), "prompt": "עדכן את הערך", "mode": "main"},
                )
                approval = next(
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                    and '"approval_id"' in line
                )
                self.assertEqual(repo.head.commit.message.strip(), "initial")
                self.assertTrue(repo.is_dirty(untracked_files=True))
                (root / "app.py").write_text("external edit\n", encoding="utf-8")
                stale_approval = client.post(
                    "/api/approve", json={"approval_id": approval["approval_id"]}
                )
                self.assertEqual(stale_approval.status_code, 409)
                (root / "app.py").write_text("value = 2\n", encoding="utf-8")
                result = client.post(
                    "/api/approve", json={"approval_id": approval["approval_id"]}
                )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(repo.head.commit.message.strip(), "עודכן הקובץ")
            self.assertFalse(repo.is_dirty(untracked_files=True))
            repo.close()

    def test_ai_branch_commits_and_pushes_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            remote_path = base / "remote.git"
            root.mkdir()
            remote = Repo.init(remote_path, bare=True)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            repo = self.initialize_repo(root, remote_path)
            try:
                with patch("main_app.LLMClient", FakeLLM):
                    response = TestClient(main_app.app).post(
                        "/api/chat",
                        json={"path": str(root), "prompt": "עדכן את הערך", "mode": "ai"},
                    )
                self.assertIn("event: complete", response.text)
                self.assertEqual(repo.active_branch.name, "ai-agent")
                self.assertEqual(repo.head.commit.message.strip(), "עודכן הקובץ")
                self.assertIn("ai-agent", [ref.name for ref in remote.refs])
                self.assertFalse(repo.is_dirty(untracked_files=True))
            finally:
                repo.close()
                remote.close()


if __name__ == "__main__":
    unittest.main()
