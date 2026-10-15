"""Source invocation metadata remains usable without inventing a Codex picker field."""
from hve_codex_porter.common import frontmatter
from hve_codex_porter.skill_converter import render_skill


def test_source_skill_hint_preserves_exact_arguments_and_original_body():
    hint = "scope=... mode={build|refresh|report} [baseUrl=...]"
    source_body = "# Coverage\n\nAsk the user to choose a scope.\n"
    rendered = render_skill("coverage", "Build coverage", source_body, {"argument-hint": hint})
    fields, body = frontmatter(rendered, "SKILL.md")
    assert "argument-hint" not in fields
    assert source_body.rstrip() in body
    assert f"## Invocation arguments\n\n{hint}\n" in body
    assert body.count(hint) == 1


def test_absent_or_empty_skill_hint_does_not_add_an_arguments_section():
    for metadata in (None, {}, {"argument-hint": ""}):
        rendered = render_skill("coverage", "Build coverage", "# Coverage\n", metadata)
        assert "## Invocation arguments" not in rendered
