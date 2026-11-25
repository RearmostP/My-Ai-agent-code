"""Git workflow isolated from file editing and network transport."""

from __future__ import annotations

from pathlib import Path

from git import Repo, GitCommandError, InvalidGitRepositoryError, NoSuchPathError


class GitManager:
    def repository(self, path: Path) -> Repo | None:
        try:
            return Repo(path, search_parent_directories=True)
        except (InvalidGitRepositoryError, NoSuchPathError):
            return None

    def status(self, path: Path) -> dict[str, str | bool | None]:
        repo = self.repository(path)
        if repo is None:
            return {"is_git": False, "branch": None, "dirty": False, "remote": False}
        try:
            return {
                "is_git": True,
                "branch": repo.active_branch.name if not repo.head.is_detached else "HEAD (detached)",
                "dirty": repo.is_dirty(untracked_files=True),
                "remote": "origin" in [remote.name for remote in repo.remotes],
            }
        finally:
            repo.close()

    def checkout(self, repo: Repo, branch_name: str) -> None:
        if repo.is_dirty(untracked_files=True):
            raise RuntimeError(
                "המאגר מכיל שינויים מקומיים. שמרו או העבירו אותם לפני החלפת ענף."
            )
        if branch_name in repo.heads:
            repo.git.checkout(branch_name)
            return
        if "origin" in [remote.name for remote in repo.remotes]:
            remote_ref = f"origin/{branch_name}"
            try:
                repo.remotes.origin.fetch(branch_name)
                repo.git.show_ref("--verify", f"refs/remotes/{remote_ref}")
                repo.git.checkout("-b", branch_name, "--track", remote_ref)
                return
            except GitCommandError:
                pass
        repo.git.checkout("-b", branch_name)

    def commit_and_push(
        self, repo: Repo, paths: list[str], message: str, branch_name: str
    ) -> None:
        if not paths:
            return
        repo.index.add(paths)
        if repo.is_dirty(index=True, working_tree=False):
            safe_message = " ".join(message.split())[:180] or "Update files with AI agent"
            repo.index.commit(safe_message)
        if "origin" in [remote.name for remote in repo.remotes]:
            repo.git.push("-u", "origin", branch_name)
