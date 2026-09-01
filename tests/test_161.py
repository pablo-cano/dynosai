import unittest
from importlib import resources
from pathlib import Path


class DynosAI015StudioReviewabilityTests(unittest.TestCase):
    def test_approvals_expose_diff_validation_and_integrity(self):
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("approvals.diffTitle", app)
        self.assertIn("approvals.validationsTitle", app)
        self.assertIn("approvals.integrityTitle", app)
        self.assertIn("review-diff", app)
        self.assertIn("Resulting diff", i18n)
        self.assertIn("Diff resultante", i18n)

    def test_work_cards_expose_team_leases(self):
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("teamSlotsHtml", app)
        self.assertIn("work.teamTitle", i18n)
        self.assertIn("does not start extra agents", i18n)
        self.assertIn("no arranca agentes extra", i18n)

    def test_overview_exposes_eval_cases(self):
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("evalCasesHtml", app)
        self.assertIn("eval.title", i18n)
        self.assertIn("Predictive routing stays in shadow mode", i18n)
        self.assertIn("routing predictivo sigue en sombra", i18n)

    def test_settings_and_reviews_expose_execution_policy(self):
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("execution-profile", html)
        self.assertIn("review-policy", app)
        self.assertIn("approvals.policyTitle", i18n)
        self.assertIn("OS-level network enforcement is not shipped", i18n)
        self.assertIn("aplicación de red a nivel de sistema operativo no está incluida", i18n)
        self.assertIn("projectSettings.executionProfile", i18n)

    def test_overview_exposes_certified_provider_manifests(self):
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        self.assertIn("providerCapsHtml", app)
        self.assertIn("provider-capabilities", html)
        self.assertIn("caps.title", i18n)
        self.assertIn("Additional clients are not shipped", i18n)
        self.assertIn("clientes adicionales no están incluidos", i18n)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
