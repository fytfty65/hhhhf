"""Safety-gated contextual exploration for mature planning datasets.

This module is deliberately framework-free and disabled by default at the
service boundary. It accepts only explicit rewards (0..1), keeps propensity
for counterfactual evaluation, and never bypasses a caller's hard-constraint
filter. It is suitable for shadow/canary experiments, not unsupervised
online personalization.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence


def context_features(context: Mapping[str, Any] | None) -> Dict[str, float]:
    context = context or {}
    features: Dict[str, float] = {}
    pace = str(context.get("pace") or context.get("soft_pace") or "").lower()
    features["pace_slow"] = 1.0 if pace in {"slow", "relaxed", "慢", "舒缓"} else 0.0
    features["pace_fast"] = 1.0 if pace in {"fast", "紧凑", "高效"} else 0.0
    try:
        features["budget_ratio"] = max(0.0, min(2.0, float(context.get("budget_ratio") or 0.0)))
    except (TypeError, ValueError):
        features["budget_ratio"] = 0.0
    try:
        features["travelers"] = max(0.0, min(50.0, float(context.get("travelers") or 0.0))) / 10.0
    except (TypeError, ValueError):
        features["travelers"] = 0.0
    features["low_carbon"] = 1.0 if context.get("low_carbon") is True else 0.0
    return features


def derive_reward(signal: Mapping[str, Any] | None) -> float:
    """把真实行为信号确定性映射为 0..1 的显式奖励。

    支持三类信号（按优先级）：
      1. reward:        显式 0..1 奖励，直接透传；
      2. satisfaction:  满意度评分 0..5，线性映射到 0..1；
      3. signal/outcome: 离散行为信号（采纳/完成/替换/拒绝）。

    拒绝"纯点击"：无明确接受/评分信号时抛 ValueError，避免把曝光当作正反馈。
    """
    if not isinstance(signal, Mapping):
        raise ValueError("signal_required")

    if "reward" in signal and signal.get("reward") is not None:
        reward = float(signal["reward"])
        if 0.0 <= reward <= 1.0:
            return reward
        raise ValueError("reward_out_of_range")

    if "satisfaction" in signal and signal.get("satisfaction") is not None:
        satisfaction = float(signal["satisfaction"])
        if 0.0 <= satisfaction <= 5.0:
            return round(satisfaction / 5.0, 6)
        raise ValueError("satisfaction_out_of_range")

    if "accepted" in signal and signal.get("accepted") is not None:
        return 1.0 if bool(signal["accepted"]) else 0.0

    kind = str(signal.get("signal") or signal.get("outcome") or "").strip().lower()
    mapping = {
        "accepted": 1.0,
        "accept": 1.0,
        "keep": 1.0,
        "completed": 0.9,
        "complete": 0.9,
        "rating_high": 0.8,
        "swapped": 0.2,
        "replace": 0.2,
        "rejected": 0.0,
        "reject": 0.0,
        "skip": 0.0,
    }
    if kind in mapping:
        return mapping[kind]
    raise ValueError("no_reward_signal")


@dataclass
class ArmState:
    alpha: float = 1.0
    beta: float = 1.0
    pulls: int = 0
    reward_sum: float = 0.0
    non_greedy_pulls: int = 0
    tags: set[str] = field(default_factory=set)


def stable_arm_id(policy_version: str, style: str) -> str:
    """Deterministic, reusable arm identity.

    Arm identity must survive across negotiations for learning to accumulate.
    The previous implementation registered each freshly generated variant id
    (`plan_variants[i].id`, a new UUID per request), so every negotiation created
    brand-new arms with untouched priors: the policy could never accumulate
    evidence about anything. An arm is really "the policy version that produced
    this route, applied to this itinerary style", so that pair is the id.
    """
    version = (policy_version or "builtin-joint-v1").strip() or "builtin-joint-v1"
    style_name = (style or "unspecified").strip().lower() or "unspecified"
    return f"{version}:{style_name}"


class ContextualThompsonBandit:
    """Thompson-sampling policy over itinerary styles with bounded memory.

    Selection draws one sample per arm from Beta(alpha, beta) and keeps the
    argmax. That is what makes the class name accurate: exploration falls out of
    the posterior width instead of being switched on by a counter.
    """

    # Bound memory: unbounded arm growth was how this policy leaked.
    max_arms = 512

    def __init__(
        self,
        *,
        exploration_budget: float = 0.10,
        enabled: bool = False,
        seed: int | None = None,
        max_non_greedy_rate: float | None = None,
        max_arms: int | None = None,
    ):
        self.enabled = bool(enabled)
        self.exploration_budget = max(0.0, min(0.20, float(exploration_budget)))
        # Safety ceiling on how often an arm that is not the current posterior
        # favourite may be chosen, expressed as a rate rather than a cumulative
        # count. The old rule was `explorations < max(1, int(decisions * budget))`
        # which evaluates to 1 forever, so the policy explored exactly once in
        # its entire lifetime and then never again.
        self.max_non_greedy_rate = max(
            0.0, min(1.0, float(self.exploration_budget if max_non_greedy_rate is None else max_non_greedy_rate))
        )
        if max_arms is not None:
            self.max_arms = int(max_arms)
        self._rng = random.Random(seed)
        self._arms: Dict[str, ArmState] = {}
        self._non_greedy = 0
        self._decisions = 0
        self._skipped = 0
        self._state_file: str | None = None

    def register(self, arm_id: str, *, tags: Iterable[str] = ()) -> None:
        arm_id = str(arm_id).strip()
        if not arm_id:
            raise ValueError("arm_id_required")
        state = self._arms.setdefault(arm_id, ArmState())
        state.tags.update(str(tag).strip().lower() for tag in tags if str(tag).strip())
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        """Drop the weakest-evidence arms once the cap is exceeded.

        Eviction is by (pulls, total observations) ascending, so an arm that has
        never been rewarded and rarely pulled is discarded before one with real
        evidence. The cap makes the state file bounded, which matters because it
        is rewritten on every reward.
        """
        overflow = len(self._arms) - self.max_arms
        if overflow <= 0:
            return
        ordered = sorted(
            self._arms.items(),
            key=lambda kv: (kv[1].pulls, kv[1].alpha + kv[1].beta),
        )
        for arm_id, _state in ordered[:overflow]:
            self._arms.pop(arm_id, None)
        self._skipped += overflow

    def select(
        self,
        candidates: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
        *,
        hard_constraint: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> Dict[str, Any]:
        safe = [item for item in candidates if isinstance(item, Mapping) and (hard_constraint(item) if hard_constraint else True)]
        if not safe:
            return {"selected": None, "reason": "no_feasible_arm", "explored": False, "propensity": 0.0}
        for item in safe:
            self.register(str(item.get("id") or item.get("name") or "unknown"), tags=item.get("tags") or ())
        self._decisions += 1
        features = context_features(context)

        # Greedy reference point (posterior mean) and the Thompson sample.
        means = [(self._score(item, features), item) for item in safe]
        means.sort(key=lambda row: row[0], reverse=True)
        greedy = means[0][1]

        if not self.enabled or len(safe) == 1:
            return {
                "selected": greedy,
                "reason": "exploitation",
                "explored": False,
                "propensity": 1.0,
                "candidate_count": len(safe),
                "sampled_scores": {},
            }

        samples = [(self._sample(item, features), item) for item in safe]
        samples.sort(key=lambda row: row[0], reverse=True)
        selected = samples[0][1]
        selected_id = self._arm_key(selected)
        greedy_id = self._arm_key(greedy)
        explored = selected_id != greedy_id

        # Enforce the non-greedy ceiling over the policy's whole lifetime.
        if explored and self._decisions > 0:
            allowed = self.max_non_greedy_rate * self._decisions
            if self._non_greedy + 1 > allowed:
                selected = greedy
                selected_id = greedy_id
                explored = False

        # Propensity is the probability that this arm would win under the current
        # posterior, estimated by sampling. This is what makes the logged
        # decisions usable for off-policy evaluation.
        wins = 0
        trials = 64
        arm_ids = [self._arm_key(item) for item in safe]
        for _ in range(trials):
            best = max(range(len(safe)), key=lambda idx: self._rng.betavariate(
                max(1e-6, self._arms[arm_ids[idx]].alpha), max(1e-6, self._arms[arm_ids[idx]].beta)
            ))
            if arm_ids[best] == selected_id:
                wins += 1
        propensity = wins / trials

        if explored:
            self._non_greedy += 1

        return {
            "selected": selected,
            "reason": "thompson_exploration" if explored else "thompson_exploitation",
            "explored": explored,
            "propensity": round(min(1.0, max(1e-6, propensity)), 6),
            "candidate_count": len(safe),
            "sampled_scores": {arm_ids[idx]: round(row[0], 6) for idx, row in enumerate(samples)},
        }

    @staticmethod
    def _arm_key(candidate: Mapping[str, Any]) -> str:
        return str(candidate.get("id") or candidate.get("name") or "unknown")

    def _sample(self, candidate: Mapping[str, Any], features: Mapping[str, float]) -> float:
        """One Beta draw plus the deterministic affinity term."""
        arm_id = self._arm_key(candidate)
        state = self._arms[arm_id]
        draw = self._rng.betavariate(max(1e-6, state.alpha), max(1e-6, state.beta))
        return draw + self._affinity(candidate, state, features) + 0.001 * float(candidate.get("score") or 0.0)

    def _affinity(self, candidate: Mapping[str, Any], state: ArmState, features: Mapping[str, float]) -> float:
        tags = {str(tag).lower() for tag in (candidate.get("tags") or [])}
        return 0.03 if features["low_carbon"] and ({"low-carbon", "低碳"} & (tags | state.tags)) else 0.0

    def record(self, arm_id: str, reward: float) -> None:
        if arm_id not in self._arms:
            raise KeyError("arm_not_registered")
        reward = float(reward)
        if not 0.0 <= reward <= 1.0:
            raise ValueError("reward_out_of_range")
        state = self._arms[arm_id]
        state.pulls += 1
        state.reward_sum += reward
        state.alpha += reward
        state.beta += 1.0 - reward
        if self._state_file:
            self.save(self._state_file)

    def snapshot(self) -> Dict[str, Any]:
        total_pulls = sum(state.pulls for state in self._arms.values())
        return {
            "enabled": self.enabled,
            "algorithm": "thompson_sampling_beta",
            "exploration_budget": self.exploration_budget,
            "max_non_greedy_rate": self.max_non_greedy_rate,
            "decisions": self._decisions,
            "explorations": self._non_greedy,
            "observed_non_greedy_rate": round(self._non_greedy / max(1, self._decisions), 6),
            "rewards_recorded": total_pulls,
            "evicted_arms": self._skipped,
            "arm_count": len(self._arms),
            "max_arms": self.max_arms,
            "arms": {
                key: {
                    "pulls": value.pulls,
                    "mean_reward": round(value.reward_sum / max(1, value.pulls), 6),
                    "posterior_mean": round(value.alpha / max(1e-9, value.alpha + value.beta), 6),
                    "alpha": round(value.alpha, 6),
                    "beta": round(value.beta, 6),
                }
                for key, value in sorted(self._arms.items())
            },
        }

    def to_state(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "exploration_budget": self.exploration_budget,
            "max_non_greedy_rate": self.max_non_greedy_rate,
            "decisions": self._decisions,
            "explorations": self._non_greedy,
            "skipped": self._skipped,
            "arms": {
                key: {
                    "alpha": value.alpha,
                    "beta": value.beta,
                    "pulls": value.pulls,
                    "reward_sum": value.reward_sum,
                    "non_greedy_pulls": value.non_greedy_pulls,
                    "tags": sorted(value.tags),
                }
                for key, value in self._arms.items()
            },
        }

    def from_state(self, data: Mapping[str, Any]) -> None:
        self.enabled = bool(data.get("enabled", self.enabled))
        self.exploration_budget = max(0.0, min(0.20, float(data.get("exploration_budget", self.exploration_budget))))
        self.max_non_greedy_rate = max(
            0.0, min(1.0, float(data.get("max_non_greedy_rate", self.max_non_greedy_rate)))
        )
        self._decisions = int(data.get("decisions", self._decisions))
        self._non_greedy = int(data.get("explorations", self._non_greedy))
        self._skipped = int(data.get("skipped", self._skipped))
        for arm_id, arm in (data.get("arms") or {}).items():
            state = self._arms.setdefault(str(arm_id), ArmState())
            state.alpha = float(arm.get("alpha", state.alpha))
            state.beta = float(arm.get("beta", state.beta))
            state.pulls = int(arm.get("pulls", state.pulls))
            state.reward_sum = float(arm.get("reward_sum", state.reward_sum))
            state.non_greedy_pulls = int(arm.get("non_greedy_pulls", state.non_greedy_pulls))
            state.tags = set(arm.get("tags") or ())
        self._evict_if_needed()

    def save(self, path: str) -> None:
        import json
        import os

        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Write to a temp file and replace, so a crash mid-write cannot leave a
        # truncated state file that would wipe the learned posterior on restart.
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_state(), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def load(self, path: str) -> bool:
        """从 JSON 文件恢复学习状态；成功后记下状态文件以便后续自动持久化。"""
        import json
        import os

        if not (path and os.path.exists(path)):
            return False
        with open(path, "r", encoding="utf-8") as fh:
            self.from_state(json.load(fh))
        self._state_file = path
        return True

    def arm_state_file(self, path: str) -> None:
        """Enable autosave on a path without requiring the file to exist yet.

        `load()` can only arm persistence when there is something to read, so a
        first-ever run had no save target and lost every posterior on restart.
        """
        if path:
            self._state_file = path

    def _score(self, candidate: Mapping[str, Any], features: Mapping[str, float]) -> float:
        """Posterior mean plus affinity — the deterministic (greedy) score."""
        arm_id = self._arm_key(candidate)
        state = self._arms[arm_id]
        prior = state.alpha / max(1e-9, state.alpha + state.beta)
        return prior + self._affinity(candidate, state, features) + 0.001 * float(candidate.get("score") or 0.0)
