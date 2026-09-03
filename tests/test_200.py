import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.capability_manifests import (
    capability_report,
    load_extension_pack,
    provider_manifest,
    require_adapter,
)
from dynosai_flow.db import Database
from dynosai_flow.providers import MachineProviderSetup
from dynosai_flow.version import __version__


class DynosAI019CapabilityManifestTests(unittest.TestCase):
    def test_shipped_manifests_name_existing_transports(self):
        cursor = provider_manifest("cursor")
        self.assertEqual(cursor["adapter"], "cursor-acp")
        self.assertEqual(cursor["transport"], "acp")
        self.assertEqual(cursor["elicitation"], "studio_owned")
        self.assertTrue(cursor["certified"])
        self.assertEqual(cursor["human_gates"], "required")
        self.assertFalse(cursor["spawn_extra_providers"])
        codex = provider_manifest("openai")
        self.assertEqual(codex["provider"], "codex")
        self.assertEqual(codex["adapter"], "codex-app-server")
        self.assertEqual(codex["transport"], "app-server")
        self.assertEqual(codex["elicitation"], "wire")
        self.assertEqual(require_adapter("cursor-acp"), "cursor-acp")
        self.assertEqual(require_adapter("codex-app-server"), "codex-app-server")

    def test_uncertified_clients_and_packs_are_refused(self):
        with self.assertRaises(ValueError) as vscode:
            provider_manifest("vscode")
        self.assertIn("not shipped", str(vscode.exception))
        with self.assertRaises(ValueError) as windsurf:
            require_adapter("windsurf-acp")
        self.assertIn("not shipped", str(windsurf.exception))
        with self.assertRaises(ValueError) as pack:
            load_extension_pack("quality-pack")
        self.assertIn("not shipped", str(pack.exception))
        home = Path(tempfile.mkdtemp(prefix="dynosai-200-setup-"))
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        with self.assertRaises(ValueError):
            MachineProviderSetup(home).setup(["claude-code"], install_model=False)

    def test_overview_stats_and_bundle_expose_manifests(self):
        tmp = Path(tempfile.mkdtemp(prefix="dynosai-200-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        app = DynosAIApplication(tmp)
        app.initialize_project(name="Caps", allow_git_init=True)
        overview = app.project_overview()
        report = overview["provider_capabilities"]
        self.assertEqual(report["shipped_adapters"], ["cursor-acp", "codex-app-server"])
        self.assertFalse(report["extension_packs"])
        self.assertEqual(report["human_gates"], "required")
        dumped = json.dumps(report)
        self.assertIn("cursor-acp", dumped)
        self.assertIn("codex-app-server", dumped)
        stats = app.engine.stats()
        self.assertEqual(stats["capability_manifests"]["shipped_adapters"], ["cursor-acp", "codex-app-server"])
        bundle = app.engine.diagnostic_bundle()
        self.assertIn("capability_manifests.json", bundle["files"])
        self.assertEqual(capability_report()["certification"], "0.13.0-matrix")

    def test_schema_and_version_remain_governed(self):
        self.assertEqual(__version__, "1.0.0rc3")
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
