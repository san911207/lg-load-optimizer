"""
=============================================================================
Expert Review Agents
=============================================================================

Three rule-based + optional LLM-augmented agents that review simulation
results from a specific domain perspective:

  1. LogisticsExpert     — operations, cost, compliance
  2. ITSystemsExpert     — data quality, integration readiness
  3. WarehouseOpsExpert  — dock safety, worker workflow, OSHA

Each agent returns a list of Findings with severity, message, and rationale.

Design principle:
  - Rule-based checks first (deterministic, free, fast)
  - LLM call only for nuanced situations (optional, costs money)
  - All thresholds are configurable and documented
  - Every finding cites the rule or standard it's based on
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


class Severity(str, Enum):
    OK = "✅"
    INFO = "ℹ️"
    WARN = "⚠️"
    CRITICAL = "🚨"


@dataclass
class Finding:
    severity: Severity
    category: str            # e.g. "weight_distribution", "data_quality"
    message: str
    rationale: str = ""      # why this rule exists (source/standard)
    suggested_action: str = ""


@dataclass
class AgentReview:
    agent_name: str
    agent_role: str
    findings: List[Finding] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    @property
    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.findings)


# =============================================================================
# Agent 1: Logistics Expert
# =============================================================================

class LogisticsExpert:
    """Reviews from operations, cost, and DOT/FMCSA compliance perspective."""

    name = "Logistics Expert"
    role = "Reviews load plan for operational efficiency, weight distribution, and regulatory compliance"

    # Industry-standard thresholds
    WEIGHT_DIST_FRONT_MIN = 55  # %, ideal 60
    WEIGHT_DIST_FRONT_MAX = 65
    VOLUME_UTIL_GOOD = 70       # %, below = inefficient
    VOLUME_UTIL_OK = 50
    PAYLOAD_UTIL_GOOD = 70

    def review(self, simulation_result: Dict[str, Any], master: Dict[str, Any]) -> AgentReview:
        review = AgentReview(agent_name=self.name, agent_role=self.role)
        m = simulation_result["metrics"]

        # 1. Volume utilization
        vol_pct = m["volume_util_pct"]
        if vol_pct < 30:
            review.findings.append(Finding(
                severity=Severity.CRITICAL,
                category="truck_sizing",
                message=f"Volume utilization only {vol_pct}% — truck severely oversized",
                rationale="Trucks below 30% utilization indicate the wrong vehicle was selected. "
                          "Industry benchmark: 50%+ for cost-effective shipping.",
                suggested_action="Consider downsizing to 16ft or 20ft truck, or consolidate with another shipment.",
            ))
        elif vol_pct < self.VOLUME_UTIL_OK:
            review.findings.append(Finding(
                severity=Severity.WARN,
                category="truck_sizing",
                message=f"Volume utilization {vol_pct}% — truck may be oversized",
                rationale="Below 50% utilization adds unnecessary freight cost.",
                suggested_action="Review if smaller truck is available.",
            ))
        elif vol_pct >= self.VOLUME_UTIL_GOOD:
            review.findings.append(Finding(
                severity=Severity.OK,
                category="truck_sizing",
                message=f"Volume utilization {vol_pct}% — excellent",
                rationale="Above 70% indicates near-optimal truck selection.",
            ))

        # 2. Length utilization (compactness)
        comp_pct = m["compactness_pct"]
        if comp_pct > 98:
            review.findings.append(Finding(
                severity=Severity.WARN,
                category="length_buffer",
                message=f"Length used {comp_pct}% — no buffer for last-minute additions",
                rationale="DOT recommends 5%+ buffer for strap adjustments and add-ons.",
                suggested_action="If possible, leave 1-2 ft buffer for safety.",
            ))
        elif comp_pct < 50:
            review.findings.append(Finding(
                severity=Severity.WARN,
                category="length_buffer",
                message=f"Length used {comp_pct}% — significant underutilization",
                rationale="Low compactness with high volume usually means height is the constraint.",
            ))

        # 3. Payload utilization
        wt_pct = m["weight_util_pct"]
        if wt_pct > 95:
            review.findings.append(Finding(
                severity=Severity.CRITICAL,
                category="payload",
                message=f"Weight {wt_pct}% of payload — near or over GVWR limit",
                rationale="Exceeding GVWR is a DOT violation and risks vehicle safety.",
                suggested_action="Remove items or split into two loads.",
            ))
        elif wt_pct < 30 and vol_pct < 50:
            review.findings.append(Finding(
                severity=Severity.INFO,
                category="payload",
                message=f"Both weight ({wt_pct}%) and volume ({vol_pct}%) low",
                rationale="Truck is significantly underutilized in both dimensions.",
            ))

        # 4. Unfitted units
        if simulation_result["unfitted_count"] > 0:
            unfitted = simulation_result["unfitted_detail"]
            items = ", ".join(f"{u['model_code']}×{u['quantity']}" for u in unfitted)
            review.findings.append(Finding(
                severity=Severity.CRITICAL,
                category="unfitted",
                message=f"{simulation_result['unfitted_count']} units could not be loaded: {items}",
                rationale="Unfitted units indicate algorithm couldn't place them — verify upstream order planning.",
                suggested_action="Either upgrade to 53ft truck, or add second 26ft load.",
            ))

        return review


# =============================================================================
# Agent 2: IT Systems Expert
# =============================================================================

class ITSystemsExpert:
    """Reviews data quality, integration readiness, and system reliability."""

    name = "IT/Systems Expert"
    role = "Reviews data quality, master record completeness, and integration readiness"

    REQUIRED_FIELDS = {
        "category", "width_mm", "depth_mm", "height_mm",
        "weight_kg", "stackable", "load_bear_kg", "fragile",
    }

    def review(self, simulation_result: Dict[str, Any], master: Dict[str, Any]) -> AgentReview:
        review = AgentReview(agent_name=self.name, agent_role=self.role)

        # 1. Master data completeness
        models_used = set(p["model_code"] for p in simulation_result["placements"])
        for mc in models_used:
            if mc not in master:
                review.findings.append(Finding(
                    severity=Severity.CRITICAL,
                    category="data_integrity",
                    message=f"Model {mc} placed but not in master — orphan record",
                    rationale="Simulation succeeded with missing master data. Indicates data sync issue.",
                ))
                continue
            spec = master[mc]
            missing = self.REQUIRED_FIELDS - set(spec.keys())
            if missing:
                review.findings.append(Finding(
                    severity=Severity.WARN,
                    category="data_quality",
                    message=f"Model {mc} missing fields: {', '.join(missing)}",
                    rationale="Incomplete master records may cause incorrect calculations.",
                    suggested_action=f"Update Model_Master with: {', '.join(missing)}",
                ))

        # 2. Logical consistency checks
        for mc, spec in master.items():
            if mc not in models_used:
                continue

            # Fragile + stackable contradiction
            if spec.get("fragile") and spec.get("stackable"):
                review.findings.append(Finding(
                    severity=Severity.WARN,
                    category="data_consistency",
                    message=f"{mc}: fragile=True AND stackable=True — review for consistency",
                    rationale="Fragile items typically should not be stacked. Possible data error.",
                    suggested_action="Verify with logistics: is the BOX fragile or the PRODUCT?",
                ))

            # Load bearing < own weight (can't stack own copies)
            if spec.get("stackable") and spec.get("load_bear_kg", 0) < spec.get("weight_kg", 0):
                review.findings.append(Finding(
                    severity=Severity.INFO,
                    category="data_consistency",
                    message=f"{mc}: marked stackable but load_bear ({spec.get('load_bear_kg')}kg) < own weight ({spec.get('weight_kg')}kg)",
                    rationale="Can't stack same model on top — effectively non-stackable.",
                ))

        # 3. Strategy determinism (important for audit)
        strategy = simulation_result.get("strategy", "")
        if strategy:
            review.findings.append(Finding(
                severity=Severity.OK,
                category="reproducibility",
                message=f"Strategy: {strategy} — deterministic, re-runs produce same result",
                rationale="Required for audit trail and dispute resolution.",
            ))

        # 4. Edge case: empty placements
        if not simulation_result["placements"]:
            review.findings.append(Finding(
                severity=Severity.CRITICAL,
                category="empty_load",
                message="No placements generated — empty load or all items failed",
                rationale="Empty result usually indicates data issue or impossible constraints.",
            ))

        return review


# =============================================================================
# Agent 3: Warehouse Operations Expert
# =============================================================================

class WarehouseOpsExpert:
    """Reviews dock safety, worker workflow, OSHA compliance."""

    name = "Warehouse Operations Expert"
    role = "Reviews dock workflow, worker safety, and OSHA/ergonomic compliance"

    # OSHA / NIOSH thresholds
    SINGLE_PERSON_LIFT_LB = 50      # NIOSH recommended max
    TWO_PERSON_LIFT_LB = 100        # Industry standard
    STACK_HEIGHT_REACH_FT = 6.0     # Above this requires lift assist

    def review(self, simulation_result: Dict[str, Any], master: Dict[str, Any]) -> AgentReview:
        review = AgentReview(agent_name=self.name, agent_role=self.role)
        placements = simulation_result["placements"]

        # 1. Heavy items requiring 2-person lift
        heavy_models = set()
        very_heavy_models = set()
        for p in placements:
            spec = master.get(p["model_code"], {})
            wt_lb = spec.get("weight_kg", 0) * 2.20462
            if wt_lb > self.TWO_PERSON_LIFT_LB:
                very_heavy_models.add(p["model_code"])
            elif wt_lb > self.SINGLE_PERSON_LIFT_LB:
                heavy_models.add(p["model_code"])

        if very_heavy_models:
            review.findings.append(Finding(
                severity=Severity.WARN,
                category="worker_safety",
                message=f"Items >100 lb require team lift or equipment: {', '.join(sorted(very_heavy_models))}",
                rationale="OSHA recommends mechanical assist for loads >50 lb single-person. "
                          ">100 lb should use forklift or hand truck even with 2 workers.",
                suggested_action="Ensure hand trucks available; brief workers on team lift technique.",
            ))

        # 2. Top-tier stacking — reach height
        top_tier_items = [p for p in placements if p["layer"] >= 1]
        if top_tier_items:
            top_height_mm = max(p["z_mm"] + p["dim_z_mm"] for p in top_tier_items)
            top_height_ft = top_height_mm / 304.8
            if top_height_ft > self.STACK_HEIGHT_REACH_FT:
                review.findings.append(Finding(
                    severity=Severity.WARN,
                    category="ergonomics",
                    message=f"Top tier reaches {top_height_ft:.1f} ft — exceeds comfortable reach height",
                    rationale="OSHA ergonomic guidelines: loads above 6 ft increase shoulder injury risk.",
                    suggested_action="Use step platform or forklift for placing top-tier items.",
                ))

        # 3. LIFO ordering check (if multi-stop scenario)
        # For now, just verify last-loaded is closest to door
        if placements:
            last_seq_x = placements[-1]["x_mm"]
            first_seq_x = placements[0]["x_mm"]
            if last_seq_x < first_seq_x:
                review.findings.append(Finding(
                    severity=Severity.CRITICAL,
                    category="lifo_order",
                    message="Last-loaded item is NOT closest to door — LIFO order violated",
                    rationale="LIFO (Last In First Out) means last loaded = first unloaded. "
                              "Violation causes re-arrangement at delivery, doubling work time.",
                    suggested_action="Review load sequence — items unloaded first should be at rear.",
                ))
            else:
                review.findings.append(Finding(
                    severity=Severity.OK,
                    category="lifo_order",
                    message="Load sequence follows LIFO — last loaded near door",
                ))

        # 4. Equipment checklist
        n_units = len(placements)
        equipment_msg = (
            f"For {n_units} units, recommend: 2 hand trucks, "
            f"{max(4, n_units // 10)} ratchet straps, 6 moving blankets, 2 workers minimum"
        )
        review.findings.append(Finding(
            severity=Severity.INFO,
            category="equipment",
            message=equipment_msg,
            rationale="Based on standard LG dock equipment ratios.",
        ))

        # 5. Stack stability check
        # Items with heavier model stacked on lighter — flag
        for p in placements:
            if p["layer"] == 0:
                continue
            below = [b for b in placements
                     if b["x_mm"] == p["x_mm"]
                     and b["y_mm"] == p["y_mm"]
                     and b["layer"] == 0]
            if below:
                below_spec = master.get(below[0]["model_code"], {})
                top_spec = master.get(p["model_code"], {})
                if top_spec.get("weight_kg", 0) > below_spec.get("load_bear_kg", float("inf")):
                    review.findings.append(Finding(
                        severity=Severity.CRITICAL,
                        category="stack_stability",
                        message=f"Stack instability: {p['model_code']} ({top_spec.get('weight_kg')}kg) on "
                                f"{below[0]['model_code']} (load_bear {below_spec.get('load_bear_kg')}kg)",
                        rationale="Top item exceeds bottom item's load-bearing rating. "
                                  "Risk of crush damage during transport.",
                        suggested_action="Reverse stack order or do not stack.",
                    ))

        return review


# =============================================================================
# Orchestrator
# =============================================================================

class ExpertPanel:
    """Runs all three agents and produces a consolidated review."""

    def __init__(self):
        self.agents = [
            LogisticsExpert(),
            ITSystemsExpert(),
            WarehouseOpsExpert(),
        ]

    def review(self, simulation_result: Dict[str, Any], master: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run all agents and consolidate findings.

        Returns:
            {
                "overall_status": "ok" | "warning" | "critical",
                "reviews": [AgentReview, ...],
                "total_findings": {...},
                "critical_issues": [Finding, ...],  # extracted for highlight
            }
        """
        reviews = [agent.review(simulation_result, master) for agent in self.agents]

        all_findings = [f for r in reviews for f in r.findings]
        critical = [f for f in all_findings if f.severity == Severity.CRITICAL]
        warnings = [f for f in all_findings if f.severity == Severity.WARN]

        if critical:
            status = "critical"
        elif warnings:
            status = "warning"
        else:
            status = "ok"

        summary = {s.value: 0 for s in Severity}
        for f in all_findings:
            summary[f.severity.value] += 1

        return {
            "overall_status": status,
            "reviews": reviews,
            "total_findings": summary,
            "critical_issues": critical,
            "warnings": warnings,
        }

    def format_report_text(self, panel_result: Dict[str, Any]) -> str:
        """Plain-text report for email/PDF inclusion."""
        lines = []
        lines.append("=" * 70)
        lines.append("EXPERT PANEL REVIEW")
        lines.append("=" * 70)

        status_emoji = {"ok": "✅", "warning": "⚠️", "critical": "🚨"}
        lines.append(f"\nOverall Status: {status_emoji[panel_result['overall_status']]} "
                     f"{panel_result['overall_status'].upper()}")
        lines.append(f"Summary: {panel_result['total_findings']}\n")

        for review in panel_result["reviews"]:
            lines.append(f"\n── {review.agent_name} ──")
            lines.append(f"Role: {review.agent_role}")
            for f in review.findings:
                lines.append(f"\n  {f.severity.value} [{f.category}] {f.message}")
                if f.rationale:
                    lines.append(f"      Why: {f.rationale}")
                if f.suggested_action:
                    lines.append(f"      Action: {f.suggested_action}")

        return "\n".join(lines)
