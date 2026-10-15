from __future__ import annotations

import copy

import pytest

from hve_codex_porter.release import ReleaseError, weekly_publication_decision


@pytest.mark.parametrize("channel", ["stable", "edge"])
def test_weekly_publish_only_latest_unpublished_source(channel):
    sha = "a" * 40
    ref = "hve-core-v3.2.2" if channel == "stable" else "main"
    source = {"channel": channel, "source_sha": sha, "source_ref": ref}
    manifest = {"channel": channel, "source": {"sha": sha, "ref": ref}, "upstream_manifest_version": "3.2.2"}
    assert weekly_publication_decision(channel, manifest, source, [])["publish"]
    assert not weekly_publication_decision(channel, None, source, [])["publish"]
    old = copy.deepcopy(manifest)
    old["source"]["sha"] = "b" * 40
    assert not weekly_publication_decision(channel, old, source, [])["publish"]
    prefix = "hve-core-v3.2.2" if channel == "stable" else f"hve-core-edge-{sha}"
    published = {"tag_name": f"{prefix}-codex.9", "draft": False, "prerelease": channel == "edge"}
    assert not weekly_publication_decision(channel, manifest, source, [published])["publish"]
    draft = {**published, "draft": True}
    assert weekly_publication_decision(channel, manifest, source, [draft])["publish"]
    wrong_status = {**published, "prerelease": channel != "edge"}
    with pytest.raises(ReleaseError, match="wrong release channel"):
        weekly_publication_decision(channel, manifest, source, [wrong_status])


def test_weekly_rejects_prerelease_wrong_channel_and_unpinned_source():
    source = {"channel": "stable", "source_sha": "a" * 40, "source_ref": "v3.2.2"}
    manifest = {"channel": "stable", "source": {"sha": "a" * 40, "ref": "v3.2.2"}, "upstream_manifest_version": "3.2.2"}
    with pytest.raises(ReleaseError, match="stable and edge only"):
        weekly_publication_decision("prerelease", manifest, source, [])
    with pytest.raises(ReleaseError, match="wrong channel"):
        weekly_publication_decision("edge", manifest, source, [])
    with pytest.raises(ReleaseError):
        weekly_publication_decision("stable", manifest, {**source, "source_sha": "HEAD"}, [])
    with pytest.raises(ReleaseError, match="non-prerelease"):
        weekly_publication_decision("stable", {**manifest, "upstream_manifest_version": "3.3.0-rc.1"}, source, [])
