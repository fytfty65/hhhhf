"""Coordinator gate for specialist proposals.

Only proposals that passed the structured contract and carry evidence are
allowed to narrow candidate pools before Pareto/fairness ranking.  A degraded
proposal never becomes a silent success; its reason is retained in the audit.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from .optimization import validate_candidate


def _name(value: Mapping[str, Any]) -> str:
    return "".join(str(value.get("name") or value.get("location") or "").lower().split())


def gate_candidate_pool(
    pool: Sequence[Mapping[str, Any]],
    proposal: Mapping[str, Any] | None,
    *,
    require_evidence: bool = True,
) -> Dict[str, Any]:
    """Return accepted/rejected candidates and an auditable gate decision."""
    rows = [dict(item) for item in pool if isinstance(item, Mapping)]
    if not isinstance(proposal, Mapping):
        return {"accepted": [], "rejected": [{"name": _name(item), "reason": "proposal_missing"} for item in rows], "status": "failed", "reason": "proposal_missing"}
    status = str(proposal.get("status") or "failed")
    evidence = proposal.get("evidence")
    if status != "ok":
        return {
            "accepted": [],
            "rejected": [{"name": _name(item), "reason": f"proposal_{status}"} for item in rows],
            "status": status,
            "reason": (proposal.get("failure") or {}).get("code", "proposal_not_accepted"),
        }
    if require_evidence and not isinstance(evidence, Sequence):
        return {"accepted": [], "rejected": [{"name": _name(item), "reason": "evidence_missing"} for item in rows], "status": "failed", "reason": "evidence_missing"}

    allowed = {_name(item) for item in (proposal.get("candidates") or []) if isinstance(item, Mapping) and _name(item)}
    accepted = []
    rejected = []
    for item in rows:
        key = _name(item)
        validation = validate_candidate(item)
        if not key:
            rejected.append({"name": key, "reason": "name_required"})
        elif key not in allowed:
            rejected.append({"name": key, "reason": "not_in_specialist_proposal"})
        elif not validation["valid"]:
            rejected.append({"name": key, "reason": "candidate_invalid", "violations": validation["violations"]})
        else:
            accepted.append(item)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "status": "ok",
        "reason": "evidence_and_candidate_contract_passed",
    }


def apply_candidate_gates(
    pools: Mapping[str, Sequence[Mapping[str, Any]]],
    runtime_result: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply specialist gates to the pools used by the coordinator."""
    proposals = {
        str(item.get("agent_id")): item
        for item in (runtime_result.get("proposals") or [])
        if isinstance(item, Mapping) and item.get("agent_id")
    }
    mapping = {
        "attractions": "route-planner",
        "dining": "dining-agent",
        "lodging": "lodging-agent",
        "transport": "transport-agent",
        "budget": "budget-agent",
    }
    gated: Dict[str, list[dict[str, Any]]] = {}
    audits: Dict[str, Any] = {}
    for pool_name, items in pools.items():
        agent_id = mapping.get(pool_name)
        if not agent_id:
            gated[pool_name] = [dict(item) for item in items if isinstance(item, Mapping)]
            audits[pool_name] = {"status": "not_applicable", "accepted": len(gated[pool_name]), "rejected": 0}
            continue
        result = gate_candidate_pool(items, proposals.get(agent_id))
        gated[pool_name] = result["accepted"]
        audits[pool_name] = {
            "status": result["status"],
            "reason": result["reason"],
            "accepted": len(result["accepted"]),
            "rejected": len(result["rejected"]),
        }
    return {"pools": gated, "audits": audits}
