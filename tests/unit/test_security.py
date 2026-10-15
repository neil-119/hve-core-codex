from hve_codex_porter.security import credential_findings


def test_secret_diagnostics_never_expose_values(tmp_path):
    token = "ghp_" + "Ab1c" * 9
    path = tmp_path / "data.txt"
    path.write_text(token)
    findings = credential_findings(tmp_path, [path])
    assert len(findings) == 1
    assert token not in str(findings)
    assert "github-token" in findings[0]


def test_documented_placeholder_is_not_a_real_token(tmp_path):
    path = tmp_path / "README.md"
    path.write_text("GITHUB_TOKEN=<configured-by-user>; ghp_EXAMPLE")
    assert credential_findings(tmp_path, [path]) == []


def test_reviewed_fixture_exemption_is_bound_to_all_file_bytes(tmp_path, monkeypatch):
    from hve_codex_porter.common import digest
    data = ("ghp_" + "Ab1c" * 9).encode()
    path = tmp_path / "fixture.py"
    path.write_bytes(data)
    monkeypatch.setattr("hve_codex_porter.security.load_reviewed_fixtures", lambda: {digest(data): {"github-token"}})
    assert credential_findings(tmp_path, [path]) == []
    path.write_bytes(data + b" changed")
    assert len(credential_findings(tmp_path, [path])) == 1
