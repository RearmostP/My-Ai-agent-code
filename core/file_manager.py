"""Safe project scanning and atomic application of structured file changes."""

from __future__ import annotations

import difflib
import fnmatch
import os
import tempfile
from pathlib import Path
from typing import Any

MAX_FILES = 150
MAX_FILE_BYTES = 100_000
MAX_CONTEXT_BYTES = 1_000_000
ALWAYS_IGNORED = {".git", ".env", "key.env", "key1.env"}


class FileManager:
    def __init__(self, ignored_paths: list[str]) -> None:
        self.ignored_paths = [item.replace("\\", "/").strip("/") for item in ignored_paths]

    def resolve_target(self, raw_path: str) -> tuple[Path, Path, bool]:
        if not raw_path or not raw_path.strip():
            raise ValueError("בחרו נתיב לקובץ או לתיקיית פרויקט.")
        target = Path(raw_path).expanduser().resolve(strict=True)
        if not target.is_file() and not target.is_dir():
            raise ValueError("הנתיב חייב להיות קובץ או תיקייה.")
        for component in target.parts:
            if component in ALWAYS_IGNORED:
                raise ValueError(f"הנתיב נמצא ברשימת ההתעלמות: {component}")
        if target.is_file():
            self._assert_not_ignored(target.name)
            return target.parent, target, True
        return target, target, False

    def _assert_not_ignored(self, relative: str) -> None:
        normalized = relative.replace("\\", "/").strip("/")
        parts = normalized.split("/")
        prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
        for pattern in self.ignored_paths:
            if any(
                fnmatch.fnmatchcase(part, pattern)
                for part in parts
            ) or any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes):
                raise ValueError(f"הנתיב נמצא ברשימת ההתעלמות: {normalized}")
        if any(part in ALWAYS_IGNORED for part in parts):
            raise ValueError(f"הנתיב נמצא ברשימת ההתעלמות: {normalized}")

    @staticmethod
    def _contained(root: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def gather_context(self, root: Path, target: Path, is_file: bool) -> dict[str, str]:
        root = root.resolve(strict=True)
        files: list[Path] = []
        if is_file:
            files = [target.resolve(strict=True)]
        else:
            for current, dirs, names in os.walk(root, followlinks=False):
                current_path = Path(current)
                relative_dir = current_path.relative_to(root).as_posix()
                dirs[:] = [
                    name for name in dirs
                    if not self._ignored_join(relative_dir, name)
                    and not (current_path / name).is_symlink()
                ]
                for name in names:
                    candidate = current_path / name
                    rel = candidate.relative_to(root).as_posix()
                    if not self._is_ignored(rel) and candidate.is_file():
                        files.append(candidate)
        result: dict[str, str] = {}
        consumed = 0
        for path in files:
            resolved = path.resolve(strict=True)
            if not self._contained(root, resolved):
                continue
            relative = resolved.relative_to(root).as_posix()
            if self._is_ignored(relative):
                continue
            try:
                if resolved.stat().st_size > MAX_FILE_BYTES:
                    continue
                content = resolved.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            byte_count = len(content.encode("utf-8"))
            if consumed + byte_count > MAX_CONTEXT_BYTES:
                break
            result[relative] = content
            consumed += byte_count
            if len(result) >= MAX_FILES:
                break
        if not result:
            raise ValueError("לא נמצאו קובצי טקסט מתאימים בפרויקט.")
        return result

    def _ignored_join(self, parent: str, name: str) -> bool:
        relative = f"{parent}/{name}".strip("/")
        return self._is_ignored(relative)

    def _is_ignored(self, relative: str) -> bool:
        try:
            self._assert_not_ignored(relative)
            return False
        except ValueError:
            return True

    def apply_changes(
        self, root: Path, originals: dict[str, str], changes: Any
    ) -> tuple[list[str], str]:
        if not isinstance(changes, list):
            raise ValueError("המודל לא החזיר שינויים תקינים.")
        if not changes:
            return [], ""
        validated: dict[Path, tuple[str, str]] = {}
        root = root.resolve(strict=True)
        for item in changes:
            if not isinstance(item, dict):
                raise ValueError("כל שינוי חייב להיות אובייקט עם path ו-content.")
            relative, content = item.get("path"), item.get("content")
            if not isinstance(relative, str) or not relative.strip():
                raise ValueError("לכל שינוי חייב להיות נתיב יחסי.")
            if not isinstance(content, str):
                raise ValueError(f"תוכן הקובץ {relative} חייב להיות טקסט.")
            rel_path = Path(relative)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ValueError(f"נתיב שינוי אינו בטוח: {relative}")
            normalized = rel_path.as_posix()
            self._assert_not_ignored(normalized)
            unresolved = root / rel_path
            cursor = root
            for component in rel_path.parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ValueError(f"לא ניתן לכתוב דרך קישור סמלי: {relative}")
            destination = unresolved.resolve(strict=False)
            if not self._contained(root, destination):
                raise ValueError(f"הנתיב חורג מתיקיית הפרויקט: {relative}")
            if destination.exists() and normalized not in originals:
                raise ValueError(f"הקובץ {relative} לא נכלל בהקשר ולכן לא יעודכן.")
            if destination in validated:
                raise ValueError(f"המודל החזיר נתיב כפול: {relative}")
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError(f"הקובץ {relative} חורג ממגבלת הגודל.")
            before = originals.get(normalized, "")
            validated[destination] = (before, content)

        written: list[Path] = []
        previous: dict[Path, str | None] = {}
        for destination, (before, _) in validated.items():
            try:
                previous[destination] = (
                    destination.read_text(encoding="utf-8")
                    if destination.exists() else None
                )
            except (OSError, UnicodeDecodeError) as exc:
                raise RuntimeError(f"לא ניתן לבדוק את הקובץ לפני כתיבה: {exc}") from exc
            relative = destination.relative_to(root).as_posix()
            expected = before if relative in originals else None
            if previous[destination] != expected:
                raise RuntimeError(
                    f"הקובץ {relative} השתנה בזמן עיבוד הבקשה; לא נכתבו שינויים."
                )
        try:
            for destination, (before, content) in validated.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                temp_name: str | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=destination.parent,
                        delete=False, suffix=".tmp",
                    ) as handle:
                        temp_name = handle.name
                        handle.write(content)
                    os.replace(temp_name, destination)
                finally:
                    if temp_name and os.path.exists(temp_name):
                        os.unlink(temp_name)
                written.append(destination)
        except (OSError, UnicodeDecodeError) as exc:
            for destination in reversed(written):
                old_content = previous[destination]
                if old_content is None:
                    destination.unlink(missing_ok=True)
                else:
                    destination.write_text(old_content, encoding="utf-8")
            raise RuntimeError(f"לא ניתן לשמור את השינויים: {exc}") from exc

        diff_parts: list[str] = []
        changed_paths: list[str] = []
        for destination, (before, after) in validated.items():
            if before == after:
                continue
            relative = destination.relative_to(root).as_posix()
            changed_paths.append(relative)
            diff_parts.extend(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{relative}" if before else "/dev/null",
                tofile=f"b/{relative}",
            ))
        return changed_paths, "".join(diff_parts)


def load_external_rules(raw_path: str) -> str:
    if not raw_path.strip():
        return ""
    path = Path(raw_path).expanduser()
    try:
        if not path.is_file():
            raise ValueError(f"קובץ הכללים החיצוני לא נמצא: {path}")
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"לא ניתן לקרוא את קובץ הכללים החיצוני: {exc}") from exc
