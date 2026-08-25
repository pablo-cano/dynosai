# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""SQLite persistence, schema management, migrations, and authoritative workflow storage."""

from __future__ import annotations

import sqlite3
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import json_dumps, json_loads, utc_now


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  work_type TEXT NOT NULL,
  state TEXT NOT NULL,
  quick_flow INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  branch TEXT,
  worktree TEXT,
  quality_score INTEGER NOT NULL DEFAULT 0,
  workspace_strategy TEXT NOT NULL DEFAULT 'isolated_worktree',
  provider_session_id TEXT,
  original_branch TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'accepted',
  created_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  state TEXT NOT NULL,
  requirements TEXT NOT NULL DEFAULT '[]',
  acceptance TEXT NOT NULL DEFAULT '[]',
  files TEXT NOT NULL DEFAULT '[]',
  validation_commands TEXT NOT NULL DEFAULT '[]',
  evidence_required TEXT NOT NULL DEFAULT '[]',
  depends_on TEXT NOT NULL DEFAULT '[]',
  owner TEXT,
  claimed_run TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  agent TEXT,
  status TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  files_changed TEXT NOT NULL DEFAULT '[]',
  tests TEXT NOT NULL DEFAULT '[]',
  base_commit TEXT NOT NULL DEFAULT '',
  context_id TEXT,
  declared_files TEXT NOT NULL DEFAULT '[]',
  verified_files TEXT NOT NULL DEFAULT '[]',
  verification_status TEXT NOT NULL DEFAULT 'pending',
  supersedes_run TEXT,
  session_id TEXT,
  worktree TEXT,
  task_ids TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  commit_hash TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS validations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  command TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER NOT NULL,
  output TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS features (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL,
  commit_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id)
);

CREATE TABLE IF NOT EXISTS feature_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_id TEXT NOT NULL,
  rule TEXT NOT NULL,
  FOREIGN KEY(feature_id) REFERENCES features(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feature_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_id TEXT NOT NULL,
  path TEXT NOT NULL,
  role TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence REAL NOT NULL,
  content_hash TEXT,
  symbol TEXT,
  validity TEXT NOT NULL DEFAULT 'current',
  UNIQUE(feature_id, path, role, symbol),
  FOREIGN KEY(feature_id) REFERENCES features(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS code_files (
  path TEXT PRIMARY KEY,
  language TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  size INTEGER NOT NULL,
  indexed_commit TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  qualified_name TEXT NOT NULL DEFAULT '',
  parent_id TEXT,
  kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  signature TEXT NOT NULL,
  docstring TEXT NOT NULL,
  body TEXT NOT NULL,
  FOREIGN KEY(path) REFERENCES code_files(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  module TEXT NOT NULL,
  UNIQUE(path, module),
  FOREIGN KEY(path) REFERENCES code_files(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE(source_type, source_id, relation, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS context_manifests (
  id TEXT PRIMARY KEY,
  work_id TEXT,
  purpose TEXT NOT NULL,
  query TEXT NOT NULL,
  max_tokens INTEGER NOT NULL,
  estimated_tokens INTEGER NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_searches (
  name TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  entity_id TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS semantic_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  source_commit TEXT NOT NULL DEFAULT '',
  created_commit TEXT NOT NULL DEFAULT '',
  last_changed_commit TEXT NOT NULL DEFAULT '',
  last_verified_commit TEXT NOT NULL DEFAULT '',
  indexed_at_commit TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'current',
  vector BLOB NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_documents_entity ON semantic_documents(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_semantic_documents_model ON semantic_documents(model, status);

CREATE TABLE IF NOT EXISTS id_sequences (
  prefix TEXT PRIMARY KEY,
  next_value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  run_id TEXT,
  task_id TEXT,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE SET NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS call_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_symbol TEXT NOT NULL,
  target_name TEXT NOT NULL,
  target_symbol TEXT,
  path TEXT NOT NULL,
  line INTEGER NOT NULL,
  UNIQUE(source_symbol,target_name,path,line)
);

CREATE TABLE IF NOT EXISTS routes (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  method TEXT NOT NULL,
  symbol_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS test_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  test_symbol TEXT NOT NULL,
  target_symbol TEXT NOT NULL,
  confidence REAL NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE(test_symbol,target_symbol)
);

CREATE TABLE IF NOT EXISTS retrieval_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL,
  work_id TEXT,
  returned_entities TEXT NOT NULL DEFAULT '[]',
  returned_files TEXT NOT NULL DEFAULT '[]',
  estimated_tokens INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_metrics (
  run_id TEXT PRIMARY KEY,
  repository_files INTEGER NOT NULL DEFAULT 0,
  context_tokens INTEGER NOT NULL DEFAULT 0,
  suggested_files INTEGER NOT NULL DEFAULT 0,
  files_read INTEGER NOT NULL DEFAULT 0,
  actual_files INTEGER NOT NULL DEFAULT 0,
  planned_files_hit INTEGER NOT NULL DEFAULT 0,
  unplanned_files INTEGER NOT NULL DEFAULT 0,
  test_passed INTEGER NOT NULL DEFAULT 0,
  retries INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS artifact_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  work_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  revision INTEGER NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(artifact_id, revision)
);

CREATE TABLE IF NOT EXISTS validation_profiles (
  name TEXT PRIMARY KEY,
  command_json TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'baseline',
  approved INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  agent TEXT NOT NULL,
  run_id TEXT,
  work_id TEXT,
  worktree TEXT NOT NULL,
  state TEXT NOT NULL,
  permissions TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  expires_at TEXT,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_run ON agent_sessions(run_id,state);

CREATE TABLE IF NOT EXISTS scope_requests (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL,
  run_id TEXT,
  path TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_evidence_links (
  task_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  requirement_id TEXT,
  acceptance_id TEXT,
  symbol_id TEXT,
  test_name TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY(task_id,evidence_id,acceptance_id,symbol_id,test_name)
);

CREATE TABLE IF NOT EXISTS run_overlays (
  run_id TEXT NOT NULL,
  path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  symbols TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(run_id,path)
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
CREATE INDEX IF NOT EXISTS idx_call_edges_target ON call_edges(target_symbol);
CREATE INDEX IF NOT EXISTS idx_test_links_target ON test_links(target_symbol);
CREATE INDEX IF NOT EXISTS idx_feature_files_path ON feature_files(path,validity);


CREATE TABLE IF NOT EXISTS provider_sessions (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  work_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  strategy TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',
  permissions_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_provider_sessions_work ON provider_sessions(work_id,state);
CREATE INDEX IF NOT EXISTS idx_provider_sessions_provider ON provider_sessions(provider,state);

CREATE TABLE IF NOT EXISTS provider_capabilities (
  provider TEXT PRIMARY KEY,
  protocol TEXT NOT NULL DEFAULT '',
  client_name TEXT NOT NULL DEFAULT '',
  client_version TEXT NOT NULL DEFAULT '',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_interactions (
  id TEXT PRIMARY KEY,
  work_id TEXT,
  session_id TEXT,
  kind TEXT NOT NULL,
  gate TEXT,
  message TEXT NOT NULL,
  schema_json TEXT NOT NULL DEFAULT '{}',
  response_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  provider TEXT,
  dedupe_key TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY(work_id) REFERENCES work_items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_human_interactions_work ON human_interactions(work_id,status);
CREATE INDEX IF NOT EXISTS idx_human_interactions_dedupe ON human_interactions(dedupe_key,status);

CREATE TABLE IF NOT EXISTS application_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  project_id TEXT,
  work_id TEXT,
  session_id TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
  entity_type UNINDEXED,
  entity_id UNINDEXED,
  title,
  body,
  tokenize='unicode61 remove_diacritics 2'
);
"""


class Database:
    """Authoritative local project database."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / ".dynosai" / "knowledge.db"

    CURRENT_SCHEMA_VERSION = 6

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_version = 0
        if self.path.exists():
            try:
                raw = sqlite3.connect(self.path)
                try:
                    table = raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
                    if table:
                        row = raw.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                        existing_version = int(row[0]) if row else 0
                finally:
                    raw.close()
            except sqlite3.DatabaseError:
                existing_version = 0
        if existing_version > self.CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {existing_version} is newer than supported schema {self.CURRENT_SCHEMA_VERSION}")
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection, existing_version)
            connection.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(self.CURRENT_SCHEMA_VERSION),))
            connection.commit()

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}

    def _migrate(self, connection: sqlite3.Connection, existing_version: int = 0) -> None:
        """Idempotent, forward-only migrations through schema v6."""
        columns = self._columns(connection, "symbols")
        for name, sql in (("qualified_name", "ALTER TABLE symbols ADD COLUMN qualified_name TEXT NOT NULL DEFAULT ''"),("parent_id", "ALTER TABLE symbols ADD COLUMN parent_id TEXT")):
            if name not in columns: connection.execute(sql)
        run_columns = self._columns(connection, "runs")
        for name, sql in (
            ("base_commit", "ALTER TABLE runs ADD COLUMN base_commit TEXT NOT NULL DEFAULT ''"),
            ("context_id", "ALTER TABLE runs ADD COLUMN context_id TEXT"),
            ("declared_files", "ALTER TABLE runs ADD COLUMN declared_files TEXT NOT NULL DEFAULT '[]'"),
            ("verified_files", "ALTER TABLE runs ADD COLUMN verified_files TEXT NOT NULL DEFAULT '[]'"),
            ("verification_status", "ALTER TABLE runs ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'pending'"),
            ("supersedes_run", "ALTER TABLE runs ADD COLUMN supersedes_run TEXT"),
            ("session_id", "ALTER TABLE runs ADD COLUMN session_id TEXT"),
            ("worktree", "ALTER TABLE runs ADD COLUMN worktree TEXT"),
            ("task_ids", "ALTER TABLE runs ADD COLUMN task_ids TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in run_columns: connection.execute(sql)
        task_columns = self._columns(connection, "tasks")
        for name, sql in (("owner", "ALTER TABLE tasks ADD COLUMN owner TEXT"),("claimed_run", "ALTER TABLE tasks ADD COLUMN claimed_run TEXT")):
            if name not in task_columns: connection.execute(sql)
        work_columns = self._columns(connection, "work_items")
        for name, sql in (
            ("workspace_strategy", "ALTER TABLE work_items ADD COLUMN workspace_strategy TEXT NOT NULL DEFAULT 'isolated_worktree'"),
            ("provider_session_id", "ALTER TABLE work_items ADD COLUMN provider_session_id TEXT"),
            ("original_branch", "ALTER TABLE work_items ADD COLUMN original_branch TEXT"),
        ):
            if name not in work_columns: connection.execute(sql)
        # Indexes that depend on columns introduced by migrations must be created only
        # after the ALTER TABLE statements above have completed.
        connection.execute("CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path)")
        sem_columns = self._columns(connection, "semantic_documents")
        for name in ("created_commit","last_changed_commit","last_verified_commit","indexed_at_commit"):
            if name not in sem_columns:
                connection.execute(f"ALTER TABLE semantic_documents ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        now=utc_now()
        connection.execute("INSERT OR IGNORE INTO migrations(version,name,applied_at) VALUES(3,'governance-codegraph-recovery',?)", (now,))
        connection.execute("INSERT OR IGNORE INTO migrations(version,name,applied_at) VALUES(4,'hardening-scale-revisions-sessions-policy',?)", (now,))
        connection.execute("INSERT OR IGNORE INTO migrations(version,name,applied_at) VALUES(5,'headless-core-provider-sessions-elicitations',?)", (now,))
        # Prevent concurrent provider polling from creating duplicate
        # pending gates for the same logical interaction. Clean pre-v6 duplicates
        # deterministically before adding the partial unique index.
        duplicate_keys = connection.execute(
            "SELECT dedupe_key FROM human_interactions WHERE dedupe_key IS NOT NULL AND status='pending' GROUP BY dedupe_key HAVING COUNT(*)>1"
        ).fetchall()
        for key_row in duplicate_keys:
            key = key_row[0]
            rows = connection.execute(
                "SELECT id FROM human_interactions WHERE dedupe_key=? AND status='pending' ORDER BY created_at,id", (key,)
            ).fetchall()
            for duplicate in rows[1:]:
                connection.execute(
                    "UPDATE human_interactions SET status='cancelled',resolved_at=? WHERE id=?", (now, duplicate[0])
                )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_human_interactions_one_pending ON human_interactions(dedupe_key) "
            "WHERE dedupe_key IS NOT NULL AND status='pending'"
        )
        connection.execute("INSERT OR IGNORE INTO migrations(version,name,applied_at) VALUES(6,'scope-lifecycle-and-gate-dedup',?)", (now,))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as connection:
            connection.execute(sql, params)
            connection.commit()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return str(row["value"]) if row else default

    def next_id(self, prefix: str, table: str, column: str = "id") -> str:
        # BEGIN IMMEDIATE serializes ID allocation between concurrent agents.
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT next_value FROM id_sequences WHERE prefix=?", (prefix,)).fetchone()
            if row is None:
                existing = connection.execute(
                    f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY {column} DESC LIMIT 1",
                    (f"{prefix}-%",),
                ).fetchone()
                value = int(existing[0].split("-")[-1]) + 1 if existing else 1
                connection.execute("INSERT INTO id_sequences(prefix,next_value) VALUES(?,?)", (prefix, value + 1))
            else:
                value = int(row[0])
                connection.execute("UPDATE id_sequences SET next_value=? WHERE prefix=?", (value + 1, prefix))
        return f"{prefix}-{value:04d}"

    def audit(self, event_type: str, entity_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        created = utc_now()
        body = payload or {}
        self.execute(
            "INSERT INTO audit(event_type,entity_id,payload,created_at) VALUES(?,?,?,?)",
            (event_type, entity_id, json_dumps(body), created),
        )
        state_dir = self.root / ".dynosai" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        event = {"event_type": event_type, "entity_id": entity_id, "payload": body, "created_at": created}
        with (state_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def index_search(self, entity_type: str, entity_id: str, title: str, body: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM search_fts WHERE entity_type=? AND entity_id=?", (entity_type, entity_id))
            connection.execute(
                "INSERT INTO search_fts(entity_type,entity_id,title,body) VALUES(?,?,?,?)",
                (entity_type, entity_id, title, body),
            )
            connection.commit()

    def fts(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Natural-language FTS retrieval.

        FTS5 joins bare terms with AND. That is appropriate for an exact search box,
        but it is brittle for agent questions ("what file adapts X to the CLI?").
        Keep identifiers and meaningful words, discard conversational noise, and use
        an OR query; graph expansion/reranking performs the narrowing afterwards.
        """
        stop = {
            "que","qué","como","cómo","donde","dónde","cual","cuál","cuales","cuáles",
            "quien","quién","para","por","del","los","las","una","uno","unos","unas",
            "archivo","archivos","fichero","ficheros","parte","partes","sistema","codigo","código",
            "the","what","which","where","how","file","files","code","does","is","are",
            "de","la","el","en","y","o","a","se","un","con","to","of","in","and","or"
        }
        parts = re.findall(r"[\w./:@+-]{2,}", query.lower(), flags=re.UNICODE)
        meaningful: list[str] = []
        for part in parts:
            if part in stop:
                continue
            if part not in meaningful:
                meaningful.append(part)
        if not meaningful:
            return []
        # Quote every token so punctuation cannot become FTS syntax. Limit query
        # breadth to keep candidate generation bounded on large repositories.
        fts_query = " OR ".join('"' + token.replace('"', '""') + '"' for token in meaningful[:12])
        try:
            rows = self.query(
                "SELECT entity_type,entity_id,title,body,bm25(search_fts) AS rank "
                "FROM search_fts WHERE search_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, limit),
            )
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass
        # Last-resort lexical fallback, bounded to a few meaningful tokens.
        clauses=[]; params: list[Any]=[]
        for token in meaningful[:4]:
            clauses.append("(title LIKE ? OR body LIKE ?)")
            like=f"%{token}%"; params.extend([like,like])
        if not clauses:
            return []
        params.append(limit)
        return self.query(
            "SELECT entity_type,entity_id,title,body,0 AS rank FROM search_fts WHERE " + " OR ".join(clauses) + " LIMIT ?",
            tuple(params),
        )

    def _load_sqlite_vec(self, connection: sqlite3.Connection) -> bool:
        try:
            import sqlite_vec

            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            return True
        except Exception:
            try:
                connection.enable_load_extension(False)
            except Exception:
                pass
            return False

    def ensure_vector_schema(self, dimensions: int = 384) -> str:
        with self.connect() as connection:
            if not self._load_sqlite_vec(connection):
                self.set_meta("vector_backend", "numpy")
                return "numpy"
            connection.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS semantic_vec USING vec0(embedding float[{int(dimensions)}] distance_metric=cosine)"
            )
            connection.commit()
        self.set_meta("vector_backend", "sqlite-vec")
        return "sqlite-vec"

    def vector_backend(self) -> str:
        configured = self.get_meta("vector_backend")
        dimensions = int(self.get_meta("embedding_dimensions", "384") or 384)
        if configured == "sqlite-vec":
            # A project may be copied to a machine where loadable SQLite extensions
            # are unavailable. Verify the backend instead of trusting stale metadata.
            with self.connect() as connection:
                if self._load_sqlite_vec(connection):
                    try:
                        connection.execute("SELECT rowid FROM semantic_vec LIMIT 1").fetchall()
                        return "sqlite-vec"
                    except sqlite3.OperationalError:
                        pass
            self.set_meta("vector_backend", "numpy")
            return "numpy"
        if configured == "numpy":
            return "numpy"
        return self.ensure_vector_schema(dimensions)

    def upsert_semantic_document(
        self,
        *,
        entity_type: str,
        entity_id: str,
        title: str,
        body: str,
        content_hash: str,
        model: str,
        dimensions: int,
        source_commit: str,
        status: str,
        vector: Any,
        verification_commit: str | None = None,
    ) -> None:
        import numpy as np

        array = np.asarray(vector, dtype=np.float32)
        if array.shape != (dimensions,):
            raise ValueError(f"Expected vector with {dimensions} dimensions, got {array.shape}")
        backend = self.ensure_vector_schema(dimensions)
        now = utc_now()
        verified_commit = verification_commit if verification_commit is not None else source_commit
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM semantic_documents WHERE entity_type=? AND entity_id=?",
                (entity_type, entity_id),
            ).fetchone()
            if existing:
                row_id = int(existing[0])
                if backend == "sqlite-vec":
                    self._load_sqlite_vec(connection)
                    connection.execute("DELETE FROM semantic_vec WHERE rowid=?", (row_id,))
                connection.execute(
                    "UPDATE semantic_documents SET title=?,body=?,content_hash=?,model=?,dimensions=?,source_commit=?,last_changed_commit=?,last_verified_commit=?,indexed_at_commit=?,status=?,vector=?,updated_at=? WHERE id=?",
                    (title, body, content_hash, model, dimensions, source_commit, source_commit, verified_commit, verified_commit, status, array.tobytes(), now, row_id),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO semantic_documents(entity_type,entity_id,title,body,content_hash,model,dimensions,source_commit,created_commit,last_changed_commit,last_verified_commit,indexed_at_commit,status,vector,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (entity_type, entity_id, title, body, content_hash, model, dimensions, source_commit, source_commit, source_commit, verified_commit, verified_commit, status, array.tobytes(), now, now),
                )
                row_id = int(cursor.lastrowid)
            if backend == "sqlite-vec":
                self._load_sqlite_vec(connection)
                connection.execute("INSERT INTO semantic_vec(rowid,embedding) VALUES(?,?)", (row_id, array))
            connection.commit()

    def semantic_search(self, vector: Any, limit: int, model: str) -> list[dict[str, Any]]:
        import numpy as np

        array = np.asarray(vector, dtype=np.float32)
        backend = self.vector_backend()
        if backend == "sqlite-vec":
            try:
                with self.connect() as connection:
                    if not self._load_sqlite_vec(connection):
                        raise sqlite3.OperationalError("sqlite-vec could not be loaded")
                    rows = connection.execute(
                        "SELECT d.*, v.distance FROM (SELECT rowid,distance FROM semantic_vec WHERE embedding MATCH ? AND k=?) v JOIN semantic_documents d ON d.id=v.rowid WHERE d.model=? ORDER BY v.distance",
                        (array, int(limit), model),
                    ).fetchall()
                    return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                # Keep semantic search available even if a copied project lands on a
                # Python/SQLite build that cannot load native extensions.
                self.set_meta("vector_backend", "numpy")
        rows = self.query("SELECT * FROM semantic_documents WHERE model=?", (model,))
        query_norm = float(np.linalg.norm(array)) or 1.0
        scored: list[dict[str, Any]] = []
        for row in rows:
            candidate = np.frombuffer(row["vector"], dtype=np.float32)
            denom = query_norm * (float(np.linalg.norm(candidate)) or 1.0)
            cosine = float(np.dot(array, candidate) / denom)
            item = dict(row)
            item["distance"] = max(0.0, 1.0 - cosine)
            scored.append(item)
        scored.sort(key=lambda item: item["distance"])
        return scored[:limit]

    def clear_semantic_documents(self) -> None:
        backend = self.vector_backend()
        with self.connect() as connection:
            if backend == "sqlite-vec":
                self._load_sqlite_vec(connection)
                connection.execute("DELETE FROM semantic_vec")
            connection.execute("DELETE FROM semantic_documents")
            connection.commit()

    def delete_semantic_documents_not_in(self, valid_keys: set[tuple[str, str]], model: str) -> int:
        rows = self.query("SELECT id,entity_type,entity_id FROM semantic_documents WHERE model=?", (model,))
        remove = [row for row in rows if (row["entity_type"], row["entity_id"]) not in valid_keys]
        if not remove:
            return 0
        backend = self.vector_backend()
        with self.connect() as connection:
            if backend == "sqlite-vec":
                self._load_sqlite_vec(connection)
            for row in remove:
                if backend == "sqlite-vec":
                    connection.execute("DELETE FROM semantic_vec WHERE rowid=?", (row["id"],))
                connection.execute("DELETE FROM semantic_documents WHERE id=?", (row["id"],))
            connection.commit()
        return len(remove)

    @staticmethod
    def decode(row: dict[str, Any], *fields: str) -> dict[str, Any]:
        copy = dict(row)
        for field in fields:
            copy[field] = json_loads(copy.get(field), [])
        return copy
