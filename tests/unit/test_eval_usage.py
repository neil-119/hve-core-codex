from __future__ import annotations

import sys
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.eval_usage import summarize_usage, usage_observations  # noqa: E402


class EvalUsageTests(unittest.TestCase):
    def test_generic_turn_completed_usage_is_cumulative_and_extracts_token_details(self) -> None:
        event = {
            "type": "turn.completed",
            "model": "gpt-6-luna",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 30,
                "input_tokens_details": {"cached_tokens": 40},
                "output_tokens_details": {"reasoning_tokens": 12},
            },
        }
        observation = usage_observations(event, "session-1")[0]
        summary = summarize_usage([observation], requested_model="gpt-6-luna", turns_completed=1)

        self.assertEqual(observation["accounting_mode"], "cumulative-snapshot")
        self.assertEqual(summary["usage_by_model"], [{
            "model": "gpt-6-luna",
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "output_tokens": 30,
            "reasoning_output_tokens": 12,
            "total_tokens": 0,
        }])
        self.assertEqual(summary["model_observation_status"], "match")
        self.assertEqual(summary["cost_estimate"], None)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["delegated_model_verification"], "unverified")

    def test_codex_exec_resume_pilot_cumulative_snapshots_are_not_double_counted(self) -> None:
        events = [
            {
                "type": "turn.completed",
                "model": "gpt-6-luna",
                "usage": {
                    "input_tokens": 543685,
                    "cached_input_tokens": 475136,
                    "output_tokens": 18320,
                },
            },
            {
                "type": "turn.completed",
                "model": "gpt-6-luna",
                "usage": {
                    "input_tokens": 686200,
                    "cached_input_tokens": 608768,
                    "output_tokens": 23545,
                },
            },
        ]
        observations = [usage_observations(event, "pilot-session")[0] for event in events]

        summary = summarize_usage(
            observations,
            requested_model="gpt-6-luna",
            turns_completed=2,
        )

        self.assertEqual([item["accounting_mode"] for item in observations], [
            "cumulative-snapshot",
            "cumulative-snapshot",
        ])
        self.assertEqual(summary["usage_by_model"], [{
            "model": "gpt-6-luna",
            "input_tokens": 686200,
            "cached_input_tokens": 608768,
            "output_tokens": 23545,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
        }])
        self.assertEqual(summary["cumulative_usage_observations"], 2)
        self.assertEqual(summary["turn_usage_observations"], 0)

    def test_resume_cumulative_totals_are_counted_once_using_latest_snapshot(self) -> None:
        observations = [
            {"session_id": "session-1", "observed_model": "gpt-6-luna", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 100, "output_tokens": 20}},
            {"session_id": "session-1", "observed_model": "gpt-6-luna", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 145, "output_tokens": 35}},
        ]
        summary = summarize_usage(observations, requested_model="gpt-6-luna", turns_completed=2)

        self.assertEqual(summary["usage_by_model"][0]["input_tokens"], 145)
        self.assertEqual(summary["usage_by_model"][0]["output_tokens"], 35)
        self.assertEqual(summary["cumulative_usage_observations"], 2)
        self.assertEqual(summary["usage_observations"], 2)

    def test_unknown_then_labeled_cumulative_resume_stays_in_unknown_model_bucket(self) -> None:
        observations = [
            {"session_id": "session-1", "observed_model": None, "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 10, "output_tokens": 2}},
            {"session_id": "session-1", "observed_model": "gpt-6-luna", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 18, "output_tokens": 5}},
        ]
        summary = summarize_usage(observations, requested_model="gpt-6-luna")

        self.assertIsNone(summary["usage_by_model"][0]["model"])
        self.assertEqual(summary["usage_by_model"][0]["input_tokens"], 18)
        self.assertEqual(summary["model_observation_status"], "ambiguous")
        self.assertIn("cumulative-model-attribution-ambiguous", {item["code"] for item in summary["accounting_anomalies"]})

    def test_model_switch_during_cumulative_resume_uses_unknown_bucket_and_reports_mismatch(self) -> None:
        observations = [
            {"session_id": "session-1", "observed_model": "gpt-6-luna", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 10, "output_tokens": 2}},
            {"session_id": "session-1", "observed_model": "gpt-6-sol", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 18, "output_tokens": 5}},
        ]
        summary = summarize_usage(observations, requested_model="gpt-6-luna")

        self.assertIsNone(summary["usage_by_model"][0]["model"])
        self.assertEqual(summary["usage_by_model"][0]["input_tokens"], 18)
        self.assertEqual(summary["model_observation_status"], "mismatch")
        self.assertEqual(summary["observed_models"], ["gpt-6-luna", "gpt-6-sol"])

    def test_decreasing_cumulative_snapshot_is_reported_instead_of_maximized(self) -> None:
        observations = [
            {"session_id": "session-1", "observed_model": "gpt-6-luna", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 20, "output_tokens": 5}},
            {"session_id": "session-1", "observed_model": "gpt-6-luna", "accounting_mode": "cumulative-snapshot", "tokens": {"input_tokens": 4, "output_tokens": 1}},
        ]
        summary = summarize_usage(observations, requested_model="gpt-6-luna")

        self.assertEqual(summary["usage_by_model"][0]["input_tokens"], 4)
        self.assertIn("cumulative-usage-decreased", {item["code"] for item in summary["accounting_anomalies"]})


if __name__ == "__main__":
    unittest.main()
