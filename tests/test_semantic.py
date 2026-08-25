from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dynosai_flow.engine import DynosAI


class TinySemanticProvider:
    """Deterministic test provider exercising the real vector-store/search path."""

    model_name = "dynosai/test-semantic"
    dimensions = 16

    GROUPS = {
        0: ("fibonacci", "serie", "secuencia", "términos", "terminos", "números", "numeros"),
        1: ("validar", "validación", "limitar", "límite", "máximo", "maximo", "entrada"),
        2: ("consola", "cli", "argumento", "comando"),
        3: ("prueba", "test", "tests", "unitaria"),
        4: ("token", "autenticación", "login", "credencial"),
    }

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        output = []
        for text in texts:
            lowered = text.lower()
            vector = np.zeros(self.dimensions, dtype=np.float32)
            for index, words in self.GROUPS.items():
                vector[index] = sum(lowered.count(word) for word in words)
            # Stable residual signal avoids zero vectors and exact ties.
            digest = hashlib.sha256(lowered.encode()).digest()
            for index in range(5, self.dimensions):
                vector[index] = digest[index] / 2550.0
            norm = float(np.linalg.norm(vector)) or 1.0
            output.append(vector / norm)
        return output


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="dynosai-semantic-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp, ignore_errors=True))
        external = self.temp.parent / ".dynosai-worktrees" / self.temp.name
        self.addCleanup(lambda: shutil.rmtree(external, ignore_errors=True))

    def test_neural_semantic_index_and_hybrid_retrieval(self):
        engine = DynosAI(self.temp, semantic_provider=TinySemanticProvider())
        result = engine.initialize("Semantic Project", test_command="python -m unittest")
        self.assertTrue(result["semantic"]["enabled"] if "enabled" in result["semantic"] else True)

        now = "2026-08-04T00:00:00+00:00"
        engine.db.execute(
            "INSERT INTO features(id,work_id,title,summary,status,commit_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("FEATURE-0001", "PROJECT", "Generación Fibonacci", "Produce una secuencia numérica desde una aplicación de consola", "validated", engine.git.head(), now, now),
        )
        engine.db.execute("INSERT INTO feature_rules(feature_id,rule) VALUES(?,?)", ("FEATURE-0001", "La entrada indica la cantidad de términos que debe generar la serie."))
        engine.db.execute(
            "INSERT INTO feature_files(feature_id,path,role,reason,confidence,content_hash,validity) VALUES(?,?,?,?,?,?,?)",
            ("FEATURE-0001", "src/cli.py", "implementation", "Valida el argumento de consola", 0.97, "", "current"),
        )
        first = engine.semantic.reindex_authoritative(engine.git.head())
        self.assertGreater(first["indexed"], 0)

        result = engine.ask("poner un máximo a la cantidad de números producidos")
        semantic_hits = [item for item in result["hits"] if item["category"] == "semantic"]
        self.assertTrue(semantic_hits)
        self.assertTrue(result["impact"]["features"])
        self.assertTrue(any(item["path"] == "src/cli.py" for item in result["impact"]["files"]))

        second = engine.semantic.reindex_authoritative(engine.git.head())
        self.assertEqual(second["indexed"], 0)
        self.assertGreater(second["skipped"], 0)


    def test_semantic_provenance_does_not_rewrite_last_changed_on_reverification(self):
        engine=DynosAI(self.temp,semantic_provider=TinySemanticProvider()); engine.initialize("Provenance")
        now="2026-08-12T00:00:00+00:00"; first_commit=engine.git.head()
        engine.db.execute("INSERT INTO features(id,work_id,title,summary,status,commit_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",("FEATURE-P","PROJECT","Auth token","Valida token de autenticación","validated",first_commit,now,now))
        engine.semantic.reindex_authoritative(first_commit)
        row1=engine.db.one("SELECT created_commit,last_changed_commit,last_verified_commit FROM semantic_documents WHERE entity_type='feature' AND entity_id='FEATURE-P'")
        marker=self.temp/"README.extra"; marker.write_text("metadata only\n"); engine.git._git("add","README.extra"); engine.git._git("commit","-m","docs: metadata change"); second_commit=engine.git.head()
        engine.semantic.reindex_authoritative(second_commit)
        row2=engine.db.one("SELECT created_commit,last_changed_commit,last_verified_commit,indexed_at_commit FROM semantic_documents WHERE entity_type='feature' AND entity_id='FEATURE-P'")
        self.assertEqual(row2["created_commit"],row1["created_commit"]); self.assertEqual(row2["last_changed_commit"],row1["last_changed_commit"]); self.assertEqual(row2["last_verified_commit"],second_commit); self.assertEqual(row2["indexed_at_commit"],second_commit)

    def test_vector_rows_are_authoritative_and_rebuildable(self):
        engine = DynosAI(self.temp, semantic_provider=TinySemanticProvider())
        engine.initialize("Rebuild")
        before = engine.db.one("SELECT COUNT(*) count FROM semantic_documents")["count"]
        result = engine.semantic_index(rebuild=True)
        after = engine.db.one("SELECT COUNT(*) count FROM semantic_documents")["count"]
        self.assertGreaterEqual(before, 1)
        self.assertEqual(before, after)
        self.assertEqual(result["semantic"]["backend"], "numpy")


    def test_no_model_fts_fallback_routes_natural_language_to_validated_feature_files(self):
        engine = DynosAI(self.temp)
        engine.initialize("Lexical fallback")
        work = engine.start("Crear una aplicación CLI que reciba N y muestre exactamente N términos de Fibonacci")
        now = "2026-08-12T00:00:00+00:00"
        engine.db.execute(
            "INSERT INTO features(id,work_id,title,summary,status,commit_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("FEATURE-LEX", work["id"], "Fibonacci CLI", "Generación de Fibonacci desde consola", "validated", engine.git.head(), now, now),
        )
        for path, role, symbol in [
            ("src/fibonacci.py", "implementation", "python://src.fibonacci/generate_fibonacci"),
            ("src/cli.py", "implementation", "python://src.cli/main"),
            ("tests/test_fibonacci.py", "test", "python://tests.test_fibonacci/test_five"),
        ]:
            engine.db.execute(
                "INSERT INTO feature_files(feature_id,path,role,reason,confidence,content_hash,symbol,validity) VALUES(?,?,?,?,?,?,?,?)",
                ("FEATURE-LEX", path, role, "Evidencia validada", 0.99, "", symbol, "current"),
            )
        first = engine.ask("¿Dónde se valida el número de términos de Fibonacci?")["impact"]
        cli = engine.ask("¿Qué archivo adapta Fibonacci a la consola CLI?")["impact"]
        tests = engine.ask("¿Qué tests cubren Fibonacci?")["impact"]
        self.assertEqual(first["files"][0]["path"], "src/fibonacci.py")
        self.assertEqual(cli["files"][0]["path"], "src/cli.py")
        self.assertEqual(tests["tests"][0]["path"], "tests/test_fibonacci.py")


if __name__ == "__main__":
    unittest.main()
