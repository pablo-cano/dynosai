import unittest
from importlib import resources
from pathlib import Path


class DynosAI141InteractionFlowTests(unittest.TestCase):
    def _asset(self, name: str) -> str:
        return resources.files("dynosai_flow.studio_assets").joinpath(name).read_text(encoding="utf-8")

    def test_overview_removes_duplicate_next_step_and_duplicate_projects_button(self):
        page = self._asset("index.html")
        overview = page.split('<section id="project-overview"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('id="next-action-card"', overview)
        app = self._asset("app.js")
        self.assertNotIn('function renderNextAction', app)
        self.assertIn('$("hero-actions").innerHTML = action !== "none"', app)
        self.assertNotIn('t("top.allProjects")}</button>`;', app)

    def test_project_navigation_is_locked_until_initialization(self):
        page = self._asset("index.html")
        for view in ("new-task", "work", "approvals"):
            self.assertIn(f'class="nav-item nested requires-initialized" data-view="{view}"', page)
        app = self._asset("app.js")
        self.assertIn('const initializedViews = ["new-task", "work", "approvals", ...technicalViews];', app)
        self.assertIn('item.disabled = disabled;', app)
        self.assertIn('if (initializedViews.includes(view) && hasProject() && !overview?.initialized) view = "project-overview";', app)
        self.assertIn('class="nav-item nested" data-view="checks"', page)
        self.assertIn('class="nav-item nested" data-view="project-settings"', page)

    def test_initialization_uses_global_busy_overlay_and_only_opens_checks_when_needed(self):
        page = self._asset("index.html")
        app = self._asset("app.js")
        self.assertIn('id="operation-overlay"', page)
        self.assertIn('withOperation("operation.initialize"', app)
        self.assertIn('const pendingChecks = candidates.some', app)
        self.assertIn('navigate(pendingChecks ? "checks" : "project-overview")', app)

    def test_mutating_actions_have_busy_feedback(self):
        app = self._asset("app.js")
        for key in (
            "operation.openProject",
            "operation.createProject",
            "operation.closeProject",
            "operation.removeProject",
            "operation.createFolder",
            "operation.initialize",
            "operation.approveChecks",
            "operation.createChange",
            "operation.review",
            "operation.saveSettings",
        ):
            self.assertIn(f'withOperation("{key}"', app)
        css = self._asset("styles.css")
        self.assertIn('.operation-overlay{position:fixed', css)
        self.assertIn('@keyframes dynosaiSpin', css)

    def test_new_change_contains_only_request_input_examples_and_submit(self):
        page = self._asset("index.html")
        section = page.split('<section id="new-task"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="work-description"', section)
        self.assertIn('id="start-work"', section)
        self.assertNotIn('new.step2Title', section)
        self.assertNotIn('new.step3Title', section)
        self.assertNotIn('data-combobox="provider"', section)
        self.assertNotIn('data-combobox="workspace"', section)
        self.assertNotIn('mini-flow', section)

    def test_new_changes_use_project_execution_settings(self):
        app = self._asset("app.js")
        self.assertIn('provider:projectPreference("provider", "codex")', app)
        self.assertIn('workspace_strategy:projectPreference("workspace", "interactive_branch")', app)
        self.assertNotIn('provider:comboValue("provider")', app)
        self.assertNotIn('workspace_strategy:comboValue("workspace")', app)

    def test_model_routes_follow_selected_project_agent_without_provider_tabs(self):
        page = self._asset("index.html")
        settings = page.split('<section id="project-settings"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="project-provider"', settings)
        self.assertIn('id="routing-provider-label"', settings)
        self.assertNotIn('data-routing-provider=', settings)
        app = self._asset("app.js")
        self.assertIn('routingProvider = event.target.value; loadModelRouting(routingProvider);', app)

    def test_workspace_choices_are_explained_in_project_settings(self):
        page = self._asset("index.html")
        self.assertIn('data-i18n="projectSettings.workspaceInteractiveHelp"', page)
        self.assertIn('data-i18n="projectSettings.workspaceIsolatedHelp"', page)
        i18n = self._asset("i18n.js")
        self.assertIn('"projectSettings.workspaceInteractiveHelp": "Work directly in the current Git branch."', i18n)
        self.assertIn('"projectSettings.workspaceInteractiveHelp": "Trabaja directamente en la rama Git actual."', i18n)

    def test_ready_new_change_hides_readiness_noise(self):
        app = self._asset("app.js")
        self.assertIn('note.className = "inline-note hidden";', app)
        self.assertIn('note.textContent = "";', app)

    def test_assets_stay_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
