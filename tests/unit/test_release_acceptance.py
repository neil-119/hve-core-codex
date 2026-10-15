from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hve_codex_porter.common import PortError, digest
from hve_codex_porter.release import validate_runtime_evidence
from hve_codex_porter.release_acceptance import verify_reviewed_acceptance
from test_release import _valid_runtime_evidence

ROOT = Path(__file__).resolve().parents[2]


def _review(tmp_path):
    policy = json.loads((ROOT / 'mappings/release-acceptance.json').read_text())
    for item in policy['historical_baseline']['reports']:
        target = tmp_path / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / item['path']).read_text(encoding='utf-8'), encoding='utf-8')
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(policy), encoding='utf-8')
    return path, policy, {'acceptance_policy_sha256': digest(path.read_bytes())}


def test_review_preserves_failed_blocked_counts_and_does_not_claim_fresh_execution(tmp_path):
    path, policy, envelope = _review(tmp_path)
    receipt = verify_reviewed_acceptance(path, tmp_path, envelope)
    assert receipt['accepted'] is True
    assert receipt['counts']['failed'] == 927
    assert receipt['counts']['blocked'] == 439
    assert receipt['fresh_candidate_full_suite'] is False
    assert 'fullSuiteExecution' not in receipt
    envelope['acceptance_policy_sha256'] = '0' * 64
    with pytest.raises(PortError, match='exact reviewed'):
        verify_reviewed_acceptance(path, tmp_path, envelope)


def test_review_rejects_tampered_report_and_unreconciled_counts(tmp_path):
    path, policy, envelope = _review(tmp_path)
    report = tmp_path / policy['historical_baseline']['reports'][0]['path']
    original = report.read_text(encoding='utf-8')
    report.write_text(original + 'changed', encoding='utf-8')
    with pytest.raises(PortError, match='hash mismatch'):
        verify_reviewed_acceptance(path, tmp_path, envelope)
    report.write_text(original, encoding='utf-8')
    policy['historical_baseline']['counts']['passed'] += 1
    path.write_text(json.dumps(policy), encoding='utf-8')
    envelope['acceptance_policy_sha256'] = digest(path.read_bytes())
    with pytest.raises(PortError, match='reconcile'):
        verify_reviewed_acceptance(path, tmp_path, envelope)


def test_review_does_not_waive_desktop_or_other_required_host_checks(tmp_path):
    path, _, envelope = _review(tmp_path)
    acceptance = verify_reviewed_acceptance(path, tmp_path, envelope)
    evidence = _valid_runtime_evidence()
    kwargs = {key: evidence[key] for key in ('source_sha', 'adapter_sha', 'converter_sha', 'mapping_sha256', 'scenario_definition_sha256', 'output_hashes')}
    evidence['scenarios'] = [row for row in evidence['scenarios'] if row['id'] != 'child-read-only-permission']
    assert validate_runtime_evidence(evidence, channel='stable', behavior_acceptance=acceptance, **kwargs) == []
    legacy = validate_runtime_evidence(evidence, channel='stable', **kwargs)
    assert any('full-suite' in item for item in legacy)
    missing_desktop = copy.deepcopy(evidence)
    missing_desktop['surfaces']['desktop']['result'] = 'pending'
    assert any('DESKTOP surface did not pass' in item for item in validate_runtime_evidence(missing_desktop, channel='stable', behavior_acceptance=acceptance, **kwargs))
    failed_hook = copy.deepcopy(evidence)
    failed_hook['scenarios'][2]['surfaces']['cli'] = 'failed'
    assert any('runtime scenarios did not pass' in item for item in validate_runtime_evidence(failed_hook, channel='stable', behavior_acceptance=acceptance, **kwargs))
