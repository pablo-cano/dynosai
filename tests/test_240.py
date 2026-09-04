import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI, create_server
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.db import Database
from dynosai_flow.mcp import LEGACY_TOOLS, TOOLS
from dynosai_flow.version import DISPLAY_VERSION, __version__


ROOT = Path(__file__).resolve().parents[1]
SENTINEL_WORK = "DYN-0190"


def _write_019_state(project: Path) -> None:
    """Representative 0.19.0 schema-v6 project state (not a live 0.19 install)."""
    dynosai = project / ".dynosai"
    dynosai.mkdir(parents=True, exist_ok=True)
    path = dynosai / "knowledge.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE work_items (
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
            INSERT INTO meta(key,value) VALUES('schema_version','6');
            INSERT INTO meta(key,value) VALUES('project_name','Legacy019');
            INSERT INTO meta(key,value) VALUES('origin_release','0.19.0');
            INSERT INTO work_items(
              id,title,description,work_type,state,quick_flow,active,branch,worktree,
              quality_score,workspace_strategy,provider_session_id,original_branch,created_at,updated_at
            ) VALUES(
              'DYN-0190','Preserve me','upgrade sentinel','feature','inbox',0,1,NULL,NULL,
              0,'interactive_branch',NULL,NULL,'2026-08-31T00:00:00Z','2026-08-31T00:00:00Z'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


class DynosAI240Rc4InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-240-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _app(self, name="InstallRc4"):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name=name, allow_git_init=True)
        return app

    def test_application_doctor_reuses_engine_checks_without_rewrite(self):
        app = self._app()
        wrapped = app.doctor()
        direct = app.engine.doctor()
        self.assertIn("ready", wrapped)
        self.assertIn("checks", wrapped)
        self.assertEqual(wrapped["checks"], direct["checks"])
        self.assertEqual(wrapped["ready"], direct["ready"])
        deep = app.doctor(deep=True)
        self.assertIn("deep_checks", deep)
        self.assertIn("mcp_worktree_handshake", deep["checks"])

    def test_studio_api_exposes_doctor_and_deep_doctor(self):
        hub = StudioAPI(None, registry_path=self.tmp / "registry.json")
        code, body = hub.get("/api/doctor")
        self.assertEqual(code, 200)
        self.assertEqual(body.get("scope"), "machine")
        self.assertIn("checks", body)
        self.assertNotIn("deep_checks", body)

        app = self._app()
        api = StudioAPI(self.tmp, registry_path=self.tmp / "registry.json")
        code, body = api.get("/api/doctor")
        self.assertEqual(code, 200)
        self.assertEqual(body["checks"], app.engine.doctor()["checks"])
        code, deep = api.get("/api/doctor", {"deep": ["1"]})
        self.assertEqual(code, 200)
        self.assertIn("deep_checks", deep)

    def test_studio_assets_expose_doctor_ui_in_english_and_spanish(self):
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        script = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn('id="doctor"', html)
        self.assertIn("data-doctor-run", html)
        self.assertIn("loadDoctor", script)
        self.assertIn("doctorHtml", script)
        self.assertIn("dataset.doctorRun", script)
        self.assertIn("doctor.title", i18n)
        self.assertIn("Run doctor", i18n)
        self.assertIn("Run deep doctor", i18n)
        self.assertIn("Ejecutar doctor", i18n)
        self.assertIn("Ejecutar doctor profundo", i18n)

    def test_docs_and_install_script_use_verified_local_wheel_not_pypi(self):
        getting_started = (ROOT / "GETTING_STARTED.md").read_text(encoding="utf-8")
        release = (ROOT / "docs" / "RELEASE_1.0.0-rc.4.md").read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "install_local_wheel.py").read_text(encoding="utf-8")
        self.assertIn("SHA256SUMS", getting_started)
        self.assertIn("install_local_wheel.py", getting_started)
        self.assertIn("not published on PyPI", getting_started)
        self.assertNotIn("curl |", getting_started)
        self.assertNotIn("curl|", getting_started.replace(" ", ""))
        self.assertNotRegex(getting_started, r"pip install dynosai-flow(?!\S)")
        self.assertIn("SHA256", installer)
        self.assertIn("pypi.org", installer.lower())
        blob = getting_started + release + installer
        self.assertNotIn("pip install dynosai-flow==", blob)
        self.assertNotIn("https://pypi.org/project/dynosai", blob)

    def test_install_local_wheel_requires_matching_checksum(self):
        wheel = self.tmp / "dynosai_flow-1.0.0rc4-py3-none-any.whl"
        wheel.write_bytes(b"not-a-real-wheel")
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        sums = self.tmp / "SHA256SUMS.txt"
        sums.write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
        script = ROOT / "scripts" / "install_local_wheel.py"
        probe = subprocess.run(
            [sys.executable, str(script), "--wheel", str(wheel), "--sums", str(sums), "--verify-only"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        sums.write_text("0" * 64 + f"  {wheel.name}\n", encoding="utf-8")
        bad = subprocess.run(
            [sys.executable, str(script), "--wheel", str(wheel), "--sums", str(sums), "--verify-only"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(bad.returncode, 0)

    def test_ci_keeps_linux_python_matrix_and_adds_cross_os_wheel_smoke(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn('python-version: ["3.11", "3.12", "3.13", "3.14"]', ci)
        self.assertIn("ubuntu-latest", ci)
        self.assertIn("windows-latest", ci)
        self.assertIn("macos-latest", ci)
        self.assertIn("installed-wheel", ci)
        self.assertIn("smoke_installed_package.py", ci)
        self.assertIn("ci_installed_wheel.py", ci)
        python_job, _, rest = ci.partition("installed-wheel")
        self.assertIn("3.14", python_job)
        self.assertNotIn("3.14", rest.split("web:")[0] if "web:" in rest else rest)

    def test_smoke_script_refuses_checkout_src_import(self):
        script = ROOT / "scripts" / "smoke_installed_package.py"
        self.assertTrue(script.exists())
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
        )
        combined = (result.stdout + result.stderr).lower()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue("checkout" in combined or "src/" in combined or "installed-package" in combined)

    def test_upgrade_from_019_state_preserves_schema_v6_and_work(self):
        project = self.tmp / "legacy019"
        project.mkdir()
        _write_019_state(project)
        db = Database(project)
        db.initialize()
        self.assertEqual(db.get_meta("schema_version"), "6")
        self.assertEqual(db.get_meta("origin_release"), "0.19.0")
        row = db.one("SELECT id,title,state FROM work_items WHERE id=?", (SENTINEL_WORK,))
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Preserve me")
        self.assertEqual(row["state"], "inbox")
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)

    def test_mcp_schema_version_and_loopback_health(self):
        names = {str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}
        self.assertEqual(len(names), 31)
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)
        self.assertEqual(__version__, "1.0.0rc6")
        self.assertEqual(DISPLAY_VERSION, "1.0.0-rc.6")
        server = create_server(self.tmp, port=0)
        host, port = server.server_address[:2]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=3) as response:
            health = json.loads(response.read().decode("utf-8"))
        self.assertTrue(health["ok"])
        self.assertEqual(health["version"], "1.0.0rc6")
        with urllib.request.urlopen(f"http://{host}:{port}/index.html", timeout=3) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="doctor"', page)


if __name__ == "__main__":
    unittest.main()
