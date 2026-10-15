from __future__ import annotations

import hashlib
import json
import urllib.parse
from pathlib import Path

import pytest

from hve_codex_porter.release import (
    GitHubApi,
    ReleaseError,
    RUNTIME_SCENARIOS,
    RUNTIME_SURFACES,
    add_release_parser,
    build_release_manifest,
    copy_generated_outputs,
    create_release_manifest,
    discover_sources,
    empty_ledger,
    publication_eligibility,
    record_processed,
    refresh_release_manifest,
    render_checksums,
    sync_auto_merge_eligibility,
    validate_source_ref,
    validate_generated_paths,
    write_checksums,
    _trusted_mapping_hash,
    _trusted_scenario_hash,
)


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40
HASH_A = "1" * 64
HASH_B = "2" * 64
HASH_C = "3" * 64
HASH_D = "4" * 64


class FakeApi:
    def __init__(self, tags: list[dict], releases: list[dict], commits: dict[str, str] | None = None):
        self.tags = tags
        self.releases = releases
        self.commits = commits or {}
        self.tag_targets = {tag["name"]: tag["commit"]["sha"] for tag in tags}
        self.objects: dict[str, dict] = {}

    def list_all(self, path: str) -> list:
        if path.endswith("/tags"):
            return self.tags
        if path.endswith("/releases"):
            return self.releases
        raise AssertionError(f"unexpected paginated endpoint {path}")

    def get_json(self, path: str) -> dict:
        if path.endswith("/commits/main"):
            return {"sha": self.commits.get("main", SHA_D)}
        if "/git/ref/tags/" in path:
            encoded = path.rsplit("/git/ref/tags/", 1)[1]
            tag = urllib.parse.unquote(encoded)
            sha = self.tag_targets[tag]
            if tag.startswith("annotated-"):
                self.objects[sha] = {"object": {"sha": SHA_C, "type": "commit"}}
                return {"object": {"sha": sha, "type": "tag"}}
            return {"object": {"sha": sha, "type": "commit"}}
        if "/git/tags/" in path:
            sha = path.rsplit("/git/tags/", 1)[1]
            return self.objects[sha]
        raise AssertionError(f"unexpected API endpoint {path}")


def _tag(name: str, sha: str) -> dict:
    return {"name": name, "commit": {"sha": sha}}


def _release(tag: str, *, prerelease: bool = False, draft: bool = False, day: int = 1, release_id: int = 1) -> dict:
    return {
        "id": release_id,
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "published_at": f"2026-01-{day:02d}T12:00:00Z",
    }


def test_github_pagination_reads_every_full_page(monkeypatch):
    responses = [
        [{"name": f"tag-{number}"} for number in range(100)],
        [{"name": "tag-100"}],
    ]
    requested: list[str] = []

    class Response:
        def __init__(self, body: list):
            self.body = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return self.body

    def opener(request, timeout):
        requested.append(request.full_url)
        return Response(responses.pop(0))

    api = GitHubApi(api_base="https://api.example", opener=opener)
    results = api.list_all("/repos/microsoft/hve-core/tags")
    assert len(results) == 101
    assert "page=1" in requested[0]
    assert "page=2" in requested[1]


def test_release_discovery_is_paginated_pinned_and_skips_processed_sources():
    api = FakeApi(
        tags=[_tag("v1.0.0", SHA_A), _tag("v1.1.0", SHA_B), _tag("v1.2.0-rc.1", SHA_C)],
        releases=[
            _release("v1.1.0", day=3, release_id=3),
            _release("v1.2.0-rc.1", prerelease=True, day=4, release_id=4),
            _release("v1.0.0", day=1, release_id=1),
            _release("v9.9.9-draft", draft=True, day=2, release_id=2),
        ],
    )
    ledger = {"version": 1, "processed": [{"channel": "stable", "source_sha": SHA_A}], "tag_shas": {"v1.0.0": SHA_A}}

    stable = discover_sources("stable", api=api, ledger=ledger)
    prerelease = discover_sources("prerelease", api=api, ledger=ledger)

    assert [item["tag"] for item in stable] == ["v1.1.0"]
    assert stable[0]["source_sha"] == SHA_B
    assert prerelease[0]["tag"] == "v1.2.0-rc.1"
    assert prerelease[0]["prerelease"] is True


def test_stable_discovery_selects_only_the_latest_release_without_draining_backlog():
    api = FakeApi(
        tags=[_tag("v1.0.0", SHA_A), _tag("v1.1.0", SHA_B), _tag("v1.2.0", SHA_C)],
        releases=[
            _release("v1.0.0", day=1, release_id=1),
            _release("v1.1.0", day=2, release_id=2),
            _release("v1.2.0", day=3, release_id=3),
        ],
    )

    latest = discover_sources("stable", api=api)
    assert [item["tag"] for item in latest] == ["v1.2.0"]

    ledger = {
        "version": 1,
        "processed": [{"channel": "stable", "source_sha": SHA_C}],
        "tag_shas": {"v1.2.0": SHA_C},
    }
    assert discover_sources("stable", api=api, ledger=ledger) == []


def test_edge_discovery_pins_main_and_is_idempotent():
    api = FakeApi(tags=[], releases=[], commits={"main": SHA_D})
    assert discover_sources("edge", api=api) == [
        {
            "channel": "edge",
            "source_ref": "main",
            "source_sha": SHA_D,
            "tag": None,
            "published_at": None,
            "upstream_release_id": None,
            "upstream_version": None,
            "prerelease": False,
        }
    ]
    ledger = {"version": 1, "processed": [{"channel": "edge", "source_sha": SHA_D}], "tag_shas": {}}
    assert discover_sources("edge", api=api, ledger=ledger) == []

    old_edge_ledger = {"version": 1, "processed": [{"channel": "edge", "source_sha": SHA_A}], "tag_shas": {}}
    assert discover_sources("edge", api=api, ledger=old_edge_ledger)[0]["source_sha"] == SHA_D


def test_annotated_tag_is_resolved_to_its_commit_object():
    api = FakeApi(
        tags=[_tag("annotated-v1.0.0", SHA_A)],
        releases=[_release("annotated-v1.0.0")],
    )
    sources = discover_sources("stable", api=api)
    assert sources[0]["source_sha"] == SHA_C


def test_discovery_rejects_moved_or_deleted_processed_tags():
    moved = FakeApi(tags=[_tag("v1.0.0", SHA_B)], releases=[_release("v1.0.0")])
    ledger = {"version": 1, "processed": [], "tag_shas": {"v1.0.0": SHA_A}}
    with pytest.raises(ReleaseError, match="moved"):
        discover_sources("stable", api=moved, ledger=ledger)

    deleted = FakeApi(tags=[], releases=[])
    with pytest.raises(ReleaseError, match="disappeared"):
        discover_sources("stable", api=deleted, ledger=ledger)


def test_record_processed_is_idempotent_and_refuses_tag_reissue():
    initial = empty_ledger()
    first, changed = record_processed(
        initial,
        channel="stable",
        source_ref="v1.0.0",
        source_sha=SHA_A,
        tag="v1.0.0",
        outcome="pr-opened",
        timestamp="2026-01-01T00:00:00Z",
    )
    repeated, repeated_changed = record_processed(
        first,
        channel="stable",
        source_ref="v1.0.0",
        source_sha=SHA_A,
        tag="v1.0.0",
        outcome="pr-opened",
        timestamp="2026-01-02T00:00:00Z",
    )
    upgraded, upgraded_changed = record_processed(
        first,
        channel="stable",
        source_ref="v1.0.0",
        source_sha=SHA_A,
        tag="v1.0.0",
        outcome="merged",
    )

    assert changed is True
    assert repeated_changed is False
    assert repeated == first
    assert upgraded_changed is True
    assert upgraded["processed"][0]["outcome"] == "merged"
    with pytest.raises(ReleaseError, match="moved"):
        record_processed(
            first,
            channel="stable",
            source_ref="v1.0.0",
            source_sha=SHA_B,
            tag="v1.0.0",
            outcome="pr-opened",
        )


def test_source_refs_are_safe_and_token_is_limited_to_github_api():
    assert validate_source_ref("refs/tags/v3.2.2") == "refs/tags/v3.2.2"
    for unsafe in ("../main", "a//b", "tag@{1}", "main.lock", "refs/.hidden", "tag\\name", "tag\nname"):
        with pytest.raises(ReleaseError, match="safe Git ref"):
            validate_source_ref(unsafe)
    with pytest.raises(ReleaseError, match="only be sent"):
        GitHubApi(api_base="https://api.attacker.example", token="secret")

    class RedirectedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self) -> str:
            return "https://attacker.example/collect"

        def read(self) -> bytes:
            return b"{}"

    api = GitHubApi(token="secret", opener=lambda *_args, **_kwargs: RedirectedResponse())
    with pytest.raises(ReleaseError, match="redirect"):
        api.get_json("/repos/microsoft/hve-core/tags")


def test_refresh_manifest_preserves_build_identity_and_hashes_explicit_evidence(tmp_path: Path):
    package = tmp_path / "plugins" / "hve-core-codex"
    package.mkdir(parents=True)
    (package / "plugin.json").write_text("{}\n", encoding="utf-8")
    manifest = build_release_manifest(
        root=tmp_path,
        channel="stable",
        source_ref="v3.2.2",
        source_sha=SHA_A,
        upstream_manifest_version="3.2.2",
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        converter_version="0.1.0",
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
        generated_at="2026-01-01T00:00:00Z",
    )
    evidence = tmp_path / "generated" / "runtime-evidence.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"explicitly_invoked":true}\n', encoding="utf-8")

    refreshed = refresh_release_manifest(root=tmp_path, runtime_evidence_path=evidence)

    assert refreshed["source"] == manifest["source"]
    assert refreshed["converter"] == manifest["converter"]
    assert refreshed["adapter_sha"] == manifest["adapter_sha"]
    assert refreshed["generated_at"] == manifest["generated_at"]
    assert refreshed["upstream_prerelease"] is False
    assert refreshed["output_hashes"] == manifest["output_hashes"]
    assert refreshed["runtime_evidence_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert (tmp_path / "generated" / "checksums.txt").is_file()


def test_prerelease_manifest_records_upstream_prerelease_provenance():
    manifest = create_release_manifest(
        channel="prerelease",
        source_ref="hve-core-v3.3.0-rc.1",
        source_sha=SHA_A,
        upstream_manifest_version="3.3.0-rc.1",
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        converter_version="0.1.0",
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_B,
        scenario_definition_sha256=HASH_C,
        upstream_prerelease=True,
    )

    assert manifest["upstream_prerelease"] is True


def test_generated_paths_and_copy_are_allowlisted(tmp_path: Path):
    source = tmp_path / "converted"
    target = tmp_path / "repo"
    (source / "plugins" / "hve-core-codex").mkdir(parents=True)
    (source / "plugins" / "hve-core-codex" / "plugin.json").write_text("{}\n", encoding="utf-8")
    copied = copy_generated_outputs(source, target)
    assert copied["copied"] == ["plugins/hve-core-codex/plugin.json"]
    assert (target / "plugins" / "hve-core-codex" / "plugin.json").read_text() == "{}\n"

    assert validate_generated_paths(["generated/compatibility-report.json"]) == ["generated/compatibility-report.json"]
    for unsafe in ("../secrets.txt", ".github/workflows/publish.yml", "C:/secrets.txt", "generated\\escape.json"):
        with pytest.raises(ReleaseError):
            validate_generated_paths([unsafe])


def test_copy_rejects_reserved_or_symlink_outputs(tmp_path: Path):
    source = tmp_path / "converted"
    source.mkdir()
    (source / "generated").mkdir()
    (source / "generated" / "processed-sources.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="reserved"):
        copy_generated_outputs(source, tmp_path / "repo")
    (source / "generated" / "processed-sources.json").unlink()
    report_dir = source / "generated" / "runtime-evidence"
    report_dir.mkdir()
    (report_dir / "cli.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="reserved"):
        copy_generated_outputs(source, tmp_path / "repo")


def test_checksums_are_sorted_repeatable_and_exclude_volatile_metadata(tmp_path: Path):
    package = tmp_path / "plugins" / "hve-core-codex"
    package.mkdir(parents=True)
    (package / "plugin.json").write_text("{}\n", encoding="utf-8")
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "processed-sources.json").write_text('{"version":1}\n', encoding="utf-8")
    hashes = write_checksums(tmp_path)
    checksum_text = (generated / "checksums.txt").read_text(encoding="utf-8")

    assert list(hashes) == ["plugins/hve-core-codex/plugin.json"]
    assert checksum_text == render_checksums(hashes)
    assert write_checksums(tmp_path) == hashes


def test_sync_gate_hashes_trusted_mapping_inputs_and_scenario_definition(tmp_path: Path):
    mapping_root = tmp_path / "mappings"
    mapping_root.mkdir()
    (mapping_root / "z.yaml").write_bytes(b"Z")
    (mapping_root / "a.yaml").write_bytes(b"A")
    scenario_inputs = {
        "schemas/runtime-acceptance.json": b'{"version":2}\n',
        "tests/codex-host/stable.json": b'{"scope":"codex-host","channel":"stable"}\n',
        "tests/codex-host/edge.json": b'{"scope":"codex-host","channel":"edge"}\n',
    }
    for relative, data in scenario_inputs.items():
        scenario_path = tmp_path / relative
        scenario_path.parent.mkdir(parents=True, exist_ok=True)
        scenario_path.write_bytes(data)

    assert _trusted_mapping_hash(tmp_path) == hashlib.sha256(b"a.yamlAz.yamlZ").hexdigest()
    entries = [[relative, hashlib.sha256(data).hexdigest()] for relative, data in scenario_inputs.items()]
    expected = hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
    assert _trusted_scenario_hash(tmp_path) == expected

    (tmp_path / "tests/codex-host/edge.json").write_bytes(b'{"scope":"codex-host","channel":"edge","change":true}\n')
    assert _trusted_scenario_hash(tmp_path) != expected


def _valid_report() -> dict:
    return {
        "upstream_sha": SHA_A,
        "adapter_revision": SHA_B,
        "mapping_hash": HASH_A,
        "release_blockers": [{"code": "runtime-evidence-missing"}],
        "unknown_fields": [],
        "unresolved_references": [],
        "coverage": {"inventory": "complete", "structural": "passed", "behavioral": "not-run"},
        "counts": {
            kind: {"discovered": 1, "represented": 1, "validated": 0}
            for kind in ("skills", "agents", "instructions", "prompts")
        },
        "artifacts": [],
    }


def _valid_runtime_evidence() -> dict:
    surfaces = {}
    for surface, version, evidence_hash in (
        ("cli", "Codex CLI 1.0.0-test", HASH_C),
        ("desktop", "Codex Desktop 1.0.0-test", HASH_D),
    ):
        surfaces[surface] = {
            "version": version,
            "result": "passed",
            "evidence_path": f"generated/runtime-evidence/{surface}.json",
            "evidence_sha256": evidence_hash,
            "human_review": {
                "reviewer": "reviewer@example.test",
                "reviewed_at": "2026-01-01T00:00:00Z",
                "result": "passed",
                "scenario_ids": list(RUNTIME_SCENARIOS),
                "scenario_definition_sha256": HASH_B,
                "evidence_sha256": evidence_hash,
            },
        }
    return {
        "schema_version": 2,
        "explicitly_invoked": True,
        "result": "passed",
        "codex_version": "1.0.0-test",
        "source_sha": SHA_A,
        "adapter_sha": SHA_B,
        "converter_sha": SHA_C,
        "mapping_sha256": HASH_A,
        "scenario_definition_sha256": HASH_B,
        "surfaces": surfaces,
        "output_hashes": {
            "plugins/hve-core-codex/plugin.json": HASH_A,
            "generated/runtime-evidence/cli.json": HASH_C,
            "generated/runtime-evidence/desktop.json": HASH_D,
        },
        "scenarios": [
            {"id": scenario, "surfaces": {surface: "passed" for surface in RUNTIME_SURFACES}}
            for scenario in RUNTIME_SCENARIOS
        ],
    }


def _sync_inventory(*, source_sha: str, source_ref: str) -> dict:
    return {
        "channel": "stable",
        "upstream_sha": source_sha,
        "upstream_ref": source_ref,
        "files": [
            {
                "path": "LICENSE",
                "hash": HASH_C,
                "status": "converted",
                "classification": "packaged-runtime-resource",
                "rationale": "Included license",
                "outputs": ["plugins/hve-core-codex/resources/LICENSE"],
            }
        ],
        "artifacts": [
            {
                "id": "skill:sample/SKILL.md",
                "source": "skills/sample/SKILL.md",
                "hash": HASH_A,
                "kind": "skill",
                "status": "adapted",
                "frontmatter": {"name": "sample", "license": "MIT"},
                "user_invocable": True,
                "disable_model_invocation": False,
                "dependencies": [],
                "references": [],
                "required_tools": [],
                "required_capabilities": [],
                "outputs": ["plugins/hve-core-codex/skills/sample/SKILL.md"],
            }
        ],
    }


def test_sync_auto_merge_requires_explicit_runtime_evidence_and_known_transformations():
    report = _valid_report()
    report.update({"upstream_ref": "hve-core-v3.2.2", "adapter_revision": SHA_B})
    candidate_inventory = _sync_inventory(source_sha=SHA_A, source_ref="hve-core-v3.2.2")
    baseline_inventory = _sync_inventory(source_sha=SHA_D, source_ref="hve-core-v3.2.1")
    evidence = _valid_runtime_evidence()
    evidence["converter_sha"] = SHA_B
    hashes = evidence["output_hashes"]
    manifest = {
        "schema_version": 1,
        "channel": "stable",
        "source": {
            "repository": "microsoft/hve-core",
            "ref": "hve-core-v3.2.2",
            "sha": SHA_A,
        },
        "adapter_sha": SHA_B,
        "converter": {"sha": SHA_B},
        "mapping_sha256": HASH_A,
        "scenario_definition_sha256": HASH_B,
        "runtime_evidence_sha256": HASH_D,
        "output_hashes": hashes,
    }
    parameters = {
        "channel": "stable",
        "compatibility_report": report,
        "release_manifest": manifest,
        "runtime_evidence": evidence,
        "runtime_evidence_sha256": HASH_D,
        "output_hashes": hashes,
        "checksums_text": render_checksums(hashes),
        "trusted_mapping_sha256": HASH_A,
        "trusted_scenario_definition_sha256": HASH_B,
        "baseline_inventory": baseline_inventory,
        "candidate_inventory": candidate_inventory,
        "expected_source_sha": SHA_A,
        "expected_adapter_revision": SHA_B,
        "source_recorded": True,
        "vally_verification": {"verified": True},
    }

    eligible = sync_auto_merge_eligibility(**parameters)
    assert eligible["eligible"] is True, eligible["blockers"]
    assert eligible["action"] == "schedule-auto-merge"

    no_independent_proof = sync_auto_merge_eligibility(**{**parameters, "vally_verification": None})
    assert no_independent_proof["eligible"] is False
    assert any("independent file-backed full-suite Vally verification" in blocker for blocker in no_independent_proof["blockers"])

    stale_checksums = sync_auto_merge_eligibility(**{**parameters, "checksums_text": "stale\n"})
    assert stale_checksums["eligible"] is False
    assert any("checksums file does not exactly match" in blocker for blocker in stale_checksums["blockers"])

    stale_mapping = sync_auto_merge_eligibility(**{**parameters, "trusted_mapping_sha256": HASH_D})
    assert stale_mapping["eligible"] is False
    assert any("mapping hash does not match the protected baseline" in blocker for blocker in stale_mapping["blockers"])

    stale_scenarios = sync_auto_merge_eligibility(
        **{**parameters, "trusted_scenario_definition_sha256": HASH_D}
    )
    assert stale_scenarios["eligible"] is False
    assert any("scenario-definition hash does not match the protected baseline" in blocker for blocker in stale_scenarios["blockers"])

    no_evidence = sync_auto_merge_eligibility(**{**parameters, "runtime_evidence": None, "runtime_evidence_sha256": None})
    assert no_evidence["eligible"] is False
    assert any("requires explicitly recorded" in blocker for blocker in no_evidence["blockers"])

    unrecorded = sync_auto_merge_eligibility(**{**parameters, "source_recorded": False})
    assert unrecorded["eligible"] is False
    assert any("ledger does not record" in blocker for blocker in unrecorded["blockers"])

    new_artifact = _sync_inventory(source_sha=SHA_A, source_ref="hve-core-v3.2.2")
    new_artifact["artifacts"].append({"id": "agent:new", "kind": "agent", "status": "adapted"})
    changed_license = _sync_inventory(source_sha=SHA_A, source_ref="hve-core-v3.2.2")
    changed_license["artifacts"][0]["frontmatter"]["license"] = "CC-BY-SA-4.0"
    for candidate, expected in ((new_artifact, "added artifact"), (changed_license, "frontmatter")):
        blocked = sync_auto_merge_eligibility(**{**parameters, "candidate_inventory": candidate})
        assert blocked["eligible"] is False
        assert any(expected in blocker for blocker in blocked["blockers"])

    changed_license_metadata = _sync_inventory(source_sha=SHA_A, source_ref="hve-core-v3.2.2")
    changed_license_metadata["artifacts"][0]["license_metadata"] = {"spdx": "MIT"}
    blocked = sync_auto_merge_eligibility(**{**parameters, "candidate_inventory": changed_license_metadata})
    assert blocked["eligible"] is False
    assert any("license_metadata" in blocker for blocker in blocked["blockers"])

    changed_notice = _sync_inventory(source_sha=SHA_A, source_ref="hve-core-v3.2.2")
    changed_notice["files"][0]["hash"] = HASH_B
    blocked = sync_auto_merge_eligibility(**{**parameters, "candidate_inventory": changed_notice})
    assert blocked["eligible"] is False
    assert any("license, copyright, or third-party notice" in blocker for blocker in blocked["blockers"])

    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        baseline_root = Path(temporary) / "baseline"
        candidate_root = Path(temporary) / "candidate"
        output_path = Path("plugins/hve-core-codex/skills/sample/SKILL.md")
        for root in (baseline_root, candidate_root):
            output = root / output_path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("Ask the user for approval before taking action.\n", encoding="utf-8")
        changed_body = _sync_inventory(source_sha=SHA_A, source_ref="hve-core-v3.2.2")
        changed_body["artifacts"][0]["hash"] = HASH_B
        blocked = sync_auto_merge_eligibility(
            **{
                **parameters,
                "candidate_inventory": changed_body,
                "baseline_root": baseline_root,
                "candidate_root": candidate_root,
            }
        )
        assert blocked["eligible"] is False
        assert any("human-gate context" in blocker for blocker in blocked["blockers"])

    manual_review = dict(report)
    manual_review["release_blockers"] = [{"code": "security-review-required", "message": "Review required."}]
    blocked = sync_auto_merge_eligibility(**{**parameters, "compatibility_report": manual_review})
    assert blocked["eligible"] is False
    assert any("security-review-required" in blocker for blocker in blocked["blockers"])


def test_stable_eligibility_requires_matching_explicit_runtime_evidence_and_merge():
    parameters = {
        "channel": "stable",
        "compatibility_report": _valid_report(),
        "source_sha": SHA_A,
        "adapter_sha": SHA_B,
        "converter_sha": SHA_C,
        "mapping_sha256": HASH_A,
        "scenario_definition_sha256": HASH_B,
        "output_hashes": _valid_runtime_evidence()["output_hashes"],
        "runtime_evidence_sha256": "e" * 64,
        "merged_commit": SHA_D,
        "protected_branch": True,
    }
    blocked = publication_eligibility(**parameters)
    assert blocked["eligible"] is False
    assert any("requires runtime evaluation evidence" in blocker for blocker in blocked["blockers"])

    not_invoked = _valid_runtime_evidence()
    not_invoked["explicitly_invoked"] = False
    denied_for_no_invocation = publication_eligibility(
        **parameters, runtime_evidence=not_invoked, vally_verification={"verified": True}
    )
    assert any("not explicitly invoked" in blocker for blocker in denied_for_no_invocation["blockers"])

    no_independent_proof = publication_eligibility(**parameters, runtime_evidence=_valid_runtime_evidence())
    assert no_independent_proof["eligible"] is False
    assert any("independent file-backed full-suite Vally verification" in blocker for blocker in no_independent_proof["blockers"])

    fabricated_envelope_claim = _valid_runtime_evidence()
    fabricated_envelope_claim["vally_verification"] = {"verified": True}
    denied_fabricated_claim = publication_eligibility(**parameters, runtime_evidence=fabricated_envelope_claim)
    assert denied_fabricated_claim["eligible"] is False
    assert any("independent file-backed full-suite Vally verification" in blocker for blocker in denied_fabricated_claim["blockers"])

    legacy_schema = _valid_runtime_evidence()
    legacy_schema["schema_version"] = 1
    denied_legacy_schema = publication_eligibility(
        **parameters,
        runtime_evidence=legacy_schema,
        vally_verification={"verified": True},
    )
    assert denied_legacy_schema["eligible"] is False
    assert any("acceptance contract version 2" in blocker for blocker in denied_legacy_schema["blockers"])

    independent_verification = {"verified": True}
    eligible = publication_eligibility(
        **parameters,
        runtime_evidence=_valid_runtime_evidence(),
        vally_verification=independent_verification,
    )
    assert eligible["eligible"] is True
    assert eligible["publication"] == "stable-release"
    assert not any("runtime-evidence-missing" in blocker for blocker in eligible["blockers"])

    mismatched = _valid_runtime_evidence()
    mismatched["source_sha"] = SHA_D
    denied = publication_eligibility(**parameters, runtime_evidence=mismatched, vally_verification=independent_verification)
    assert denied["eligible"] is False
    assert any("source_sha does not match" in blocker for blocker in denied["blockers"])

    missing_review = _valid_runtime_evidence()
    del missing_review["surfaces"]["cli"]["human_review"]
    denied = publication_eligibility(**parameters, runtime_evidence=missing_review, vally_verification=independent_verification)
    assert denied["eligible"] is False
    assert any("missing reviewer-authored CLI rubric approval" in blocker for blocker in denied["blockers"])

    pending_review = _valid_runtime_evidence()
    pending_review["surfaces"]["desktop"]["human_review"]["result"] = "manual-review-pending"
    denied = publication_eligibility(**parameters, runtime_evidence=pending_review, vally_verification=independent_verification)
    assert denied["eligible"] is False
    assert any("DESKTOP human rubric review is not passed" in blocker for blocker in denied["blockers"])

    mismatched_review_hash = _valid_runtime_evidence()
    mismatched_review_hash["surfaces"]["cli"]["human_review"]["evidence_sha256"] = HASH_D
    denied = publication_eligibility(**parameters, runtime_evidence=mismatched_review_hash, vally_verification=independent_verification)
    assert denied["eligible"] is False
    assert any("CLI human review is not bound" in blocker for blocker in denied["blockers"])

    mismatched_review_scenarios = _valid_runtime_evidence()
    mismatched_review_scenarios["surfaces"]["desktop"]["human_review"]["scenario_ids"] = list(RUNTIME_SCENARIOS[:-1])
    denied = publication_eligibility(
        **parameters,
        runtime_evidence=mismatched_review_scenarios,
        vally_verification=independent_verification,
    )
    assert denied["eligible"] is False
    assert any("must attest every required host check" in blocker for blocker in denied["blockers"])


def test_prerelease_requires_opt_in_and_edge_never_becomes_stable():
    prerelease = publication_eligibility(
        channel="prerelease",
        compatibility_report=_valid_report(),
        source_sha=SHA_A,
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
        upstream_prerelease=True,
        merged_commit=SHA_D,
    )
    assert prerelease["eligible"] is False
    assert any("explicit opt-in" in blocker for blocker in prerelease["blockers"])

    opted_in_without_protection = publication_eligibility(
        channel="prerelease",
        compatibility_report=_valid_report(),
        source_sha=SHA_A,
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
        upstream_prerelease=True,
        explicitly_opted_in=True,
        merged_commit=SHA_D,
    )
    assert opted_in_without_protection["eligible"] is False
    assert any("protected release branch" in blocker for blocker in opted_in_without_protection["blockers"])

    opted_in = publication_eligibility(
        channel="prerelease",
        compatibility_report=_valid_report(),
        source_sha=SHA_A,
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
        upstream_prerelease=True,
        explicitly_opted_in=True,
        merged_commit=SHA_D,
        protected_branch=True,
    )
    assert opted_in["eligible"] is True

    edge_blocked = publication_eligibility(
        channel="edge",
        compatibility_report=_valid_report(),
        source_sha=SHA_A,
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
    )
    assert edge_blocked["eligible"] is False
    assert edge_blocked["status"] == "blocked"

    edge = publication_eligibility(
        channel="edge",
        compatibility_report=_valid_report(),
        source_sha=SHA_A,
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
        explicitly_opted_in=True,
        merged_commit=SHA_D,
        protected_branch=True,
    )
    assert edge["eligible"] is True
    assert edge["status"] == "development-snapshot-only"
    assert edge["publication"] == "edge-development-snapshot"

    structurally_pending = _valid_report()
    structurally_pending["coverage"]["structural"] = "pending"
    blocked_snapshot = publication_eligibility(
        channel="edge",
        compatibility_report=structurally_pending,
        source_sha=SHA_A,
        adapter_sha=SHA_B,
        converter_sha=SHA_C,
        output_hashes={"plugins/hve-core-codex/plugin.json": HASH_A},
        mapping_sha256=HASH_A,
        scenario_definition_sha256=HASH_B,
        explicitly_opted_in=True,
        merged_commit=SHA_D,
        protected_branch=True,
    )
    assert any("structural validation is not passed" in blocker for blocker in blocked_snapshot["blockers"])


def test_release_parser_registers_documented_subcommands():
    import argparse

    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers()
    add_release_parser(subparsers)
    args = root.parse_args(["release", "discover", "--channel", "stable", "--limit", "1"])
    assert args.release_command == "discover"
    assert args.limit == 1
    sync_args = root.parse_args(
        [
            "release",
            "sync-eligibility",
            "--channel",
            "stable",
            "--baseline-root",
            ".",
            "--expected-source-sha",
            SHA_A,
            "--expected-adapter-revision",
            SHA_B,
        ]
    )
    assert sync_args.release_command == "sync-eligibility"

def test_stable_discovery_resolves_only_latest_unprocessed_release():
    class TrackingApi(FakeApi):
        def __init__(self):
            super().__init__(tags=[_tag('v1', SHA_A), _tag('v2', SHA_B)],
                             releases=[_release('v1', day=1), _release('v2', day=2)])
            self.resolved_tags = []

        def get_json(self, path):
            if '/git/ref/tags/' in path:
                self.resolved_tags.append(path.rsplit('/', 1)[-1])
            return super().get_json(path)

    api = TrackingApi()
    result = discover_sources('stable', api=api)
    assert result[0]['source_sha'] == SHA_B
    assert api.resolved_tags == ['v2']


def test_source_acquisition_forwards_ci_read_token(tmp_path, monkeypatch):
    from hve_codex_porter.inventory import acquire
    from hve_codex_porter.common import PortError
    observed = []

    def discover(**kwargs):
        observed.append(kwargs)
        return []

    monkeypatch.setenv('GH_TOKEN', 'fixture-read-token')
    monkeypatch.setattr('hve_codex_porter.release.discover_sources', discover)
    with pytest.raises(PortError, match='No published'):
        acquire('https://github.com/microsoft/hve-core.git', None, None, 'stable', tmp_path)
    assert observed[0]['token'] == 'fixture-read-token'
