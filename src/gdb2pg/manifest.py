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
    status: str = "pending"  # pending | loading | done | failed
    rows_read: int = 0
    rows_written: int = 0
    checkpoint_pointer_page: int | None = None
    error: str | None = None


@dataclass
class Manifest:
    gdb_path: str
    schema: str
    started_at: float = field(default_factory=time.time)
    tables: dict[str, TableState] = field(default_factory=dict)

    def save(self, path: str | Path) -> None:
        payload = {
            "gdb_path": self.gdb_path,
            "schema": self.schema,
            "started_at": self.started_at,
            "tables": {k: asdict(v) for k, v in self.tables.items()},
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        m = cls(gdb_path=raw["gdb_path"], schema=raw["schema"],
                started_at=raw["started_at"])
        m.tables = {k: TableState(**v) for k, v in raw["tables"].items()}
        return m
