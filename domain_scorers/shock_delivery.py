"""
domain_scorers/shock_delivery.py
---------------------------------
Scores the Shock Delivery domain (15 pts max, weight = 15%).

Data sources:
  - FSM findings filtered by domain = "shock_delivery"
  - SHOCK_DELIVERED and RHYTHM_IDENTIFIED events in UnifiedTimeline

Sub-signals (from acls.yaml sub_signal_weights.shock_delivery):
  - time_to_first_shock    : 10 pts (shock within 120s of shockable rhythm)
  - inter_shock_interval   :  6 pts (2-min CPR cycle between shocks)
  - shock_appropriateness  :  4 pts (no shock in PEA/asystole, no missed shocks)

FSM Finding Types this scorer reads:
  - TIME_TO_FIRST_SHOCK_DELAYED      → deduct from time_to_first_shock
  - INTER_SHOCK_INTERVAL_VIOLATION   → deduct from inter_shock_interval
  - SHOCK_IN_NON_SHOCKABLE_RHYTHM    → CRITICAL deduction from shock_appropriateness
  - SHOCK_OMITTED                    → deduction from shock_appropriateness

Completeness:
  completeness = fraction of shock events with confirmed timestamps
  (high-confidence NLP extraction vs. inferred/uncertain timestamps)
"""

from __future__ import annotations

from typing import Dict, List

from scoring.completeness_flags import get_completeness_flag, get_completeness_note
from scoring.confidence import CICalculator
from scoring.domain_scorers.base_scorer import BaseDomainScorer
from scoring.schemas.event_schema import (
    EventType,
    FindingRecord,
    Severity,
    TranscriptSegment,
    UnifiedTimeline,
)
from scoring.score_models import DomainScore, SubSignalScore


_FINDING_TO_SUBSIGNAL = {
    "TIME_TO_FIRST_SHOCK_DELAYED":    "time_to_first_shock",
    "INTER_SHOCK_INTERVAL_VIOLATION": "inter_shock_interval",
    "SHOCK_IN_NON_SHOCKABLE_RHYTHM":  "shock_appropriateness",
    "SHOCK_OMITTED":                  "shock_appropriateness",
}

_SEVERITY_PENALTY = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH:     0.80,
    Severity.MODERATE: 0.50,
    Severity.LOW:      0.20,
    Severity.INFO:     0.00,
}


class ShockDeliveryScorer(BaseDomainScorer):

    domain_key   = "shock_delivery"
    domain_label = "Shock Delivery"
    max_points   = 15.0
    weight       = 0.15

    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute Shock Delivery score from FSM findings and shock event timestamps.

        TODO (when real data is available):
          - Wire in actual SHOCK_DELIVERED and RHYTHM_IDENTIFIED event timestamps.
          - Wire in actual FSM findings from acls_fsm.py.
        """
        ci_calc = CICalculator(self.config.get("confidence_interval"))
        domain_findings = self._filter_findings(findings)

        sub_signal_deductions: Dict[str, float] = {k: 0.0 for k in set(_FINDING_TO_SUBSIGNAL.values())}
        sub_signal_finding_ids: Dict[str, List[str]] = {k: [] for k in sub_signal_deductions}
        sub_signal_severities: Dict[str, str] = {k: "" for k in sub_signal_deductions}

        # ── Apply FSM finding penalties ───────────────────────────────────
        for finding in domain_findings:
            subsignal = _FINDING_TO_SUBSIGNAL.get(finding.title)
            if subsignal is None:
                continue
            max_pts = self._get_sub_signal_max(subsignal, fallback=4.0)
            penalty = _SEVERITY_PENALTY.get(finding.severity, 0.0) * max_pts
            sub_signal_deductions[subsignal] = min(
                sub_signal_deductions[subsignal] + penalty, max_pts
            )
            sub_signal_finding_ids[subsignal].append(finding.finding_id)
            _update_severity(sub_signal_severities, subsignal, finding.severity)

        # ── Build sub-signals ─────────────────────────────────────────────
        sub_signals = []
        total_deducted = 0.0
        all_finding_ids = []

        for signal_key, deduction in sub_signal_deductions.items():
            max_pts = self._get_sub_signal_max(signal_key, fallback=4.0)
            ss = SubSignalScore(
                name=signal_key,
                points_deducted=round(deduction, 2),
                points_possible=max_pts,
                finding_ids=sub_signal_finding_ids[signal_key],
                severity=sub_signal_severities.get(signal_key, ""),
            )
            sub_signals.append(ss)
            total_deducted += deduction
            all_finding_ids.extend(sub_signal_finding_ids[signal_key])

        final_score = self._clamp(self.max_points - total_deducted, lo=0.0, hi=self.max_points)

        # ── Completeness: fraction of shock events with confirmed timestamps ──
        completeness = _compute_shock_completeness(timeline)
        flag = get_completeness_flag(completeness)
        note = get_completeness_note(flag, self.domain_label, completeness)
        ci_lower, ci_upper = ci_calc.compute_domain_ci(final_score, self.max_points, completeness)

        return DomainScore(
            domain_key=self.domain_key,
            domain_label=self.domain_label,
            weight=self.weight,
            max_points=self.max_points,
            final_score=round(final_score, 2),
            completeness=round(completeness, 3),
            completeness_flag=flag,
            completeness_note=note,
            ci_lower=round(ci_lower, 2),
            ci_upper=round(ci_upper, 2),
            sub_signals=sub_signals,
            fsm_findings_applied=list(set(all_finding_ids)),
        )


def _compute_shock_completeness(timeline: UnifiedTimeline) -> float:
    """
    Completeness = fraction of SHOCK_DELIVERED events with confirmed timestamps.
    TODO: Replace with real check against has_confirmed_timestamp when event data is available.
    """
    shocks = timeline.get_events_by_type(EventType.SHOCK_DELIVERED)
    if not shocks:
        return 0.5   # No shocks at all — ambiguous; treat as partial
    confirmed = sum(1 for s in shocks if s.has_confirmed_timestamp)
    return confirmed / len(shocks)


def _update_severity(tracker: dict, key: str, severity: Severity) -> None:
    _order = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MODERATE: 2, Severity.LOW: 1, Severity.INFO: 0}
    if not tracker.get(key) or _order.get(severity, 0) > _order.get(tracker[key], 0):
        tracker[key] = severity.value
