import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from dynosai_flow.version import __version__
from dynosai_flow.predictive_validation import PredictiveRouterValidator, observations_from_bundle
from dynosai_flow.cli import parser


def make_bundle(root: Path, name: str, events: list[dict], *, provider='codex', scenario='fibonacci', version='0.12.4') -> Path:
    path = root / name
    model_events=[]
    for event in events:
        model_events.append({
            'at': event.get('at','2026-08-25T00:00:00+00:00'),
            'event':'model_route_decision','provider':provider,'activity':'implementation','tier':event.get('tier','economy'),
            'complexity':{'level':event.get('complexity','simple')},
            'prediction':{'rules_tier':event.get('tier','economy')},
        })
        model_events.append(event)
    summary={'dynosai_version':version,'scenario':scenario,'providers':[provider],'status':'passed'}
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('suite/summary-final.json',json.dumps(summary))
        z.writestr(f'{provider}/{scenario}/project/.dynosai/runtime/model-control.jsonl','\n'.join(json.dumps(x) for x in model_events)+'\n')
    return path


class PredictiveValidation0125Tests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(__version__,'0.16.0')

    def test_cli_exposes_offline_history_validation(self):
        args=parser().parse_args(['model-control','validate-history','one.zip','two.zip','--output','report.json'])
        self.assertEqual(args.action,'validate-history')
        self.assertEqual(args.files,['one.zip','two.zip'])
        self.assertEqual(args.validation_output,'report.json')

    def test_legacy_success_repairs_old_failure_flag(self):
        with tempfile.TemporaryDirectory() as td:
            p=make_bundle(Path(td),'legacy.zip',[{
                'event':'model_outcome','provider':'codex','activity':'implementation','success':True,
                'counts_as_model_failure':True,'failure_kind':None,'tier':'economy'
            }],version='0.11.2')
            obs=observations_from_bundle(p)
            self.assertEqual(len(obs),1)
            self.assertTrue(obs[0].eligible_for_learning)
            self.assertFalse(obs[0].counts_as_model_failure)
            self.assertIn('success_invariant',obs[0].eligibility_source)

    def test_legacy_failure_without_explicit_eligibility_is_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            p=make_bundle(Path(td),'legacy-fail.zip',[{
                'event':'model_outcome','provider':'codex','activity':'implementation','success':False,
                'counts_as_model_failure':True,'failure_kind':'test','tier':'economy'
            }],version='0.12.0')
            obs=observations_from_bundle(p)
            self.assertFalse(obs[0].eligible_for_learning)
            report=PredictiveRouterValidator.validate([p])
            self.assertEqual(report['eligible_samples'],0)
            self.assertEqual(report['excluded_observations'],1)
            self.assertFalse(report['authority_ready'])

    def test_explicit_validation_precondition_never_trains(self):
        with tempfile.TemporaryDirectory() as td:
            p=make_bundle(Path(td),'precondition.zip',[{
                'event':'model_outcome','provider':'codex','activity':'implementation','success':False,
                'counts_as_model_failure':False,'eligible_for_learning':False,
                'failure_kind':'validation_precondition','tier':'economy'
            }])
            report=PredictiveRouterValidator.validate([p])
            self.assertEqual(report['eligible_samples'],0)
            self.assertTrue(report['authority_gates']['unsafe_observations_learned'])

    def test_chronological_replay_detects_false_escalation_after_failure_history(self):
        with tempfile.TemporaryDirectory() as td:
            events=[]
            # Four explicit real model failures build enough evidence for the next
            # observation to recommend a one-tier escalation. The fifth outcome
            # succeeds at economy, making that recommendation a false escalation.
            for i in range(4):
                events.append({'at':f'2026-08-25T00:00:0{i}+00:00','event':'model_outcome','provider':'codex','activity':'implementation','success':False,'counts_as_model_failure':True,'eligible_for_learning':True,'failure_kind':'test','tier':'economy'})
            events.append({'at':'2026-08-25T00:00:09+00:00','event':'model_outcome','provider':'codex','activity':'implementation','success':True,'counts_as_model_failure':False,'eligible_for_learning':True,'failure_kind':None,'tier':'economy'})
            p=make_bundle(Path(td),'failures.zip',events)
            report=PredictiveRouterValidator.validate([p])
            self.assertGreaterEqual(report['metrics']['escalation_recommendations'],1)
            self.assertGreaterEqual(report['metrics']['false_escalations'],1)
            self.assertFalse(report['authority_gates']['false_escalation_rate'])

    def test_no_counterfactual_outcome_is_invented(self):
        with tempfile.TemporaryDirectory() as td:
            events=[]
            # Strong success history at economy can recommend a downshift when a
            # later rules tier is standard. The lower-tier outcome for that later
            # work is unknown and must be reported as such.
            for i in range(8):
                events.append({'at':f'2026-08-25T00:00:{i:02d}+00:00','event':'model_outcome','provider':'codex','activity':'implementation','success':True,'counts_as_model_failure':False,'eligible_for_learning':True,'tier':'economy'})
            events.append({'at':'2026-08-25T00:01:00+00:00','event':'model_outcome','provider':'codex','activity':'implementation','success':True,'counts_as_model_failure':False,'eligible_for_learning':True,'tier':'standard','complexity':'medium'})
            p=make_bundle(Path(td),'downshift.zip',events)
            report=PredictiveRouterValidator.validate([p])
            self.assertGreaterEqual(report['metrics']['downshift_recommendations'],1)
            self.assertGreaterEqual(report['metrics']['counterfactual_unknown_downshifts'],1)
            self.assertIn('never fabricated', ' '.join(report['notes']).lower())

    def test_authority_requires_provider_scenario_failure_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            events=[{'at':f'2026-08-25T00:00:{i:02d}+00:00','event':'model_outcome','provider':'codex','activity':'implementation','success':True,'counts_as_model_failure':False,'eligible_for_learning':True,'tier':'economy'} for i in range(25)]
            p=make_bundle(Path(td),'many.zip',events)
            report=PredictiveRouterValidator.validate([p])
            self.assertTrue(report['authority_gates']['minimum_eligible_samples'])
            self.assertFalse(report['authority_gates']['provider_coverage'])
            self.assertFalse(report['authority_gates']['scenario_coverage'])
            self.assertFalse(report['authority_gates']['failure_evidence'])
            self.assertEqual(report['recommended_runtime_mode'],'shadow')


if __name__=='__main__': unittest.main()
