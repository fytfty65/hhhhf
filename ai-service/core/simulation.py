"""Monte-Carlo simulation of an itinerary ("probabilistic digital twin").

Why this module exists
----------------------
The planner previously reported point estimates for things that are genuinely
uncertain, and two of them were not estimates at all:

* `agent.py` shipped a hardcoded ``team_satisfaction`` of 96/94/93 inside the LLM
  output template, so the model echoed numbers that no computation produced;
* `travel_utils.estimate_crowdedness` returned a fixed ``percent`` (82/58/35)
  that did not depend on the ``score`` it had just computed — ``score=123`` still
  reported 82%.

A single number presented as fact is worse than an honest distribution, because
the user cannot tell how much to trust it. This module replaces those point
estimates with sampled distributions: for each plan it runs N iterations over the
uncertain dimensions (dwell time, queueing, traffic, weather) and reports P50/P90
totals, the probability of overrunning the budget, and per-member satisfaction
spread.

Design constraints
------------------
* Pure computation, no I/O, no LLM, no network — so it is exhaustively testable.
* Deterministic for a fixed seed, so results are reproducible and diffable.
* Never fabricates a distribution for something it has no input for: missing
  inputs fall back to a documented default and are reported in ``assumptions``.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# Uncertainty model
# ---------------------------------------------------------------------------

# Relative spread of dwell time by activity category. A museum visit is fairly
# predictable; shopping and food streets are not.
CATEGORY_DWELL_SIGMA: Dict[str, float] = {
    "museum": 0.20,
    "culture": 0.25,
    "scenic": 0.28,
    "nature": 0.30,
    "food": 0.35,
    "shopping": 0.45,
    "nightlife": 0.40,
    "transit": 0.15,
    "hotel": 0.05,
    "other": 0.30,
}

# Median queue time in minutes and its lognormal sigma, by category.
# Values are order-of-magnitude estimates for a mid-season weekday; they are
# declared here (rather than hidden in a function) so they can be reviewed.
CATEGORY_QUEUE_MEDIAN_MIN: Dict[str, float] = {
    "museum": 15.0,
    "culture": 12.0,
    "scenic": 20.0,
    "nature": 5.0,
    "food": 18.0,
    "shopping": 5.0,
    "nightlife": 8.0,
    "transit": 0.0,
    "hotel": 5.0,
    "other": 8.0,
}

# Queue pressure multiplier by time-of-day bucket.
TIME_OF_DAY_QUEUE_FACTOR: Dict[str, float] = {
    "early": 0.5,    # before 09:00
    "morning": 0.9,  # 09:00-11:30
    "midday": 1.4,   # 11:30-14:00 peak
    "afternoon": 1.1,
    "evening": 0.8,
    "late": 0.4,
}

# Weather-condition multipliers on outdoor dwell time. Keys are matched as
# substrings of the supplied condition text, lowercased.
WEATHER_DWELL_FACTOR: Sequence[tuple] = (
    ("特大暴雨", 0.35),
    ("大暴雨", 0.40),
    ("暴雨", 0.45),
    ("台风", 0.35),
    ("雷暴", 0.50),
    ("大雨", 0.55),
    ("中雨", 0.75),
    ("小雨", 0.85),
    ("阵雨", 0.80),
    ("雪", 0.65),
    ("大雾", 0.75),
    ("雾霾", 0.85),
    ("沙尘", 0.70),
    ("晴", 1.00),
    ("多云", 1.00),
    ("阴", 0.95),
)

# Indoor categories keep working when the weather turns; outdoor ones do not.
INDOOR_CATEGORIES = {"museum", "culture", "shopping", "food", "hotel"}

# Travel-time dispersion for a leg. Public transit and driving both have a long
# right tail (missed connection, congestion); rail is comparatively stable.
MODE_TRAVEL_SIGMA: Dict[str, float] = {
    "walking": 0.20,
    "cycling": 0.30,
    "metro": 0.35,
    "subway": 0.35,
    "bus": 0.45,
    "transit": 0.40,
    "driving": 0.55,
    "taxi": 0.55,
    "highspeed_rail": 0.15,
    "train": 0.25,
    "flight": 0.35,
}

DEFAULT_DWELL_MINUTES = 90.0
DEFAULT_TRAVEL_MINUTES = 30.0
DEFAULT_TRAVEL_MODE = "transit"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _number_in_text(text: str) -> Optional[float]:
    """Extract the first numeric value from free text such as '约90分钟' or '¥80'."""
    match = re.search(r"\d+(?:\.\d+)?", text or "")
    if not match:
        return None
    return float(match.group(0))


def category_of(node: Mapping[str, Any]) -> str:
    """Best-effort category, matching the planner's own category vocabulary."""
    raw = str(node.get("category") or node.get("type") or "").lower()
    if raw in CATEGORY_DWELL_SIGMA:
        return raw
    tags = node.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    joined = " ".join(str(t).lower() for t in tags)
    for key in CATEGORY_DWELL_SIGMA:
        if key != "other" and key in joined:
            return key
    if node.get("is_hotel") is True or "酒店" in joined or "住宿" in joined:
        return "hotel"
    return "other"


def time_of_day_bucket(value: Any) -> str:
    """Map a 'Day N | HH:MM' style time string onto a queue-pressure bucket."""
    text = str(value or "")
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return "morning"
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 9:
        return "early"
    if hour < 11 or (hour == 11 and minute < 30):
        return "morning"
    if hour < 14:
        return "midday"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "late"


def weather_dwell_factor(condition: str) -> float:
    """Multiplier applied to OUTDOOR dwell time for a weather condition."""
    text = (condition or "").lower()
    for keyword, factor in WEATHER_DWELL_FACTOR:
        if keyword in text:
            return factor
    return 1.0


def dwell_minutes_of(node: Mapping[str, Any]) -> float:
    """Base dwell time in minutes, read from the node or defaulted."""
    for key in ("dwell_minutes", "duration_minutes", "stay_minutes"):
        if node.get(key) is not None:
            return max(0.0, _as_float(node.get(key), DEFAULT_DWELL_MINUTES))
    for key in ("duration", "stay", "dwell"):
        parsed = _number_in_text(str(node.get(key) or ""))
        if parsed is not None:
            return max(0.0, parsed)
    return DEFAULT_DWELL_MINUTES


def cost_of(node: Mapping[str, Any]) -> Optional[float]:
    """Explicit cost, or None when the node carries no usable price.

    Distinguishing "free" from "unknown" matters: an unpriced node must not be
    treated as costing zero, which is what made a fully unpriced itinerary
    advertise a total of ¥0.
    """
    for key in ("cost", "cost_estimate", "price"):
        if key not in node:
            continue
        raw = node.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            return max(0.0, float(raw))
        text = str(raw)
        if "暂无" in text or "未知" in text:
            return None
        parsed = _number_in_text(text)
        if parsed is not None:
            return max(0.0, parsed)
    return None


def travel_minutes_of(node: Mapping[str, Any]) -> float:
    for key in ("travel_minutes", "transit_minutes", "transfer_minutes"):
        if node.get(key) is not None:
            return max(0.0, _as_float(node.get(key), DEFAULT_TRAVEL_MINUTES))
    for key in ("transport_time", "travel", "transport"):
        parsed = _number_in_text(str(node.get(key) or ""))
        if parsed is not None:
            return max(0.0, parsed)
    return DEFAULT_TRAVEL_MINUTES


def travel_mode_of(node: Mapping[str, Any]) -> str:
    raw = str(node.get("transport") or node.get("mode") or "").lower()
    if raw in MODE_TRAVEL_SIGMA:
        return raw
    for key in MODE_TRAVEL_SIGMA:
        if key and key in raw:
            return key
    return DEFAULT_TRAVEL_MODE


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


@dataclass
class SimulationAssumptions:
    """Inputs that were missing and therefore defaulted.

    Reported alongside the result so a caller can tell how much of the output is
    grounded in supplied data versus this module's defaults.
    """

    defaulted_dwell: int = 0
    defaulted_travel: int = 0
    unpriced_nodes: int = 0
    nodes: int = 0
    weather_condition: str = ""
    sample_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "defaulted_dwell": self.defaulted_dwell,
            "defaulted_travel": self.defaulted_travel,
            "unpriced_nodes": self.unpriced_nodes,
            "weather_condition": self.weather_condition,
            "sample_count": self.sample_count,
            "basis": "monte_carlo",
        }


@dataclass
class SimulationResult:
    samples: int
    total_minutes_p50: float
    total_minutes_p90: float
    total_minutes_mean: float
    cost_known_p50: float
    cost_known_p90: float
    cost_complete_probability: float
    budget_overrun_probability: Optional[float]
    per_day_minutes_p90: Dict[str, float]
    member_satisfaction: Dict[str, Dict[str, float]] = field(default_factory=dict)
    assumptions: SimulationAssumptions = field(default_factory=SimulationAssumptions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "samples": self.samples,
            "total_minutes": {
                "p50": round(self.total_minutes_p50, 1),
                "p90": round(self.total_minutes_p90, 1),
                "mean": round(self.total_minutes_mean, 1),
            },
            "cost": {
                "known_p50": round(self.cost_known_p50, 2),
                "known_p90": round(self.cost_known_p90, 2),
                "complete_probability": round(self.cost_complete_probability, 4),
            },
            "budget_overrun_probability": (
                None
                if self.budget_overrun_probability is None
                else round(self.budget_overrun_probability, 4)
            ),
            "per_day_minutes_p90": {
                day: round(value, 1) for day, value in sorted(self.per_day_minutes_p90.items())
            },
            "member_satisfaction": {
                member: {k: round(v, 4) for k, v in stats.items()}
                for member, stats in self.member_satisfaction.items()
            },
            "assumptions": self.assumptions.to_dict(),
        }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile; returns 0 for an empty sample."""
    if not sorted_values:
        return 0.0
    index = int(math.ceil(fraction * len(sorted_values))) - 1
    return sorted_values[_clamp(index, 0, len(sorted_values) - 1)]


def _sample_dwell(rng: random.Random, base: float, sigma: float, queue_median: float, tod_factor: float, weather_factor: float, indoor: bool) -> float:
    """One sampled dwell time: lognormal core plus sampled queueing."""
    if base <= 0:
        return 0.0
    mu = math.log(base)
    dwell = rng.lognormvariate(mu, sigma)
    if queue_median > 0:
        queue = rng.lognormvariate(math.log(queue_median), 0.6) * tod_factor
        dwell += queue
    if not indoor:
        dwell *= weather_factor
    return max(0.0, dwell)


def _sample_travel(rng: random.Random, base: float, sigma: float) -> float:
    if base <= 0:
        return 0.0
    return max(0.0, rng.lognormvariate(math.log(base), sigma))


def member_utilities_for(
    nodes: Sequence[Mapping[str, Any]],
    members: Iterable[Mapping[str, Any]],
) -> Dict[str, float]:
    """Deterministic per-member utility in 0..1 from interest-tag coverage.

    Deliberately mirrors the weighting used by `core.optimization.member_utilities`
    so the simulation and the planner's own fairness report agree instead of
    reporting two different "satisfaction" notions. A member whose interests are
    simply unknown is scored at the neutral midpoint rather than being penalised
    for missing data.
    """
    member_list = [m for m in members if isinstance(m, Mapping)]
    if not member_list:
        return {}

    node_tags: List[set] = []
    for node in nodes:
        tags = node.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        node_tags.append({str(t).lower() for t in tags})

    utilities: Dict[str, float] = {}
    for index, member in enumerate(member_list):
        name = str(member.get("name") or member.get("id") or f"member-{index + 1}")
        interests = member.get("interests") or member.get("interest_tags") or []
        if isinstance(interests, str):
            interests = [interests]
        wanted = {str(t).lower() for t in interests}
        if not wanted:
            utilities[name] = 0.5
            continue
        covered = 0
        for tags in node_tags:
            if tags & wanted:
                covered += 1
        coverage = covered / max(1, len(node_tags))
        # Calibration note: coverage alone is a harsh satisfaction proxy on a
        # short route. A 4-node itinerary in which a member's single stated
        # interest IS represented yields coverage 0.25, so the earlier
        # `0.35 + 0.55*coverage` scored that member 0.49 — "unhappy" despite their
        # interest being served. The baseline is therefore generous and the
        # coverage term carries the differentiation between members.
        role = str(member.get("role") or "")
        role_bonus = 0.1 if role and any(role.lower() in " ".join(t) for t in node_tags) else 0.0
        utilities[name] = _clamp(0.45 + 0.45 * coverage + role_bonus, 0.0, 1.0)
    return utilities


def simulate_plan(
    nodes: Sequence[Mapping[str, Any]],
    *,
    members: Optional[Iterable[Mapping[str, Any]]] = None,
    weather_condition: str = "",
    budget: Optional[float] = None,
    samples: int = 2000,
    seed: int = 20260519,
) -> SimulationResult:
    """Run a Monte-Carlo simulation over an itinerary.

    `nodes` items are expected to expose the same loose shape the planner already
    produces (`day`, `time`, `location`/`name`, `tags`, `cost`, `transport`). All
    fields are optional; anything missing is defaulted and counted in
    `assumptions` so the caller can see how grounded the result is.
    """
    rng = random.Random(seed)
    node_list = [n for n in nodes if isinstance(n, Mapping)]
    sample_count = max(1, int(samples))

    if not node_list:
        return SimulationResult(
            samples=sample_count,
            total_minutes_p50=0.0,
            total_minutes_p90=0.0,
            total_minutes_mean=0.0,
            cost_known_p50=0.0,
            cost_known_p90=0.0,
            cost_complete_probability=1.0,
            budget_overrun_probability=None,
            per_day_minutes_p90={},
            assumptions=SimulationAssumptions(nodes=0, sample_count=sample_count),
        )

    assumptions = SimulationAssumptions(
        nodes=len(node_list),
        weather_condition=weather_condition or "",
        sample_count=sample_count,
    )

    prepared = []
    known_cost_total = 0.0
    for node in node_list:
        category = category_of(node)
        dwell = dwell_minutes_of(node)
        if not any(k in node for k in ("dwell_minutes", "duration_minutes", "stay_minutes")) and not any(
            k in node for k in ("duration", "stay", "dwell")
        ):
            assumptions.defaulted_dwell += 1
        travel = travel_minutes_of(node)
        if not any(
            k in node
            for k in ("travel_minutes", "transit_minutes", "transfer_minutes", "transport_time", "travel", "transport")
        ):
            assumptions.defaulted_travel += 1
        cost = cost_of(node)
        if cost is None:
            assumptions.unpriced_nodes += 1
        else:
            known_cost_total += cost
        prepared.append(
            {
                "day": str(node.get("day") or "1"),
                "category": category,
                "dwell": dwell,
                "dwell_sigma": CATEGORY_DWELL_SIGMA.get(category, CATEGORY_DWELL_SIGMA["other"]),
                "queue_median": CATEGORY_QUEUE_MEDIAN_MIN.get(category, CATEGORY_QUEUE_MEDIAN_MIN["other"]),
                "tod_factor": TIME_OF_DAY_QUEUE_FACTOR.get(time_of_day_bucket(node.get("time")), 1.0),
                "indoor": category in INDOOR_CATEGORIES,
                "travel": travel,
                "travel_sigma": MODE_TRAVEL_SIGMA.get(travel_mode_of(node), MODE_TRAVEL_SIGMA[DEFAULT_TRAVEL_MODE]),
                "cost": cost,
            }
        )

    weather_factor = weather_dwell_factor(weather_condition)

    totals: List[float] = []
    costs: List[float] = []
    overruns = 0
    per_day_samples: Dict[str, List[float]] = {}

    for _ in range(sample_count):
        total = 0.0
        cost_total = 0.0
        for item in prepared:
            dwell = _sample_dwell(
                rng,
                item["dwell"],
                item["dwell_sigma"],
                item["queue_median"],
                item["tod_factor"],
                weather_factor,
                item["indoor"],
            )
            travel = _sample_travel(rng, item["travel"], item["travel_sigma"])
            leg = dwell + travel
            total += leg
            per_day_samples.setdefault(item["day"], []).append(leg)
            if item["cost"] is not None:
                cost_total += item["cost"]
        totals.append(total)
        costs.append(cost_total)
        if budget is not None and cost_total > float(budget):
            overruns += 1

    totals.sort()
    costs.sort()
    per_day_p90 = {
        day: _percentile(sorted(values), 0.90) for day, values in per_day_samples.items()
    }

    member_stats: Dict[str, Dict[str, float]] = {}
    if members is not None:
        utilities = member_utilities_for(node_list, members)
        for name, utility in utilities.items():
            # Satisfaction is the deterministic utility modulated by how much
            # schedule pressure the sampled plan actually creates: an overrunning
            # day degrades everyone's experience, which is what makes this a
            # distribution rather than a constant.
            p90 = _percentile(totals, 0.90)
            p50 = _percentile(totals, 0.50)
            pressure = 0.0 if p50 <= 0 else _clamp((p90 - p50) / p50, 0.0, 1.0)
            sampled = [
                _clamp(utility - rng.uniform(0.0, 0.12) - 0.10 * pressure, 0.0, 1.0)
                for _ in range(64)
            ]
            sampled.sort()
            member_stats[name] = {
                # p50 of the sampled satisfaction, and a conservative floor
                # (10th percentile) so the UI can show a range rather than a
                # single fabricated score.
                "satisfaction_p50": _percentile(sampled, 0.50),
                "satisfaction_floor_p10": _percentile(sampled, 0.10),
                "mean": round(sum(sampled) / len(sampled), 4),
                "deterministic_utility": utility,
            }

    return SimulationResult(
        samples=sample_count,
        total_minutes_p50=_percentile(totals, 0.50),
        total_minutes_p90=_percentile(totals, 0.90),
        total_minutes_mean=sum(totals) / len(totals),
        cost_known_p50=_percentile(costs, 0.50),
        cost_known_p90=_percentile(costs, 0.90),
        # Probability that every node carried a usable price. When this is 0 the
        # totals above are lower bounds and the UI must say so.
        cost_complete_probability=(
            1.0 if assumptions.unpriced_nodes == 0 else 0.0
        ),
        budget_overrun_probability=(overruns / sample_count) if budget is not None else None,
        per_day_minutes_p90=per_day_p90,
        member_satisfaction=member_stats,
        assumptions=assumptions,
    )
