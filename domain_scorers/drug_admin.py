"""
domain_scorers/drug_admin.py
-----------------------------
Scores the Drug Administration domain (20 pts max, weight = 20%).

Data sources:
  - FSM findings filtered by domain = "drug_administration"
  - DRUG_ADMINISTERED events in UnifiedTimeline (with nlp_confidence)

Sub-signals (from acls.yaml sub_signal_weights.drug_administration):
  - epi_first_dose_non_shockable : 6 pts (Epi within 3 min for PEA/asystole)
  - epi_first_dose_shockable     : 5 pts (Epi after 1st shock, within 5 min for VF/pVT)
  - epi_repeat_interval          : 5 pts (repeat Epi every 3–5 min)
  - antiarrhythmic_timing        : 4 pts (Amiodarone/Lidocaine after 3rd shock in VF/pVT)

FSM Finding Types this scorer reads:
  - EPI_FIRST_DOSE_DELAYED          → epi_first_dose_non_shockable or epi_first_dose_shockable
  - EPI_REPEAT_INTERVAL_VIOLATION   → epi_repeat_interval
  - ANTIARRHYTHMIC_OMITTED          → antiarrhythmic_timing
  - ANTIARRHYTHMIC_CONTRAINDICATED  → antiarrhythmic_timing (CRITICAL)
  - WRONG_DOSE_ADMINISTERED         → whichever sub-signal is relevant

Completeness:
  completeness = mean(nlp_confidence) across all DRUG_ADMINISTERED events in timeline
  (reflects how confidently the NLP extracted drug events from audio)
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
    "EPI_FIRST_DOSE_DELAYED":          "epi_first_dose_non_shockable",
    "EPI_FIRST_DOSE_DELAYED_SHOCKABLE": "epi_first_dose_shockable",
    "EPI_REPEAT_INTERVAL_VIOLATION":   "epi_repeat_interval",
    "ANTIARRHYTHMIC_OMITTED":          "antiarrhythmic_timing",
    "ANTIARRHYTHMIC_CONTRAINDICATED":  "antiarrhythmic_timing",
    "WRONG_DOSE_ADMINISTERED":         "epi_first_dose_non_shockable",
}

_SEVERITY_PENALTY = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH:     0.80,
    Severity.MODERATE: 0.50,
    Severity.LOW:      0.20,
    Severity.INFO:     0.00,
}


class DrugAdminScorer(BaseDomainScorer):

    domain_key   = "drug_administration"
    domain_label = "Drug Administration"
    max_points   = 20.0
    weight       = 0.20

    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute Drug Administration score.

        TODO (when real data is available):
          - Wire in actual DRUG_ADMINISTERED events from event_extractor.py.
          - Ensure UnifiedEvent.nlp_confidence is populated by event_extractor.
          - Ensure findings carry delay_seconds for linear-decay scoring (optional enhancement).
        """
        ci_calc = CICalculator(self.config.get("confidence_interval"))
        domain_findings = self._filter_findings(findings)

        sub_signal_keys = [
            "epi_first_dose_non_shockable",
            "epi_first_dose_shockable",
            "epi_repeat_interval",
            "antiarrhythmic_timing",
        ]
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

        # ── Completeness from NLP confidence on drug events ───────────────
        completeness = _compute_drug_completeness(timeline)
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


def _compute_drug_completeness(timeline: UnifiedTimeline) -> float:
    """
    Completeness = mean NLP confidence across all drug administration events.
    TODO: Replace stub with real nlp_confidence values from event_extractor.
    """
    drug_events = timeline.get_events_by_type(EventType.DRUG_ADMINISTERED)
    if not drug_events:
        return 0.3   # No drug events detected — LOW_DATA
    confidences = [e.nlp_confidence for e in drug_events]
    return sum(confidences) / len(confidences)


def _update_severity(tracker: dict, key: str, severity: Severity) -> None:
    _order = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MODERATE: 2, Severity.LOW: 1, Severity.INFO: 0}
    if not tracker.get(key) or _order.get(severity, 0) > _order.get(tracker[key], 0):
        tracker[key] = severity.value
