"""Conservative token-usage aggregation for Codex JSONL evaluation evidence.

Codex JSONL retains the raw event stream. This module only summarizes usage
objects exposed in ``turn.completed`` records; it does not infer usage from the
requested model or estimate credits/costs.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


_TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "cached_input_tokens": ("cached_input_tokens", "cached_tokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoning_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for target, names in aliases.items():
        for name in names:
            number = _count(value.get(name))
            if number is not None:
                counts[target] = number
                break
    input_details = value.get("input_tokens_details")
    if "cached_input_tokens" not in counts and isinstance(input_details, dict):
        number = _count(input_details.get("cached_tokens", input_details.get("cached_input_tokens")))
        if number is not None:
            counts["cached_input_tokens"] = number
    output_details = value.get("output_tokens_details")
    if "reasoning_output_tokens" not in counts and isinstance(output_details, dict):
        number = _count(output_details.get("reasoning_tokens"))
        if number is not None:
            counts["reasoning_output_tokens"] = number
    return counts


def _observed_model(event: dict[str, Any], usage: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        event.get("model"),
        event.get("model_slug"),
        event.get("model_name"),
        usage.get("model"),
        usage.get("model_slug"),
        usage.get("model_name"),
    ]
    for container_name in ("response", "info", "payload"):
        container = event.get(container_name)
        if isinstance(container, dict):
            candidates.extend((container.get("model"), container.get("model_slug"), container.get("model_name")))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def usage_observations(event: dict[str, Any], session_id: str | None) -> list[dict[str, Any]]:
    """Extract at most one usage observation from a completion event.

    Codex's explicit ``total_token_usage``/``total_usage`` fields and generic
    ``usage`` field on ``turn.completed`` are cumulative session snapshots.
    Prefer the explicit cumulative field when both are present so the same
    tokens are not represented twice. Turn-scoped observations are accepted by
    the summarizer only when a caller can establish that scope explicitly.
    """
    if event.get("type") != "turn.completed":
        return []
    cumulative_key = next(
        (key for key in ("total_token_usage", "total_usage", "session_total_usage") if isinstance(event.get(key), dict)),
        None,
    )
    usage = event.get(cumulative_key) if cumulative_key else event.get("usage")
    if not isinstance(usage, dict):
        return []
    counts = _usage_counts(usage)
    if not counts:
        return []
    return [{
        "session_id": session_id,
        "observed_model": _observed_model(event, usage),
        "accounting_mode": "cumulative-snapshot",
        "usage_source": cumulative_key or "usage",
        "tokens": counts,
    }]


def summarize_usage(
    observations: Iterable[dict[str, Any]],
    *,
    requested_model: str | None = None,
    requested_reasoning_effort: str | None = None,
    turns_completed: int | None = None,
) -> dict[str, Any]:
    """Summarize visible token counts, preferring cumulative snapshots per session.

    Codex's generic ``turn.completed.usage`` is cumulative across an exec/resume
    session. If a session exposes cumulative snapshots, any explicitly scoped
    turn observations for that same session are excluded. The latest snapshot
    is used once per session and attributed to a model only when every visible
    model label for that session agrees. This avoids counting the same resumed
    conversation repeatedly.
    """
    normalized = [item for item in observations if isinstance(item, dict)]
    cumulative_keys: set[str] = set()
    for position, item in enumerate(normalized):
        if item.get("accounting_mode") != "cumulative-snapshot":
            continue
        session_id = item.get("session_id")
        scenario_id = item.get("scenario_id")
        key = session_id if isinstance(session_id, str) and session_id else (
            f"scenario:{scenario_id}" if isinstance(scenario_id, str) and scenario_id else f"anonymous:{position}"
        )
        cumulative_keys.add(key)

    totals: dict[str | None, dict[str, int]] = defaultdict(lambda: {field: 0 for field in _TOKEN_FIELDS})
    cumulative_by_session: dict[str, list[tuple[str | None, dict[str, int]]]] = defaultdict(list)
    labels_by_session: dict[str, list[str | None]] = defaultdict(list)
    turn_observations = 0
    cumulative_observations = 0
    observed_models: set[str] = set()
    usage_without_model = 0
    accounting_anomalies: list[dict[str, Any]] = []

    for position, item in enumerate(normalized):
        model = item.get("observed_model") if isinstance(item.get("observed_model"), str) else None
        if model:
            observed_models.add(model)
        else:
            usage_without_model += 1
        session_id = item.get("session_id")
        mode = item.get("accounting_mode")
        tokens = item.get("tokens")
        if not isinstance(tokens, dict):
            continue
        counts = {field: number for field in _TOKEN_FIELDS if (number := _count(tokens.get(field))) is not None}
        if not counts:
            continue
        scenario_id = item.get("scenario_id")
        session_key = session_id if isinstance(session_id, str) and session_id else (
            f"scenario:{scenario_id}" if isinstance(scenario_id, str) and scenario_id else f"anonymous:{position}"
        )
        labels_by_session[session_key].append(model)
        if mode == "cumulative-snapshot":
            cumulative_observations += 1
            cumulative_by_session[session_key].append((model, counts))
        else:
            turn_observations += 1
            if session_key in cumulative_keys:
                continue
            for field, number in counts.items():
                totals[model][field] += number

    for session_key, snapshots in cumulative_by_session.items():
        # Preserve event order and take the latest total once for the session.
        # A decrease is reported as ambiguous instead of summing or keeping a
        # maximum that could conceal a reset/model change.
        model_labels = labels_by_session[session_key]
        unique_labels = {label for label in model_labels if label is not None}
        has_missing_label = any(label is None for label in model_labels)
        model = next(iter(unique_labels)) if len(unique_labels) == 1 and not has_missing_label else None
        if len(unique_labels) > 1 or has_missing_label:
            accounting_anomalies.append({
                "session_id": session_key.removeprefix("scenario:"),
                "code": "cumulative-model-attribution-ambiguous",
                "message": "Cumulative usage or related turn usage did not expose one consistent model label; totals are assigned to the unknown model bucket.",
            })
        latest = snapshots[-1][1]
        decreased_fields = sorted({
            field
            for _snapshot_model, prior in snapshots[:-1]
            for field in set(prior).intersection(latest)
            if latest[field] < prior[field]
        })
        if decreased_fields:
            accounting_anomalies.append({
                "session_id": session_key.removeprefix("scenario:"),
                "code": "cumulative-usage-decreased",
                "fields": decreased_fields,
                "message": "A later cumulative usage snapshot decreased. The latest snapshot is retained, but the totals may reflect a reset or incomplete event stream.",
            })
        counts = latest
        for field, number in counts.items():
            totals[model][field] += number

    token_totals = [dict(model=model, **counts) for model, counts in sorted(totals.items(), key=lambda item: item[0] or "")]
    any_usage = bool(token_totals)
    model_mismatch = bool(requested_model and observed_models and any(item != requested_model for item in observed_models))
    if model_mismatch:
        model_observation_status = "mismatch"
    elif any(item.get("code") == "cumulative-model-attribution-ambiguous" for item in accounting_anomalies):
        model_observation_status = "ambiguous"
    elif observed_models:
        model_observation_status = "match" if requested_model and observed_models == {requested_model} else "observed"
    else:
        model_observation_status = "unverified"

    return {
        "status": "partial" if any_usage else "unavailable",
        "complete": False,
        "requested_model": requested_model,
        "requested_reasoning_effort": requested_reasoning_effort,
        "model_observation_status": model_observation_status,
        "observed_models": sorted(observed_models),
        "usage_by_model": token_totals,
        "usage_observations": len(normalized),
        "turn_usage_observations": turn_observations,
        "cumulative_usage_observations": cumulative_observations,
        "usage_observations_without_model": usage_without_model,
        "turns_completed": turns_completed,
        "turns_without_usage": max(0, turns_completed - len(normalized)) if isinstance(turns_completed, int) else None,
        "cost_estimate": None,
        "delegated_model_verification": "unverified",
        "delegated_usage_verification": "unverified",
        "accounting_anomalies": accounting_anomalies,
        "limitations": [
            "Only usage fields exposed in retained turn.completed JSONL events are counted.",
            "The primary session model field does not independently verify the model used by delegated agents; staged custom-agent profiles and subagent defaults only record the requested configuration.",
            "Usage may omit delegated-agent or other provider-side activity.",
            "No credit or monetary cost is inferred; reconcile with account usage records for complete billing.",
        ],
    }
