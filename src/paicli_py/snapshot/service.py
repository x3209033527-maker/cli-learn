from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from paicli_py.policy import PathGuard


DEFAULT_EXCLUDES = {
    ".git",
    ".paicli-py/snapshots",
    "__pycache__",
    ".pytest_cache",
    "target",
    "node_modules",
}


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Snapshot:
    id: str
    label: str
    created_at: str
    file_count: int
    byte_count: int
    files: tuple[SnapshotFile, ...] = ()


class SnapshotService:
    def __init__(self, project_path: str | Path, snapshot_dir: str | Path | None = None):
        self.project_path = Path(project_path).resolve()
        self.path_guard = PathGuard(self.project_path)
        self.snapshot_dir = Path(snapshot_dir).resolve() if snapshot_dir else self.project_path / ".paicli-py" / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def create(self, label: str = "") -> Snapshot:
        snapshot_id = "snap_" + uuid.uuid4().hex[:12]
        created_at = _now()
        safe_label = _compact(label, 120)
        root = self.snapshot_dir / snapshot_id
        files_root = root / "files"
        files_root.mkdir(parents=True, exist_ok=False)

        files: list[SnapshotFile] = []
        for source in self._iter_project_files():
            rel = source.relative_to(self.project_path).as_posix()
            destination = files_root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            data = source.read_bytes()
            files.append(SnapshotFile(rel, len(data), hashlib.sha256(data).hexdigest()))

        snapshot = Snapshot(
            snapshot_id,
            safe_label,
            created_at,
            len(files),
            sum(item.size for item in files),
            tuple(files),
        )
        _write_manifest(root / "manifest.json", snapshot)
        return snapshot

    def list(self, limit: int = 20) -> list[Snapshot]:
        snapshots = []
        for manifest in self.snapshot_dir.glob("snap_*/manifest.json"):
            try:
                snapshots.append(_read_manifest(manifest, include_files=False))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        snapshots.sort(key=lambda item: item.created_at, reverse=True)
        return snapshots[: max(1, min(100, int(limit)))]

    def find(self, snapshot_id: str) -> Snapshot | None:
        normalized = snapshot_id.strip()
        if not normalized:
            return None
        manifest = self.snapshot_dir / normalized / "manifest.json"
        if not manifest.exists():
            return None
        return _read_manifest(manifest, include_files=True)

    def revert(self, snapshot_id: str) -> str:
        snapshot = self.find(snapshot_id)
        if snapshot is None:
            return f"snapshot not found: {snapshot_id}"
        restored = 0
        files_root = self.snapshot_dir / snapshot.id / "files"
        for item in snapshot.files:
            source = files_root / item.path
            if not source.exists():
                continue
            target = self.path_guard.resolve_safe(item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored += 1
        return f"restored snapshot {snapshot.id}: {restored} files"

    def _iter_project_files(self):
        for path in sorted(self.project_path.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            rel = path.relative_to(self.project_path).as_posix()
            if self._excluded(rel):
                continue
            yield path

    def _excluded(self, rel: str) -> bool:
        return any(rel == pattern or rel.startswith(pattern.rstrip("/") + "/") for pattern in DEFAULT_EXCLUDES)


def handle_snapshot_command(service: SnapshotService, payload: str) -> str:
    normalized = (payload or "list").strip()
    if not normalized or normalized == "list":
        return format_snapshot_list(service.list())
    if normalized.startswith("list "):
        try:
            limit = int(normalized[5:].strip())
        except ValueError:
            limit = 20
        return format_snapshot_list(service.list(limit))
    if normalized.startswith("create"):
        label = normalized[len("create"):].strip()
        snapshot = service.create(label)
        return f"snapshot created: {snapshot.id}\nFiles: {snapshot.file_count}\nBytes: {snapshot.byte_count}"
    if normalized.startswith("show "):
        snapshot = service.find(normalized[5:].strip())
        return format_snapshot_detail(snapshot) if snapshot else "snapshot not found"
    if normalized.startswith("revert "):
        return service.revert(normalized[7:].strip())
    return "Usage: /snapshot [list [N] | create [label] | show <id> | revert <id>]"


def format_snapshot_list(snapshots: list[Snapshot]) -> str:
    if not snapshots:
        return "No snapshots."
    lines = [f"Snapshots: {len(snapshots)}"]
    for snapshot in snapshots:
        label = f"  {snapshot.label}" if snapshot.label else ""
        lines.append(f"{snapshot.id}  {snapshot.file_count} files  {snapshot.byte_count} bytes  {snapshot.created_at}{label}")
    return "\n".join(lines)


def format_snapshot_detail(snapshot: Snapshot) -> str:
    lines = [
        f"Snapshot {snapshot.id}",
        f"Created: {snapshot.created_at}",
        f"Label: {snapshot.label or '-'}",
        f"Files: {snapshot.file_count}",
        f"Bytes: {snapshot.byte_count}",
    ]
    for item in snapshot.files:
        lines.append(f"- {item.path}  {item.size} bytes  {item.sha256[:12]}")
    return "\n".join(lines)


def _write_manifest(path: Path, snapshot: Snapshot) -> None:
    payload = {
        "id": snapshot.id,
        "label": snapshot.label,
        "created_at": snapshot.created_at,
        "file_count": snapshot.file_count,
        "byte_count": snapshot.byte_count,
        "files": [item.__dict__ for item in snapshot.files],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_manifest(path: Path, include_files: bool) -> Snapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = tuple(
        SnapshotFile(str(item["path"]), int(item["size"]), str(item["sha256"]))
        for item in payload.get("files", [])
    ) if include_files else ()
    return Snapshot(
        str(payload["id"]),
        str(payload.get("label", "")),
        str(payload["created_at"]),
        int(payload.get("file_count", len(files))),
        int(payload.get("byte_count", sum(item.size for item in files))),
        files,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(value: str, max_chars: int) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized if len(normalized) <= max_chars else normalized[:max_chars - 3] + "..."
