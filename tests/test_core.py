import tempfile
import unittest
from pathlib import Path

from core.file_manager import FileManager
from core.llm_client import parse_response


class FileManagerTests(unittest.TestCase):
    def test_applies_changes_and_returns_unified_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            manager = FileManager([".git", ".env"])
            changed, diff = manager.apply_changes(
                root,
                {"app.py": "value = 1\n"},
                [{"path": "app.py", "content": "value = 2\n"},
                 {"path": "new.py", "content": "print('new')\n"}],
            )
            self.assertEqual(changed, ["app.py", "new.py"])
            self.assertIn("-value = 1", diff)
            self.assertIn("+value = 2", diff)
            self.assertEqual((root / "new.py").read_text(encoding="utf-8"), "print('new')\n")

    def test_rejects_parent_traversal_and_ignored_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = FileManager([".env", "private"])
            for path in ("../outside.txt", ".env", "private/secret.txt"):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        manager.apply_changes(
                            root, {}, [{"path": path, "content": "unsafe"}]
                        )

    def test_empty_change_list_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                FileManager([]).apply_changes(Path(directory), {}, []), ([], "")
            )


class LLMResponseTests(unittest.TestCase):
    def test_parses_structured_response(self):
        result = parse_response(
            '{"summary":"Updated","reasoning":"Small fix","changes":[]} '
        )
        self.assertEqual(result["summary"], "Updated")
        self.assertEqual(result["changes"], [])

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            parse_response("not json")


if __name__ == "__main__":
    unittest.main()
