"""
domain_scorers/reversible_causes.py
-------------------------------------
Scores the Reversible Causes (H's & T's) domain (10 pts max, weight = 10%).

Clinical Context (AHA 2025):
  During cardiac arrest, the team leader must proactively identify and treat
  the underlying reversible causes — the H's and T's — while CPR is ongoing.

  H's:  Hypovolemia, Hypoxia, Hydrogen ions (Acidosis),
        Hypo/Hyperkalemia, Hypothermia
  T's:  Toxins, Tamponade (cardiac), Tension pneumothorax,
        Thrombosis coronary (MI), Thrombosis pulmonary (PE)

When should H's & T's be addressed? (from AHA Adult Cardiac Arrest Algorithm):
  - Shockable rhythms (VF/pVT): Expected by Shock #3 cycle (~4-6 min)
  - Non-Shockable rhythms (PEA/Asystole): Expected at 2nd CPR cycle (~2-4 min)
    because fixing the cause is the ONLY way to restore rhythm.

Data sources:
  - FSM findings filtered by domain = "reversible_causes"
  - HS_TS_DISCUSSED events in UnifiedTimeline (from NLP/Communication Engine)

Sub-signals (10 pts total):
  - hs_ts_discussed         : 4 pts  (Team verbalized the H's & T's at all)
  - hs_ts_timing            : 4 pts  (Discussed at the clinically correct time)
  - hs_ts_treatment_initiated : 2 pts (A specific cause was named AND action ordered)

FSM Finding Types this scorer reads:
  - HS_TS_OMITTED        -> hs_ts_discussed (CRITICAL — never addressed)
  - HS_TS_DELAYED        -> hs_ts_timing    (HIGH — addressed too late)
  - HS_TS_INCOMPLETE     -> hs_ts_discussed (MODERATE — vague mention, not specific)
  - HS_TS_NO_TREATMENT   -> hs_ts_treatment_initiated (MODERATE — named but no action)

Completeness:
  completeness = 1.0 if any HS_TS_DISCUSSED event exists in timeline, else 0.0
  (No mention at all => LOW_DATA, no basis to score timing or treatment)
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


# FSM finding title -> sub-signal mapping
_FINDING_TO_SUBSIGNAL = {
    "HS_TS_OMITTED":             "hs_ts_discussed",
    "HS_TS_INCOMPLETE":          "hs_ts_discussed",
    "HS_TS_DELAYED":             "hs_ts_timing",
    "HS_TS_NO_TREATMENT":        "hs_ts_treatment_initiated",
}

# Severity -> penalty fraction of sub-signal max
_SEVERITY_PENALTY = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH:     0.80,
    Severity.MODERATE: 0.50,
    Severity.LOW:      0.20,
    Severity.INFO:     0.00,
}

# Default fallback points per sub-signal (used if not in config YAML)
_DEFAULT_SUB_SIGNAL_MAX = {
    "hs_ts_discussed":           4.0,
    "hs_ts_timing":              4.0,
    "hs_ts_treatment_initiated": 2.0,
}


class ReversibleCausesScorer(BaseDomainScorer):
    """
    Scores whether the team identified and acted on reversible causes
    (H's and T's) at the appropriate time during the cardiac arrest scenario.

    Data flows:
      1. NLP/Communication Engine detects H's & T's discussion in audio
         and emits an EventType.HS_TS_DISCUSSED event into UnifiedTimeline.
      2. ACLS FSM evaluates the timing of that discussion against the
         current rhythm state and cycle count.
      3. FSM emits FindingRecords (HS_TS_OMITTED, HS_TS_DELAYED, etc.)
         into the findings list.
      4. This scorer reads both the timeline (for completeness) and the
         findings (for penalty deductions).
    """

    domain_key   = "reversible_causes"
    domain_label = "Reversible Causes (H's & T's)"
    max_points   = 10.0
    weight       = 0.10

    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute Reversible Causes score.

        Steps:
          1. Filter FSM findings for this domain.
          2. Apply penalty deductions per finding to each sub-signal.
          3. Compute completeness from HS_TS_DISCUSSED timeline events.
          4. Return DomainScore.

        TODO (when real data is available):
          - Wire in actual HS_TS_DISCUSSED events from event_extractor.py.
          - Wire in actual FSM findings from acls_fsm.py.
        """
        ci_calc = CICalculator(self.config.get("confidence_interval"))
        domain_findings = self._filter_findings(findings)

        # Sub-signal containers
        sub_signal_keys = list(_DEFAULT_SUB_SIGNAL_MAX.keys())
        sub_signal_deductions: Dict[str, float] = {k: 0.0 for k in sub_signal_keys}
        sub_signal_finding_ids: Dict[str, List[str]] = {k: [] for k in sub_signal_keys}
        sub_signal_severities: Dict[str, str] = {k: "" for k in sub_signal_keys}

        # Step 1: Apply FSM finding penalties
        for finding in domain_findings:
            subsignal = _FINDING_TO_SUBSIGNAL.get(finding.title)
            if subsignal is None:
                continue
            max_pts = self._get_sub_signal_max(
                subsignal, fallback=_DEFAULT_SUB_SIGNAL_MAX.get(subsignal, 2.0)
            )
            penalty = _SEVERITY_PENALTY.get(finding.severity, 0.0) * max_pts
            sub_signal_deductions[subsignal] = min(
                sub_signal_deductions[subsignal] + penalty, max_pts
            )
            sub_signal_finding_ids[subsignal].append(finding.finding_id)
            _update_severity(sub_signal_severities, subsignal, finding.severity)

        # Step 2: Build SubSignalScore objects
        sub_signals = []
        total_deducted = 0.0
        all_finding_ids = []

        for signal_key, deduction in sub_signal_deductions.items():
            max_pts = self._get_sub_signal_max(
                signal_key, fallback=_DEFAULT_SUB_SIGNAL_MAX.get(signal_key, 2.0)
            )
            ss = SubSignalScore(
                name=signal_key,
                points_deducted=round(deduction, 2),
                points_possible=max_pts,
                finding_ids=sub_signal_finding_ids[signal_key],
                severity=sub_signal_severities.get(signal_key, ""),
                notes=_get_signal_notes(signal_key, deduction, max_pts),
            )
            sub_signals.append(ss)
            total_deducted += deduction
            all_finding_ids.extend(sub_signal_finding_ids[signal_key])

        # Step 3: Final score
        final_score = self._clamp(
            self.max_points - total_deducted, lo=0.0, hi=self.max_points
        )

        # Step 4: Completeness — did the team discuss H's & T's at all?
        completeness = _compute_hsts_completeness(timeline)
        flag = get_completeness_flag(completeness)
        note = get_completeness_note(flag, self.domain_label, completeness)

        ci_lower, ci_upper = ci_calc.compute_domain_ci(
            final_score, self.max_points, completeness
        )

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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_hsts_completeness(timeline: UnifiedTimeline) -> float:
    """
    Completeness = 1.0 if any HS_TS_DISCUSSED event exists in timeline.
    0.0 means the NLP engine found no mention at all => LOW_DATA.

    TODO: Refine to check timing windows when real FSM data is available.
    """
    hsts_events = timeline.get_events_by_type(EventType.HS_TS_DISCUSSED)
    if not hsts_events:
        return 0.0   # Team never verbalized H's & T's -> LOW_DATA
    return 1.0


def _update_severity(tracker: dict, key: str, severity: Severity) -> None:
    _order = {
        Severity.CRITICAL: 4, Severity.HIGH: 3,
        Severity.MODERATE: 2, Severity.LOW: 1, Severity.INFO: 0,
    }
    if not tracker.get(key) or _order.get(severity, 0) > _order.get(tracker[key], 0):
        tracker[key] = severity.value


def _get_signal_notes(signal_key: str, deduction: float, max_pts: float) -> str:
    labels = {
        "hs_ts_discussed":           "H's & T's verbalized by team",
        "hs_ts_timing":              "H's & T's discussed at correct time per AHA algorithm",
        "hs_ts_treatment_initiated": "Specific cause named and treatment action ordered",
    }
    label = labels.get(signal_key, signal_key)
    if deduction == 0:
        return f"{label}: No deductions."
    return f"{label}: -{deduction:.1f}/{max_pts:.1f} pts from FSM findings."
