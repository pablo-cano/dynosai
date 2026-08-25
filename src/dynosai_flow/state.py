# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Portable state snapshot, integrity validation, and atomic restore operations."""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .db import Database, SCHEMA
from .util import utc_now

# Only authoritative/portable knowledge is snapshotted. Code graph, vectors, FTS,
# context manifests and telemetry are derived and rebuilt after restore.
PORTABLE_TABLES = [
    "meta", "work_items", "artifacts", "artifact_revisions", "decisions", "tasks", "runs", "checkpoints",
    "validations", "features", "feature_rules", "feature_files", "saved_searches", "audit", "evidence",
    "task_evidence_links", "validation_profiles", "scope_requests", "id_sequences", "migrations",
]


class StateManager:
    def __init__(self, root: str | Path, db: Database):
        self.root=Path(root).resolve(); self.db=db
        self.state_dir=self.root/".dynosai"/"state"; self.backup_dir=self.root/".dynosai"/"backups"

    def snapshot(self, reason:str="manual")->dict[str,Any]:
        self.state_dir.mkdir(parents=True,exist_ok=True); self.backup_dir.mkdir(parents=True,exist_ok=True)
        created=utc_now(); payload={"format":"dynosai-portable-state-v2","schema_version":self.db.get_meta("schema_version",str(self.db.CURRENT_SCHEMA_VERSION)),"created_at":created,"reason":reason,"tables":{}}
        for table in PORTABLE_TABLES:
            try: payload["tables"][table]=self.db.query(f"SELECT * FROM {table}")
            except Exception: payload["tables"][table]=[]
        portable=self.state_dir/"snapshot.json.gz"
        tmp=portable.with_suffix(".tmp")
        with gzip.open(tmp,"wt",encoding="utf-8") as handle: json.dump(payload,handle,ensure_ascii=False,sort_keys=True)
        os.replace(tmp,portable)
        stamp=created.replace(":","").replace("-","").replace("+","_"); sqlite_copy=self.backup_dir/f"knowledge-{stamp}.db"
        with self.db.connect() as source:
            target=sqlite3.connect(sqlite_copy)
            try: source.backup(target)
            finally: target.close()
        self.db.audit("StateSnapshotCreated",None,{"reason":reason,"portable":str(portable.relative_to(self.root)),"portable_tables":PORTABLE_TABLES})
        return {"portable":str(portable),"sqlite":str(sqlite_copy),"created_at":created,"reason":reason}

    def restore(self,snapshot:str|Path|None=None)->dict[str,Any]:
        source=(Path(snapshot) if snapshot else self.state_dir/"snapshot.json.gz").resolve()
        if not source.exists(): raise FileNotFoundError(source)
        with gzip.open(source,"rt",encoding="utf-8") as handle: payload=json.load(handle)
        if payload.get("format") not in {"dynosai-portable-state-v1","dynosai-portable-state-v2"}: raise ValueError("Unsupported DynosAI snapshot format")
        version=int(payload.get("schema_version") or 0)
        if version>Database.CURRENT_SCHEMA_VERSION: raise RuntimeError(f"Snapshot schema {version} is newer than this DynosAI ({Database.CURRENT_SCHEMA_VERSION})")
        safety=self.snapshot("pre-restore") if self.db.path.exists() else None
        temp=self.db.path.with_name("knowledge.restore.tmp.db")
        for suffix in ("","-wal","-shm"):
            try:(Path(str(temp)+suffix)).unlink()
            except FileNotFoundError:pass
        connection=sqlite3.connect(temp)
        try:
            connection.execute("PRAGMA foreign_keys=OFF"); connection.executescript(SCHEMA); connection.execute("PRAGMA foreign_keys=OFF"); self.db._migrate(connection,version)
            tables=payload.get("tables",{})
            for table in PORTABLE_TABLES:
                rows=tables.get(table,[])
                if not rows: continue
                available={r[1] for r in connection.execute(f"PRAGMA table_info({table})").fetchall()}
                cols=[c for c in rows[0].keys() if c in available]
                if not cols: continue
                placeholders=",".join("?" for _ in cols); names=",".join(cols)
                connection.executemany(f"INSERT OR REPLACE INTO {table}({names}) VALUES({placeholders})",[tuple(row.get(c) for c in cols) for row in rows])
            connection.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(Database.CURRENT_SCHEMA_VERSION),))
            connection.commit(); connection.execute("PRAGMA foreign_keys=ON")
            integrity=connection.execute("PRAGMA integrity_check").fetchone()[0]
            fk=connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity!="ok" or fk: raise RuntimeError(f"Restored database failed validation: integrity={integrity}, fk={len(fk)}")
        except Exception as exc:
            connection.close()
            try:temp.unlink()
            except FileNotFoundError:pass
            if isinstance(exc, RuntimeError): raise
            raise RuntimeError(f"Restore failed validation/import: {exc}") from exc
        finally:
            try: connection.close()
            except Exception: pass
        # Atomic replacement only after complete validation.
        os.replace(temp,self.db.path); self.db.initialize(); self.db.audit("StateRestored",None,{"snapshot":str(source),"safety_backup":safety,"atomic":True})
        return {"restored":str(source),"schema_version":str(Database.CURRENT_SCHEMA_VERSION),"safety_backup":safety,"atomic":True,"requires_reindex":True}
