# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Local embedding provider integration and SQLite-backed semantic indexing."""

from __future__ import annotations

import os
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

import numpy as np

from .db import Database
from .util import sha256_text, utc_now, json_loads


DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_DIMENSIONS = 384


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[np.ndarray]: ...


class FastEmbedProvider:
    """Lazy FastEmbed wrapper. The model is downloaded only by model install/use."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        cache_dir: str | Path | None = None,
        dimensions: int = DEFAULT_DIMENSIONS,
    ):
        self.model_name = model_name
        self.dimensions = dimensions
        self.cache_dir = Path(cache_dir or default_cache_dir()).expanduser().resolve()
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # FastEmbed may emit a migration warning for this model because its
            # maintained ONNX definition uses mean pooling. DynosAI accepts
            # that profile explicitly and records it with the semantic index.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*now uses mean pooling instead of CLS embedding.*", category=UserWarning)
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                )
        return self._model

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        vectors = [np.asarray(item, dtype=np.float32) for item in self._load().embed(texts)]
        for vector in vectors:
            if vector.shape != (self.dimensions,):
                raise RuntimeError(
                    f"Embedding model returned {vector.shape}; expected ({self.dimensions},)"
                )
        return vectors


@dataclass(slots=True)
class SemanticHit:
    entity_type: str
    entity_id: str
    title: str
    body: str
    score: float
    distance: float
    model: str
    source_commit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "title": self.title,
            "body": self.body,
            "score": round(self.score, 6),
            "distance": round(self.distance, 6),
            "model": self.model,
            "source_commit": self.source_commit,
        }


def default_cache_dir() -> Path:
    override = os.environ.get("DYNOSAI_MODEL_CACHE")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "DynosAI" / "models"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dynosai" / "models"


class SemanticIndex:
    """Neural semantic index with sqlite-vec primary and NumPy fallback."""

    def __init__(self, db: Database, provider: EmbeddingProvider | None = None):
        self.db = db
        def meta(key: str, default: str) -> str:
            # Read constructor defaults without creating an empty SQLite database.
            # sqlite3.connect() creates the file on first access; read-only commands
            # such as `agent-config` must stay observational until a DynosAI project
            # is explicitly initialized/adopted.
            if not db.path.exists():
                return default
            try:
                return db.get_meta(key, default) or default
            except Exception:
                return default
        self.provider = provider or FastEmbedProvider(
            model_name=meta("embedding_model", DEFAULT_MODEL),
            cache_dir=meta("embedding_cache", str(default_cache_dir())),
            dimensions=int(meta("embedding_dimensions", str(DEFAULT_DIMENSIONS))),
        )

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def dimensions(self) -> int:
        return self.provider.dimensions

    def ready(self) -> bool:
        if not isinstance(self.provider, FastEmbedProvider):
            return True
        try:
            return self.db.get_meta("embedding_installed") == "1"
        except Exception:
            return False

    def install_model(self) -> dict[str, Any]:
        probe = self.provider.embed(["DynosAI semantic model installation probe"])[0]
        self.db.set_meta("embedding_model", self.model_name)
        self.db.set_meta("embedding_dimensions", str(self.dimensions))
        cache = getattr(self.provider, "cache_dir", default_cache_dir())
        self.db.set_meta("embedding_cache", str(cache))
        self.db.set_meta("embedding_installed", "1")
        self.db.set_meta("embedding_installed_at", utc_now())
        try:
            import fastembed
            provider_version=getattr(fastembed,"__version__","unknown")
        except Exception:
            provider_version="unknown"
        self.db.set_meta("embedding_provider", "fastembed")
        self.db.set_meta("embedding_provider_version", str(provider_version))
        self.db.set_meta("embedding_pooling", "mean")
        self.db.set_meta("embedding_profile", f"fastembed:{provider_version}:mean:{self.model_name}:{self.dimensions}")
        backend = self.db.ensure_vector_schema(self.dimensions)
        return {
            "installed": True,
            "model": self.model_name,
            "dimensions": int(probe.shape[0]),
            "cache": str(cache),
            "vector_backend": backend,
        }

    def status(self, probe: bool = False) -> dict[str, Any]:
        packages = {}
        for name in ("fastembed", "sqlite_vec"):
            try:
                module = __import__(name)
                packages[name] = {"installed": True, "version": getattr(module, "__version__", "unknown")}
            except Exception as exc:
                packages[name] = {"installed": False, "error": str(exc)}
        result = {
            "configured": self.db.get_meta("embedding_installed") == "1",
            "model": self.db.get_meta("embedding_model", self.model_name),
            "dimensions": int(self.db.get_meta("embedding_dimensions", str(self.dimensions)) or self.dimensions),
            "cache": self.db.get_meta("embedding_cache", str(default_cache_dir())),
            "packages": packages,
            "documents": int((self.db.one("SELECT COUNT(*) AS count FROM semantic_documents") or {"count": 0})["count"]),
            "vector_backend": self.db.vector_backend(),
            "profile": {
                "provider": self.db.get_meta("embedding_provider", "fastembed"),
                "provider_version": self.db.get_meta("embedding_provider_version", packages.get("fastembed",{}).get("version","unknown")),
                "pooling": self.db.get_meta("embedding_pooling", "mean"),
                "fingerprint": self.db.get_meta("embedding_profile", ""),
            },
        }
        if probe and packages["fastembed"]["installed"]:
            try:
                result["probe"] = {"ok": len(self.provider.embed(["DynosAI probe"])[0]) == self.dimensions}
            except Exception as exc:
                result["probe"] = {"ok": False, "error": str(exc)}
        return result

    def remove_model(self) -> dict[str, Any]:
        cache = Path(self.db.get_meta("embedding_cache", str(default_cache_dir()))).expanduser()
        removed = False
        # Only remove DynosAI's own cache root, never arbitrary parent directories.
        if cache.exists() and cache.name == "models" and "dynosai" in {part.lower() for part in cache.parts}:
            shutil.rmtree(cache)
            removed = True
        self.db.set_meta("embedding_installed", "0")
        return {"removed": removed, "cache": str(cache), "semantic_documents_preserved": True}

    def upsert(
        self,
        entity_type: str,
        entity_id: str,
        title: str,
        body: str,
        source_commit: str = "",
        status: str = "current",
    ) -> bool:
        normalized = self._semantic_text(entity_type, title, body)
        content_hash = sha256_text(normalized)
        existing = self.db.one(
            "SELECT id,content_hash,model,status,source_commit FROM semantic_documents WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        )
        if existing and existing["content_hash"] == content_hash and existing["model"] == self.model_name:
            if existing["status"] != status or existing["source_commit"] != source_commit:
                self.db.execute(
                    "UPDATE semantic_documents SET title=?,body=?,status=?,source_commit=?,last_verified_commit=?,indexed_at_commit=?,updated_at=? WHERE id=?",
                    (title, body, status, source_commit, source_commit, source_commit, utc_now(), existing["id"]),
                )
            return False
        vector = self.provider.embed([normalized])[0]
        self.db.upsert_semantic_document(
            entity_type=entity_type,
            entity_id=entity_id,
            title=title,
            body=body,
            content_hash=content_hash,
            model=self.model_name,
            dimensions=self.dimensions,
            source_commit=source_commit,
            status=status,
            vector=vector,
        )
        return True

    def upsert_many(self, documents: Iterable[dict[str, Any]], batch_size: int = 32, verification_commit: str | None = None) -> dict[str, int]:
        pending: list[dict[str, Any]] = []
        skipped = 0
        for document in documents:
            normalized = self._semantic_text(document["entity_type"], document["title"], document["body"])
            content_hash = sha256_text(normalized)
            existing = self.db.one(
                "SELECT id,content_hash,model,status,source_commit FROM semantic_documents WHERE entity_type=? AND entity_id=?",
                (document["entity_type"], document["entity_id"]),
            )
            if existing and existing["content_hash"] == content_hash and existing["model"] == self.model_name:
                desired_status = document.get("status", "current")
                desired_commit = document.get("source_commit", "")
                verified_commit = verification_commit if verification_commit is not None else desired_commit
                self.db.execute(
                    "UPDATE semantic_documents SET title=?,body=?,status=?,source_commit=?,last_verified_commit=?,indexed_at_commit=?,updated_at=? WHERE id=?",
                    (document["title"], document["body"], desired_status, desired_commit, verified_commit, verified_commit, utc_now(), existing["id"]),
                )
                skipped += 1
                continue
            item = dict(document)
            item["normalized"] = normalized
            item["content_hash"] = content_hash
            pending.append(item)
        indexed = 0
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = self.provider.embed([item["normalized"] for item in batch])
            for item, vector in zip(batch, vectors, strict=True):
                self.db.upsert_semantic_document(
                    entity_type=item["entity_type"],
                    entity_id=item["entity_id"],
                    title=item["title"],
                    body=item["body"],
                    content_hash=item["content_hash"],
                    model=self.model_name,
                    dimensions=self.dimensions,
                    source_commit=item.get("source_commit", ""),
                    status=item.get("status", "current"),
                    vector=vector,
                    verification_commit=verification_commit,
                )
                indexed += 1
        return {"indexed": indexed, "skipped": skipped, "total": indexed + skipped}

    def search(self, query: str, limit: int = 20, statuses: tuple[str, ...] = ("current", "validated", "approved", "inferred", "accepted", "completed", "done")) -> list[SemanticHit]:
        if not self.ready():
            return []
        vector = self.provider.embed([query])[0]
        rows = self.db.semantic_search(vector, limit=max(limit * 3, 20), model=self.model_name)
        hits: list[SemanticHit] = []
        for row in rows:
            if row["status"] not in statuses:
                continue
            distance = float(row["distance"])
            # Cosine distance is normally 0..2; keep a bounded intuitive relevance score.
            score = max(0.0, min(1.0, 1.0 - distance / 2.0))
            if row["status"] in {"inferred", "draft"}:
                score *= 0.75
            hits.append(SemanticHit(
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                title=row["title"],
                body=row["body"],
                score=score,
                distance=distance,
                model=row["model"],
                source_commit=row["source_commit"],
            ))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    def reindex_authoritative(self, source_commit: str = "", rebuild: bool = False) -> dict[str, Any]:
        if not self.ready():
            return {"enabled": False, "reason": "embedding model not installed", "indexed": 0, "skipped": 0, "deleted": 0}
        if rebuild:
            self.db.clear_semantic_documents()
        documents = list(self.authoritative_documents(source_commit))
        result = self.upsert_many(documents, verification_commit=source_commit)
        valid_keys = {(item["entity_type"], item["entity_id"]) for item in documents}
        deleted = self.db.delete_semantic_documents_not_in(valid_keys, self.model_name)
        self.db.set_meta("semantic_indexed_commit", source_commit)
        self.db.set_meta("semantic_indexed_at", utc_now())
        result.update({"deleted": deleted, "commit": source_commit, "model": self.model_name, "backend": self.db.vector_backend()})
        return result

    def authoritative_documents(self, source_commit: str = "") -> Iterable[dict[str, Any]]:
        """Yield small semantic units; never vectorize whole specs/plans as blobs."""
        project_name=self.db.get_meta("project_name","")
        if project_name:
            yield {"entity_type":"project","entity_id":"PROJECT","title":project_name,"body":f"Lenguaje: {self.db.get_meta('language','')}\nModo: {self.db.get_meta('mode','')}","source_commit":source_commit,"status":"current"}
        for row in self.db.query("SELECT id,title,description,state FROM work_items WHERE id!='PROJECT'"):
            yield {"entity_type":"work","entity_id":row["id"],"title":row["title"],"body":row["description"],"source_commit":source_commit,"status":row["state"]}
        for row in self.db.query("SELECT id,question,answer,status FROM decisions"):
            yield {"entity_type":"decision","entity_id":row["id"],"title":row["question"],"body":row["answer"],"source_commit":source_commit,"status":row["status"]}
        for row in self.db.query("SELECT id,kind,status,metadata FROM artifacts WHERE kind IN ('spec','plan')"):
            meta=json_loads(row.get("metadata"),{})
            if row["kind"]=="spec":
                for req in meta.get("requirements",[]):
                    yield {"entity_type":"requirement","entity_id":f"{row['id']}:{req.get('id')}","title":req.get("id","Requirement"),"body":str(req.get("text","")),"source_commit":source_commit,"status":row["status"]}
                for ac in meta.get("acceptance_criteria",[]):
                    yield {"entity_type":"acceptance","entity_id":f"{row['id']}:{ac.get('id')}","title":ac.get("id","Acceptance"),"body":str(ac.get("text","")),"source_commit":source_commit,"status":row["status"]}
                for rule in meta.get("business_rules",[]):
                    yield {"entity_type":"rule","entity_id":f"{row['id']}:{rule.get('id')}","title":rule.get("id","Rule"),"body":str(rule.get("text","")),"source_commit":source_commit,"status":row["status"]}
            else:
                approach=str(meta.get("approach","")).strip()
                if approach:
                    yield {"entity_type":"plan_delta","entity_id":f"{row['id']}:APPROACH","title":"Technical approach","body":approach,"source_commit":source_commit,"status":row["status"]}
                for idx,delta in enumerate(meta.get("architecture_delta",[]),1):
                    yield {"entity_type":"plan_delta","entity_id":f"{row['id']}:DELTA-{idx}","title":f"Architecture delta {idx}","body":str(delta),"source_commit":source_commit,"status":row["status"]}
        for row in self.db.query("SELECT id,title,description,state FROM tasks"):
            yield {"entity_type":"task","entity_id":row["id"],"title":row["title"],"body":row["description"],"source_commit":source_commit,"status":row["state"]}
        for row in self.db.query("SELECT id,title,summary,status,commit_hash FROM features"):
            yield {"entity_type":"feature","entity_id":row["id"],"title":row["title"],"body":row["summary"],"source_commit":row["commit_hash"],"status":row["status"]}
        for row in self.db.query("SELECT id,feature_id,rule FROM feature_rules"):
            yield {"entity_type":"rule","entity_id":f"{row['feature_id']}:RULE-{row['id']}","title":f"Regla de {row['feature_id']}","body":row["rule"],"source_commit":source_commit,"status":"validated"}
        for row in self.db.query("SELECT id,path,name,kind,signature,docstring,body,indexed_commit FROM symbols JOIN code_files USING(path)"):
            body=f"Archivo: {row['path']}\nTipo: {row['kind']}\nFirma: {row['signature']}\n{row['docstring']}\n{row['body'][:2500]}"
            yield {"entity_type":"symbol","entity_id":row["id"],"title":row["name"],"body":body,"source_commit":row.get("indexed_commit") or source_commit,"status":"current"}

    @staticmethod
    def _semantic_text(entity_type: str, title: str, body: str) -> str:
        return f"Tipo: {entity_type}\nTítulo: {title.strip()}\nContenido: {body.strip()}"[:12000]
