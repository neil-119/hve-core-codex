"""Preserve the five-path host matcher contract independently of old evals."""
import json
from pathlib import Path

from hve_codex_porter.rules import match_rules


def test_five_path_scoped_instruction_contract(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "fixtures/scoped-rules-matrix.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    (tmp_path / "index.json").write_text(json.dumps(data["registry"]), encoding="utf-8")
    for path, content in data["contents"].items():
        (tmp_path / path).write_text(content, encoding="utf-8")
    actual = {
        path: [rule["id"] for rule in match_rules(tmp_path, [path])]
        for path in data["expected_by_path"]
    }
    assert actual == data["expected_by_path"]
