import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import _retryable_acceptance_infrastructure_failure
from dynosai_flow.version import __version__


class DynosAI129RC4RecoveredInfrastructureRetryTests(unittest.TestCase):
    def test_version_is_0129(self):
        self.assertEqual(__version__, "0.18.0")

    def _child(self, **updates):
        child={
            "status":"failed",
            "final_state":"plan_review",
            "provider_run":{"termination_reason":"idle_timeout"},
            "elicitations":[{"gate":"spec","status":"accepted"}],
            "scope_requests":[],
            "context_optimization":{"native_file_change_actions":0},
            "mcp_failures":0,
            "mcp_rejections":1,
            "mcp_rejection_recovery":{"total":1,"recovered":1,"unrecovered":0,"all_recovered":True},
        }
        child.update(updates)
        return child

    def test_observed_codex_brown_pattern_is_retryable(self):
        self.assertTrue(_retryable_acceptance_infrastructure_failure(self._child(),None,1))

    def test_recovered_rejection_does_not_allow_second_retry(self):
        self.assertFalse(_retryable_acceptance_infrastructure_failure(self._child(),None,2))

    def test_unrecovered_rejection_blocks_retry(self):
        child=self._child(mcp_rejection_recovery={"total":1,"recovered":0,"unrecovered":1,"all_recovered":False})
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_unresolved_gate_blocks_retry(self):
        child=self._child(elicitations=[{"gate":"spec","status":"pending"}])
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_scope_activity_blocks_retry(self):
        child=self._child(scope_requests=[{"id":"SCOPE-1","status":"declined"}])
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_native_edit_blocks_retry(self):
        child=self._child(context_optimization={"native_file_change_actions":1},final_state="implementation")
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_mcp_failure_blocks_retry(self):
        self.assertFalse(_retryable_acceptance_infrastructure_failure(self._child(mcp_failures=1),None,1))

    def test_post_implementation_timeout_blocks_retry_even_without_edits(self):
        self.assertFalse(_retryable_acceptance_infrastructure_failure(self._child(final_state="validation"),None,1))


if __name__ == "__main__":
    unittest.main()
