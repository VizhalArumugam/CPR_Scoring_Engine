"""
domain_scorers/rhythm_recognition.py
--------------------------------------
Scores the Rhythm Recognition domain (10 pts max, weight = 10%).

Data sources:
  - FSM findings filtered by domain = "rhythm_recognition"
  - RHYTHM_IDENTIFIED events in UnifiedTimeline

Sub-signals (from acls.yaml sub_signal_weights.rhythm_recognition):
  - vf_pvt_response_lag     : 5 pts (lag from VF/pVT identified → shock order ≤ 30s)
  - pea_asystole_response_lag : 5 pts (lag from PEA/asystole → Epi order ≤ 60s)

FSM Finding Types this scorer reads:
  - VF_RESPONSE_DELAYED                → vf_pvt_response_lag
  - PEA_RESPONSE_DELAYED               → pea_asystole_response_lag
  - RHYTHM_CHANGE_CALLOUT_MISSED       → splits deduction across both sub-signals
  - INAPPROPRIATE_RHYTHM_CLASSIFICATION → high penalty on whichever lag applies

Completeness:
  completeness = fraction of RHYTHM_IDENTIFIED events with has_confirmed_timestamp = True
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
    "VF_RESPONSE_DELAYED":                 "vf_pvt_response_lag",
    "PEA_RESPONSE_DELAYED":                "pea_asystole_response_lag",
    "RHYTHM_CHANGE_CALLOUT_MISSED":        "vf_pvt_response_lag",   # split or apply to both
    "INAPPROPRIATE_RHYTHM_CLASSIFICATION": "vf_pvt_response_lag",
}

_SEVERITY_PENALTY = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH:     0.80,
    Severity.MODERATE: 0.50,
    Severity.LOW:      0.20,
    Severity.INFO:     0.00,
}


class RhythmRecognitionScorer(BaseDomainScorer):

    domain_key   = "rhythm_recognition"
    domain_label = "Rhythm Recognition"
    max_points   = 10.0
    weight       = 0.10

    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute Rhythm Recognition score.

        TODO (when real data is available):
          - Wire in actual RHYTHM_IDENTIFIED events with confirmed timestamps.
          - Wire in rhythm type (VF vs PEA) per event for correct sub-signal routing.
        """
        ci_calc = CICalculator(self.config.get("confidence_interval"))
        domain_findings = self._filter_findings(findings)

        sub_signal_keys = ["vf_pvt_response_lag", "pea_asystole_response_lag"]
        sub_signal_deductions: Dict[str, float] = {k: 0.0 for k in sub_signal_keys}
        sub_signal_finding_ids: Dict[str, List[str]] = {k: [] for k in sub_signal_keys}
        sub_signal_severities: Dict[str, str] = {k: "" for k in sub_signal_keys}

        for finding in domain_findings:
            subsignal = _FINDING_TO_SUBSIGNAL.get(finding.title)
            if subsignal is None:
                continue
            max_pts = self._get_sub_signal_max(subsignal, fallback=5.0)
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
            max_pts = self._get_sub_signal_max(signal_key, fallback=5.0)
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
        completeness = _compute_rhythm_completeness(timeline)
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


def _compute_rhythm_completeness(timeline: UnifiedTimeline) -> float:
    """
    Completeness = fraction of RHYTHM_IDENTIFIED events with confirmed timestamps.
    TODO: Replace stub with real has_confirmed_timestamp check.
    """
    rhythm_events = timeline.get_events_by_type(EventType.RHYTHM_IDENTIFIED)
    if not rhythm_events:
        return 0.3
    confirmed = sum(1 for e in rhythm_events if e.has_confirmed_timestamp)
    return confirmed / len(rhythm_events)


def _update_severity(tracker: dict, key: str, severity: Severity) -> None:
    _order = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MODERATE: 2, Severity.LOW: 1, Severity.INFO: 0}
    if not tracker.get(key) or _order.get(severity, 0) > _order.get(tracker[key], 0):
        tracker[key] = severity.value
