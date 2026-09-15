"""Tests for the contextual Thompson-sampling policy.

These lock down the four defects found in the audit:
  1. exploration collapsed to exactly one event for the process lifetime
     (`max(1, int(decisions * budget))`);
  2. the class was named Thompson but never sampled from Beta;
  3. arm identity drifted because each negotiation registered a fresh variant id,
     so no evidence ever accumulated;
  4. `_arms` grew without bound and the state file was rewritten on every reward.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contextual_bandit import (
    ArmState,
    ContextualThompsonBandit,
    context_features,
    derive_reward,
    stable_arm_id,
)


def _candidates():
    return [
        {"id": "intense", "name": "特种兵", "tags": ["紧凑"], "style": "intense"},
        {"id": "relaxed", "name": "慵懒", "tags": ["松弛"], "style": "relaxed"},
        {"id": "niche", "name": "小众", "tags": ["小众"], "style": "niche"},
    ]


class ContextualBanditTests(unittest.TestCase):
    def test_context_features_are_bounded_and_explicit(self):
        features = context_features({"pace": "慢", "budget_ratio": 9, "travelers": 100, "low_carbon": True})
        self.assertEqual(features["pace_slow"], 1.0)
        self.assertEqual(features["budget_ratio"], 2.0)
        self.assertEqual(features["travelers"], 5.0)
        self.assertEqual(features["low_carbon"], 1.0)

    def test_disabled_policy_exploits_and_respects_hard_constraints(self):
        policy = ContextualThompsonBandit(enabled=False, seed=3)
        result = policy.select(
            [{"id": "blocked", "score": 1}, {"id": "safe", "score": 0.5}],
            hard_constraint=lambda item: item["id"] == "safe",
        )
        self.assertEqual(result["selected"]["id"], "safe")
        self.assertFalse(result["explored"])
        self.assertEqual(result["reason"], "exploitation")

    def test_reward_validation_and_snapshot(self):
        policy = ContextualThompsonBandit(enabled=True, exploration_budget=0.1)
        policy.register("a")
        with self.assertRaises(ValueError):
            policy.record("a", 2)
        policy.record("a", 1)
        self.assertEqual(policy.snapshot()["arms"]["a"]["pulls"], 1)


class ThompsonSamplingTests(unittest.TestCase):
    """The algorithm must actually be Thompson sampling."""

    def test_uses_beta_sampling_not_posterior_mean(self):
        """A near-certain-good arm must still occasionally lose to a fresh one.

        Under a pure posterior-mean rule the well-observed arm wins every time.
        Thompson sampling draws from Beta, so the wide prior on the unobserved
        arm sometimes beats the narrow posterior of the proven arm.
        """
        policy = ContextualThompsonBandit(enabled=True, seed=7, max_non_greedy_rate=1.0)
        policy.register("proven")
        policy.register("fresh")
        for _ in range(30):
            policy.record("proven", 1.0)

        candidates = [{"id": "proven", "tags": []}, {"id": "fresh", "tags": []}]
        picks = {policy.select(candidates)["selected"]["id"] for _ in range(200)}
        self.assertEqual(picks, {"proven", "fresh"}, "Beta sampling should sometimes pick the unproven arm")

    def test_selection_is_deterministic_for_a_fixed_seed(self):
        def run():
            policy = ContextualThompsonBandit(enabled=True, seed=11, max_non_greedy_rate=0.2)
            picks = []
            for _ in range(40):
                picks.append(policy.select(_candidates())["selected"]["id"])
            return picks

        self.assertEqual(run(), run())

    def test_propensity_is_a_probability_not_a_constant(self):
        policy = ContextualThompsonBandit(enabled=True, seed=5)
        result = policy.select(_candidates())
        self.assertGreater(result["propensity"], 0.0)
        self.assertLessEqual(result["propensity"], 1.0)
        self.assertIn(result["propensity"], [i / 64 for i in range(65)], "propensity should come from the sampled win rate")


class ExplorationBudgetTests(unittest.TestCase):
    """Regression for `max(1, int(decisions * budget))` pinning exploration at 1."""

    def test_exploration_happens_more_than_once_over_many_decisions(self):
        policy = ContextualThompsonBandit(enabled=True, seed=13, max_non_greedy_rate=0.3)
        for _ in range(60):
            policy.select(_candidates())
        snapshot = policy.snapshot()
        self.assertGreater(
            snapshot["explorations"],
            1,
            "the old rule explored exactly once for the process lifetime",
        )

    def test_non_greedy_rate_respects_the_ceiling(self):
        policy = ContextualThompsonBandit(enabled=True, seed=17, max_non_greedy_rate=0.1)
        for _ in range(200):
            policy.select(_candidates())
        snapshot = policy.snapshot()
        # Allow a small margin for the ceiling being applied per decision.
        self.assertLessEqual(snapshot["observed_non_greedy_rate"], 0.2)

    def test_zero_budget_becomes_pure_exploitation(self):
        policy = ContextualThompsonBandit(enabled=True, seed=19, max_non_greedy_rate=0.0)
        for _ in range(50):
            policy.select(_candidates())
        self.assertEqual(policy.snapshot()["explorations"], 0)

    def test_single_candidate_never_counts_as_exploration(self):
        policy = ContextualThompsonBandit(enabled=True, seed=23)
        for _ in range(20):
            policy.select([{"id": "only", "tags": []}])
        snapshot = policy.snapshot()
        self.assertEqual(snapshot["explorations"], 0)
        self.assertEqual(snapshot["decisions"], 20)


class LearningAccumulatesTests(unittest.TestCase):
    """Regression for drifting arm identity: evidence must accumulate."""

    def test_stable_arm_id_is_reusable_across_negotiations(self):
        first = stable_arm_id("builtin-joint-v1", "intense")
        second = stable_arm_id("builtin-joint-v1", "INTENSE ")
        self.assertEqual(first, second, "style casing/whitespace must not fork the arm")
        self.assertEqual(first, "builtin-joint-v1:intense")
        self.assertEqual(stable_arm_id("", ""), "builtin-joint-v1:unspecified")

    def test_reward_moves_the_posterior_and_changes_future_selection(self):
        """A rewarded arm should dominate once the posterior has evidence."""
        policy = ContextualThompsonBandit(enabled=True, seed=29, max_non_greedy_rate=0.05)
        arm = stable_arm_id("v1", "relaxed")
        policy.register(arm, tags=["relaxed"])

        before = policy.snapshot()["arms"][arm]["posterior_mean"]
        for _ in range(10):
            # Re-registering must not reset accumulated evidence.
            policy.register(arm, tags=["relaxed"])
            policy.record(arm, 1.0)
        after = policy.snapshot()["arms"][arm]["posterior_mean"]

        self.assertEqual(before, 0.5)
        self.assertGreater(after, 0.9)

        candidates = [
            {"id": arm, "tags": ["relaxed"], "style": "relaxed"},
            {"id": stable_arm_id("v1", "intense"), "tags": ["intense"], "style": "intense"},
        ]
        wins = sum(1 for _ in range(40) if policy.select(candidates)["selected"]["id"] == arm)
        self.assertGreater(wins, 30, "the well-rewarded arm should be chosen most of the time")

    def test_registering_the_same_arm_twice_keeps_state(self):
        policy = ContextualThompsonBandit(enabled=True)
        policy.register("a", tags=["x"])
        policy.record("a", 1.0)
        policy.register("a", tags=["y"])
        state = policy.snapshot()["arms"]["a"]
        self.assertEqual(state["pulls"], 1)
        self.assertEqual(state["alpha"], 2.0)


class BoundedMemoryTests(unittest.TestCase):
    """Regression for unbounded `_arms` growth."""

    def test_arm_count_is_capped_and_weak_arms_are_evicted(self):
        policy = ContextualThompsonBandit(enabled=True, max_arms=10)
        for index in range(50):
            policy.select([{"id": f"arm-{index}", "tags": []}])
        self.assertLessEqual(policy.snapshot()["arm_count"], 10)
        self.assertGreater(policy.snapshot()["evicted_arms"], 0)

    def test_evicting_marks_state_but_keeps_evidenced_arms(self):
        policy = ContextualThompsonBandit(enabled=True, max_arms=3)
        for index in range(3):
            policy.register(f"kept-{index}")
            for _ in range(5):
                policy.record(f"kept-{index}", 1.0)
        # Overflow with minimal-evidence arms only.
        for index in range(6):
            policy.register(f"weak-{index}")
        snapshot = policy.snapshot()
        self.assertLessEqual(snapshot["arm_count"], 3)
        for index in range(3):
            self.assertIn(f"kept-{index}", snapshot["arms"], "arms with real evidence must survive eviction")


class PersistenceTests(unittest.TestCase):
    """The state file is the learning record — it must survive a restart."""

    def test_state_round_trips_through_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bandit_state.json")
            policy = ContextualThompsonBandit(enabled=True)
            policy.load(path)  # absent file: no-op, keeps the path unset
            policy.register("a", tags=["relaxed"])
            policy.record("a", 0.8)
            policy.save(path)

            restored = ContextualThompsonBandit(enabled=True)
            self.assertTrue(restored.load(path))
            self.assertEqual(restored.snapshot()["arms"]["a"]["pulls"], 1)
            # alpha = 1 + 0.8, beta = 1 + 0.2 -> posterior mean 1.8 / 3.0
            self.assertAlmostEqual(restored.snapshot()["arms"]["a"]["posterior_mean"], 1.8 / 3.0, places=5)

    def test_record_autosaves_once_a_state_file_is_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            policy = ContextualThompsonBandit(enabled=True)
            policy.save(path)  # seed the file
            self.assertTrue(policy.load(path))
            policy.register("a")
            policy.record("a", 1.0)
            self.assertTrue(os.path.exists(path))

            restored = ContextualThompsonBandit(enabled=True)
            restored.load(path)
            self.assertEqual(restored.snapshot()["arms"]["a"]["pulls"], 1, "autosave must persist the reward")

    def test_save_writes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            policy = ContextualThompsonBandit(enabled=True)
            policy.register("a")
            policy.save(path)
            self.assertFalse(os.path.exists(path + ".tmp"), "temp file must be renamed, not left behind")

    def test_arm_state_file_enables_autosave_on_a_first_ever_run(self):
        """A fresh deployment must persist rewards even though nothing existed.

        `load()` could only arm persistence when the file already existed, so the
        very first run recorded rewards in memory and lost them on restart.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fresh.json")
            self.assertFalse(os.path.exists(path))

            policy = ContextualThompsonBandit(enabled=True)
            self.assertFalse(policy.load(path), "nothing to load on a first run")
            policy.arm_state_file(path)
            policy.save(path)
            policy.register("a")
            policy.record("a", 1.0)
            self.assertTrue(os.path.exists(path), "autosave must write the state file")

            restored = ContextualThompsonBandit(enabled=True)
            self.assertTrue(restored.load(path))
            self.assertEqual(restored.snapshot()["arms"]["a"]["pulls"], 1)


class DeriveRewardTests(unittest.TestCase):
    """The reward gate is what keeps impressions out of the training signal."""

    def test_satisfaction_maps_linearly(self):
        self.assertEqual(derive_reward({"satisfaction": 5}), 1.0)
        self.assertEqual(derive_reward({"satisfaction": 0}), 0.0)
        self.assertEqual(derive_reward({"satisfaction": 4}), 0.8)

    def test_discrete_signals_map_to_ordered_rewards(self):
        self.assertGreater(derive_reward({"signal": "accepted"}), derive_reward({"signal": "swapped"}))
        self.assertGreater(derive_reward({"signal": "swapped"}), derive_reward({"signal": "rejected"}))

    def test_impressions_and_clicks_are_rejected(self):
        for signal in ({"signal": "impression"}, {"clicks": 5}, {}, None, {"signal": "unknown"}):
            with self.assertRaises(ValueError):
                derive_reward(signal)

    def test_out_of_range_values_are_rejected(self):
        with self.assertRaises(ValueError):
            derive_reward({"reward": 1.5})
        with self.assertRaises(ValueError):
            derive_reward({"satisfaction": 6})


class ArmStateTests(unittest.TestCase):
    def test_arm_state_defaults_are_uninformative(self):
        state = ArmState()
        self.assertEqual(state.alpha, 1.0)
        self.assertEqual(state.beta, 1.0)
        self.assertEqual(state.pulls, 0)


if __name__ == "__main__":
    unittest.main()
