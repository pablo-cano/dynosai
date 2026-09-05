import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.acceptance import RealProviderAcceptanceSuite, _retryable_acceptance_infrastructure_failure
from dynosai_flow.version import __version__


class DynosAI128RC3AcceptanceRetryTests(unittest.TestCase):
    def test_version_is_0128(self):
        self.assertEqual(__version__, "1.0.0rc7")

    def _child(self, **updates):
        child={
            "status":"failed",
            "final_state":"inbox",
            "provider_run":{"termination_reason":"idle_timeout"},
            "elicitations":[],
            "scope_requests":[],
            "context_optimization":{"native_file_change_actions":0},
            "mcp_failures":0,
            "mcp_rejections":0,
        }
        child.update(updates)
        return child

    def test_side_effect_free_idle_timeout_gets_one_retry(self):
        self.assertTrue(_retryable_acceptance_infrastructure_failure(self._child(),None,1))
        self.assertFalse(_retryable_acceptance_infrastructure_failure(self._child(),None,2))

    def test_parent_worker_timeout_gets_one_retry(self):
        child=self._child(provider_run={"termination_reason":None})
        self.assertTrue(_retryable_acceptance_infrastructure_failure(child,{"type":"WorkerTimeout"},1))

    def test_functional_failure_never_retries(self):
        child=self._child(provider_run={"termination_reason":"provider_exit"},final_state="validation")
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_timeout_after_edit_never_retries(self):
        child=self._child(context_optimization={"native_file_change_actions":1},final_state="implementation")
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_timeout_after_unresolved_governance_interaction_never_retries(self):
        child=self._child(elicitations=[{"gate":"spec","status":"pending"}],final_state="spec_review")
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_mcp_failure_never_retries(self):
        child=self._child(mcp_failures=1)
        self.assertFalse(_retryable_acceptance_infrastructure_failure(child,None,1))

    def test_suite_retries_idle_timeout_once_and_preserves_both_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            attempts={}

            def child(provider,scenario,passed):
                mode="greenfield" if scenario=="fibonacci" else "brownfield"
                gates={
                    "provider_process_ok":passed,"final_state_done":passed,"quality_100":passed,
                    "single_provider_session":True,"provider_session_completed":passed,"same_workspace":True,
                    "human_gates_resolved":passed,"expected_human_interactions_only":passed,
                    "zero_scope_requests":True,"validation_baseline_correct":True,"runtime_bin_unchanged":True,
                    "provider_model_route_verified":True,"token_usage_exact":True,"elicitation_trace_present":passed,
                    "oracle_passed":passed,"zero_mcp_failures":True,"zero_mcp_rejections":True,
                    "zero_mcp_normalizations":True,"strict_managed_runtime_verified":True,
                    "zero_provider_extension_artifacts":True,"zero_external_skill_invocations":True,
                    "zero_external_plugin_invocations":True,"zero_external_mcp_invocations":True,
                }
                return {
                    "provider":provider,"scenario":scenario,"mode":mode,"interaction_mode":"auto",
                    "status":"passed" if passed else "failed","final_state":"done" if passed else "inbox",
                    "quality_score":100 if passed else 50,"oracle":{"passed":passed},"gates":gates,
                    "provider_run":{"real_provider":True,"wire_elicitation":provider=="codex",
                        "automated_responses":True,"driver":"fake","exit_code":0 if passed else 1,
                        "termination_reason":"provider_exit" if passed else "idle_timeout"},
                    "elicitations":[{"gate":"spec"}] if passed else [],"scope_requests":[],
                    "mcp_failures":0,"mcp_rejections":0,"mcp_normalizations":0,
                    "provider_extension_artifacts":0,"external_skill_invocations":0,
                    "external_plugin_invocations":0,"external_mcp_invocations":0,
                    "token_usage":{"quality":"exact","input_tokens":100,"cached_input_tokens":50,
                        "cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":1,
                        "context_read_tokens":100,"generated_tokens":10,"processed_tokens":110,
                        "cost_estimate":{"estimated_list_price_usd":0.01}},
                    "runtime_metrics":{"workflow_elapsed_seconds":1.0,"dynosai_mcp_seconds":0.1,"mcp_calls":2,
                        "reconnect_count":0},
                    "context_optimization":{"native_file_change_actions":0 if not passed else 1},
                }

            class Probe:
                def __init__(self,p): self.provider=p
                def to_dict(self): return {"provider":self.provider,"available":True}

            class FakePopen:
                def __init__(self,cmd,*args,**kwargs):
                    provider=cmd[cmd.index("--provider")+1]; scenario=cmd[cmd.index("--scenario")+1]
                    base=Path(cmd[cmd.index("--base")+1])
                    key=(provider,scenario); attempts[key]=attempts.get(key,0)+1
                    passed=not (key==("codex","fibonacci") and attempts[key]==1)
                    summary=base/provider/scenario/"logs"/"summary.json"
                    summary.parent.mkdir(parents=True,exist_ok=True)
                    summary.write_text(json.dumps(child(provider,scenario,passed)),encoding="utf-8")
                    self.returncode=0 if passed else 1; self.pid=12345
                def poll(self): return self.returncode
                def terminate(self): self.returncode=1
                def kill(self): self.returncode=1
                def wait(self,timeout=None): return self.returncode

            suite=RealProviderAcceptanceSuite(
                providers=("codex","cursor"),scenario="green-brown",
                output=root/"matrix.zip",workspace=root/"workspace",log_dir=root/"logs",
                configure=False,heartbeat=999,
            )
            with patch("dynosai_flow.acceptance.probe_provider",side_effect=lambda p:Probe(p)), \
                 patch("dynosai_flow.acceptance.subprocess.Popen",FakePopen):
                result=suite.run()
            self.assertEqual(result["status"],"passed")
            self.assertEqual(result["retry_metrics"]["infrastructure_retries"],1)
            self.assertEqual(result["retry_metrics"]["recovered_cases"],1)
            self.assertEqual(result["retry_metrics"]["total_attempts"],5)
            self.assertEqual(result["token_usage"]["totals"]["processes"],4)
            self.assertEqual(result["token_usage"]["actual_totals_including_retries"]["processes"],5)
            self.assertTrue((root/"workspace"/"attempts"/"codex"/"fibonacci"/"attempt-1"/"logs"/"summary.json").exists())
            self.assertTrue((root/"matrix.zip").exists())


if __name__ == "__main__":
    unittest.main()
