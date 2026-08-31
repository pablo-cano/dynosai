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

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
