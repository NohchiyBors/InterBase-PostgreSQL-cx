"""Manifest конвертации: статус per-table, счётчики, resume (M3)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TableState:
    relation_id: int
    name: str
    target_name: str = ""
    status: str = "pending"  # pending | loading | done | failed
    rows_read: int = 0
    rows_written: int = 0
    checkpoint_pointer_page: int | None = None
    bad_pages: int = 0
    bad_page_numbers: list[int] = field(default_factory=list)
    back_versions_skipped: int = 0
    decode_errors: int = 0
    blobs_read: int = 0
    blobs_skipped: int = 0
    error: str | None = None


@dataclass
class Manifest:
    gdb_path: str
    schema: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "pending"
    warnings: list[str] = field(default_factory=list)
    tables: dict[str, TableState] = field(default_factory=dict)

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = time.time()

    def save(self, path: str | Path) -> None:
        payload = {
            "gdb_path": self.gdb_path,
            "schema": self.schema,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "warnings": self.warnings,
            "tables": {k: asdict(v) for k, v in self.tables.items()},
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        m = cls(
            gdb_path=raw["gdb_path"],
            schema=raw["schema"],
            started_at=raw["started_at"],
            finished_at=raw.get("finished_at"),
            status=raw.get("status", "pending"),
            warnings=raw.get("warnings", []),
        )
        m.tables = {k: TableState(**v) for k, v in raw["tables"].items()}
        return m
