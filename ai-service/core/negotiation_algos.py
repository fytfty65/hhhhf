"""Route/negotiation algorithm classes extracted from ``api/agent.py``.

Why this module exists (2026-10-10): ``api/agent.py`` had grown to ~7,100 lines
with a single request function of several thousand lines, so every edit carried
the blast radius of the whole service. These two classes are pure algorithms with
no dependency on the request or streaming state around them, which makes them the
safe first extraction: they were lifted out verbatim, tested directly, and
re-imported by ``agent.py`` so no caller changes.

The classes are re-exported from ``api.agent`` for compatibility. Prefer importing
from here in new code.

Contents:
  - ``ParetoFrontierSelector``: conflict resolution over candidate proposals by
    multi-objective Pareto non-dominance (fitness maximised; cost and risk
    minimised). It does **not** compute a Nash equilibrium; the old class name
    ``NashEquilibriumSolver`` claimed otherwise and has been retired.
  - ``GraphRouteOptimizer``: DBSCAN geographic clustering plus a
    travelling-salesman approximation to order POIs into a route.
"""

from __future__ import annotations

import re

import networkx as nx
import numpy as np
from sklearn.cluster import DBSCAN
from typing import Any, Dict, List

from core.optimization import pareto_frontier, validate_candidate


class ParetoFrontierSelector:
    @staticmethod
    def _numeric(value: Any) -> float:
        try:
            match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
            return float(match.group()) if match else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def resolve_conflicts(pois: List[Dict]) -> Dict[str, Any]:
        """Return a real Pareto audit instead of a threshold-filtered list.

        Candidate fitness remains one objective for compatibility, while cost,
        risk and distance are kept explicit so the caller can inspect why a
        candidate was retained or dominated.
        """
        if not isinstance(pois, list):
            return {"frontier": [], "rejected": [], "objectives": []}
        valid = []
        rejected_invalid = []
        for index, candidate in enumerate(pois):
            if not isinstance(candidate, dict):
                rejected_invalid.append({"candidate": "", "index": index, "reason": "candidate_not_object"})
                continue
            validation = validate_candidate(candidate)
            if validation.get("valid"):
                valid.append(candidate)
            else:
                rejected_invalid.append({"candidate": str(candidate.get("name") or candidate.get("location") or ""), "index": index, "reason": "hard_constraint_violation", "violations": validation.get("violations", [])})
        result = pareto_frontier(
            valid,
            {
                "fitness": lambda row: float(row.get("fitness_score", 0) or 0),
                "cost": lambda row: ParetoFrontierSelector._numeric(row.get("cost", row.get("cost_estimate", 0))),
                "risk": lambda row: ParetoFrontierSelector._numeric(row.get("risk_score", row.get("riskScore", 0))),
            },
            {"fitness": True, "cost": False, "risk": False},
        )
        result["rejected"] = rejected_invalid + result.get("rejected", [])
        result["rejected_count"] = len(result["rejected"])
        return result


class GraphRouteOptimizer:
    def __init__(self, fitness_calculator):
        self.calc = fitness_calculator

    def optimize_and_sort(self, pois: List[Dict], max_nodes: int) -> List[Dict]:
        if not pois or not isinstance(pois, list):
            return []
        valid_pois = []
        coords = []

        for p in pois:
            if not isinstance(p, dict):
                continue
            loc = p.get("location", "")
            if isinstance(loc, list) and len(loc) >= 2:
                try:
                    lat, lon = float(loc[1]), float(loc[0])
                    coords.append([lat, lon])
                    valid_pois.append(p)
                except Exception:
                    pass
            elif isinstance(loc, str) and "," in loc:
                try:
                    lon, lat = map(float, loc.split(","))
                    coords.append([lat, lon])
                    valid_pois.append(p)
                except Exception:
                    pass

        if len(coords) < 3:
            return sorted(valid_pois, key=lambda x: float(x.get("fitness_score", 0) or 0), reverse=True)[:max_nodes]

        try:
            X = np.radians(np.array(coords))
            db = DBSCAN(eps=6/6371.0, min_samples=1, algorithm='ball_tree', metric='haversine').fit(X)

            G = nx.Graph()
            for i, poi in enumerate(valid_pois):
                poi['cluster'] = int(db.labels_[i])
                G.add_node(i, attr=poi)

            for i in range(len(valid_pois)):
                for j in range(i + 1, len(valid_pois)):
                    dist = self.calc._haversine_distance(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
                    score_val = float(valid_pois[j].get("fitness_score", 0) or 0)
                    edge_weight = dist - (score_val * 0.1)
                    G.add_edge(i, j, weight=max(0.1, edge_weight))

            tsp_path = nx.approximation.traveling_salesman_problem(G, cycle=False)
            optimized_pois = [G.nodes[n]['attr'] for n in tsp_path]
            return optimized_pois[:max_nodes]
        except Exception:
            return sorted(valid_pois, key=lambda x: float(x.get("fitness_score", 0) or 0), reverse=True)[:max_nodes]
