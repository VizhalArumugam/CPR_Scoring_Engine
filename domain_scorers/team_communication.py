"""
domain_scorers/team_communication.py
--------------------------------------
Scores the Team Communication domain (10 pts max, weight = 10%).

Data sources:
  - FSM / NLP findings filtered by domain = "team_communication"
  - Ceiling mic transcript (transcripts["ceiling"]) — Full room audio

Sub-signals (from acls.yaml sub_signal_weights.team_communication):
  - closed_loop_rate     : 6 pts (% of orders with explicit verbal confirmation within 30s)
  - callout_completeness : 4 pts (% of critical events verbalized to the team)

FSM / NLP Finding Types this scorer reads:
  - CLOSED_LOOP_FAILURE      → closed_loop_rate deduction
  - CRITICAL_CALLOUT_MISSED  → callout_completeness deduction
  - UNANSWERED_ORDER         → closed_loop_rate deduction

Completeness:
  completeness = ceiling_active_seconds / total_session_seconds
  Flag if < 0.70 → PARTIAL_DATA
"""

from __future__ import annotations

from typing import Dict, List

from scoring.completeness_flags import get_completeness_flag, get_completeness_note
from scoring.confidence import CICalculator
from scoring.domain_scorers.base_scorer import BaseDomainScorer
from scoring.schemas.event_schema import (
    FindingRecord,
    Severity,
    TranscriptSegment,
    UnifiedTimeline,
)
from scoring.score_models import DomainScore, SubSignalScore


_FINDING_TO_SUBSIGNAL = {
    "CLOSED_LOOP_FAILURE":     "closed_loop_rate",
    "UNANSWERED_ORDER":        "closed_loop_rate",
    "CRITICAL_CALLOUT_MISSED": "callout_completeness",
}

_SEVERITY_PENALTY = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH:     0.80,
    Severity.MODERATE: 0.50,
    Severity.LOW:      0.20,
    Severity.INFO:     0.00,
}

_MIN_CEILING_COVERAGE = 0.70


class TeamCommunicationScorer(BaseDomainScorer):

    domain_key   = "team_communication"
    domain_label = "Team Communication"
    max_points   = 10.0
    weight       = 0.10

    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute Team Communication score.

        TODO (when real data is available):
          - Wire in actual ceiling TranscriptSegment list from diarization.py.
          - Wire in NLP communication findings (CLOSED_LOOP_FAILURE, etc.)
            produced by nlp_engine.py.
          - Ensure UnifiedEvent.verbalized_by_team is populated by event_extractor.
        """
        ci_calc = CICalculator(self.config.get("confidence_interval"))
        domain_findings = self._filter_findings(findings)
        ceiling_segments: List[TranscriptSegment] = transcripts.get("ceiling", [])

        sub_signal_keys = ["closed_loop_rate", "callout_completeness"]
        sub_signal_deductions: Dict[str, float] = {k: 0.0 for k in sub_signal_keys}
        sub_signal_finding_ids: Dict[str, List[str]] = {k: [] for k in sub_signal_keys}
        sub_signal_severities: Dict[str, str] = {k: "" for k in sub_signal_keys}

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

        # ── Completeness from ceiling mic coverage ────────────────────────
        completeness = _compute_ceiling_completeness(ceiling_segments, timeline.total_duration_ms)
        min_coverage = self._get_threshold("min_mic_coverage_for_valid_score", _MIN_CEILING_COVERAGE)
        detail = (
            "Ceiling mic coverage insufficient for reliable communication scoring."
            if completeness < min_coverage else ""
        )
        flag = get_completeness_flag(completeness)
        note = get_completeness_note(flag, self.domain_label, completeness, detail)
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


def _compute_ceiling_completeness(
    ceiling_segments: List[TranscriptSegment],
    total_duration_ms: int,
) -> float:
    """
    Completeness = ceiling_active_ms / total_session_ms.
    TODO: Replace stub with real segment duration calculation.
    """
    if not ceiling_segments or total_duration_ms == 0:
        return 0.0
    ceiling_active_ms = sum((s.end_ms - s.start_ms) for s in ceiling_segments)
    return min(ceiling_active_ms / total_duration_ms, 1.0)


def _update_severity(tracker: dict, key: str, severity: Severity) -> None:
    _order = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MODERATE: 2, Severity.LOW: 1, Severity.INFO: 0}
    if not tracker.get(key) or _order.get(severity, 0) > _order.get(tracker[key], 0):
        tracker[key] = severity.value
