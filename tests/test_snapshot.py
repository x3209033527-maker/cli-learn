import tempfile
import unittest
from pathlib import Path

from paicli_py.snapshot import SnapshotService, format_snapshot_detail, handle_snapshot_command


class SnapshotServiceTest(unittest.TestCase):
    def test_create_list_show_and_revert_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes").mkdir()
            (root / "notes" / "a.txt").write_text("one", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("ignore", encoding="utf-8")
            service = SnapshotService(root)

            snapshot = service.create("before edit")
            (root / "notes" / "a.txt").write_text("two", encoding="utf-8")

            self.assertEqual("before edit", snapshot.label)
            self.assertEqual(1, snapshot.file_count)
            self.assertEqual([snapshot.id], [item.id for item in service.list()])
            self.assertIn("notes/a.txt", format_snapshot_detail(service.find(snapshot.id)))

            result = service.revert(snapshot.id)

            self.assertIn("restored snapshot", result)
            self.assertEqual("one", (root / "notes" / "a.txt").read_text(encoding="utf-8"))
            self.assertFalse((root / ".paicli-py" / "snapshots" / snapshot.id / "files" / ".git" / "config").exists())

    def test_snapshot_command_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("one", encoding="utf-8")
            service = SnapshotService(root)

            created = handle_snapshot_command(service, "create checkpoint")
            snapshot_id = created.splitlines()[0].split(": ", 1)[1]

            self.assertIn("snapshot created", created)
            self.assertIn(snapshot_id, handle_snapshot_command(service, "list"))
            self.assertIn("Snapshot", handle_snapshot_command(service, f"show {snapshot_id}"))
            self.assertIn("restored snapshot", handle_snapshot_command(service, f"revert {snapshot_id}"))
            self.assertIn("Usage", handle_snapshot_command(service, "wat"))


if __name__ == "__main__":
    unittest.main()
