"""
domain_scorers/cpr_quality.py
------------------------------
Scores the CPR Quality domain (30 pts max, weight = 30%).

Data sources (no SimMan):
  - FSM findings filtered by domain = "cpr_quality"
  - CPR_STARTED / CPR_PAUSED / CPR_RESUMED events in UnifiedTimeline

Sub-signals (from acls.yaml sub_signal_weights.cpr_quality):
  - cpr_start_delayed     : 8 pts  (CPR not started within 10 s of arrest)
  - cpr_pause_excessive   : 8 pts  (any single pause > 10 s)
  - cpr_fraction_low      : 8 pts  (CCF calculated from timeline event timestamps)
  - cpr_rate_deviation    : 6 pts  (team or coach verbalized wrong rate)

CCF Calculation (from timeline, not SimMan):
  CCF = total_compression_time / total_arrest_time
  total_compression_time = sum of (CPR_RESUMED.ts - CPR_PAUSED.ts) intervals
  total_arrest_time      = ROSC_ACHIEVED.ts - ARREST_RECOGNISED.ts (or session end)

FSM Finding Types this scorer reads:
  - CPR_START_DELAYED         → deduct from cpr_start_delayed sub-signal
  - CPR_PAUSE_EXCESSIVE       → deduct from cpr_pause_excessive sub-signal
  - CPR_FRACTION_LOW          → deduct from cpr_fraction_low sub-signal
  - CPR_RATE_DEVIATION        → deduct from cpr_rate_deviation sub-signal
  - CPR_DEPTH_DEVIATION       → shared deduction across rate/depth signals

Completeness:
  completeness = fraction of arrest duration covered by CPR events in timeline
  (if no CPR events exist at all → completeness = 0.0 → LOW_DATA)
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


# FSM finding title keywords mapped to sub-signals
_FINDING_TO_SUBSIGNAL = {
    "CPR_START_DELAYED":    "cpr_start_delayed",
    "CPR_PAUSE_EXCESSIVE":  "cpr_pause_excessive",
    "CPR_FRACTION_LOW":     "cpr_fraction_low",
    "CPR_RATE_DEVIATION":   "cpr_rate_deviation",
    "CPR_DEPTH_DEVIATION":  "cpr_rate_deviation",   # bundled into same signal for now
}

# Severity → penalty fraction of sub-signal max
_SEVERITY_PENALTY = {
    Severity.CRITICAL: 1.00,
    Severity.HIGH:     0.80,
    Severity.MODERATE: 0.50,
    Severity.LOW:      0.20,
    Severity.INFO:     0.00,
}


class CprQualityScorer(BaseDomainScorer):
    """
    Scores CPR Quality from FSM findings and timeline CPR events.
    No SimMan hardware required.
    """

    domain_key   = "cpr_quality"
    domain_label = "CPR Quality"
    max_points   = 30.0
    weight       = 0.30

    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute CPR Quality score.

        Steps:
          1. Filter FSM findings for this domain.
          2. Score each sub-signal by accumulating penalties from findings.
          3. Estimate CCF from CPR pause/resume event timestamps.
          4. Compute completeness from CPR event coverage.
          5. Return DomainScore.

        TODO (when real data is available):
          - Wire in actual CPR_PAUSED / CPR_RESUMED event timestamps for CCF.
          - Wire in actual FSM findings list from acls_fsm.py output.
        """
        ci_calc = CICalculator(self.config.get("confidence_interval"))
        domain_findings = self._filter_findings(findings)

        # ── Sub-signal containers ─────────────────────────────────────────
        sub_signal_deductions: Dict[str, float] = {k: 0.0 for k in _FINDING_TO_SUBSIGNAL.values()}
        sub_signal_finding_ids: Dict[str, List[str]] = {k: [] for k in sub_signal_deductions}
        sub_signal_severities: Dict[str, str] = {k: "" for k in sub_signal_deductions}

        # ── Step 1: Apply FSM finding penalties ───────────────────────────
        for finding in domain_findings:
            subsignal = _FINDING_TO_SUBSIGNAL.get(finding.title, None)
            if subsignal is None:
                continue

            max_pts = self._get_sub_signal_max(subsignal, fallback=6.0)
            penalty_fraction = _SEVERITY_PENALTY.get(finding.severity, 0.0)
            deduction = penalty_fraction * max_pts

            sub_signal_deductions[subsignal] = min(
                sub_signal_deductions[subsignal] + deduction,
                max_pts   # Cannot deduct more than the sub-signal's max
            )
            sub_signal_finding_ids[subsignal].append(finding.finding_id)

            # Track worst severity per sub-signal
            current = sub_signal_severities.get(subsignal, "")
            if not current or _severity_rank(finding.severity) > _severity_rank(current):
                sub_signal_severities[subsignal] = finding.severity.value

        # ── Step 2: Calculate CCF from timeline events ────────────────────
        # TODO: Replace stub with real CPR pause/resume event timestamps
        ccf = _estimate_ccf_from_timeline(timeline)
        ccf_target = self._get_threshold("ccf_target", 0.80)
        if ccf < ccf_target:
            max_pts = self._get_sub_signal_max("cpr_fraction_low", 8.0)
            shortfall = (ccf_target - ccf) / ccf_target
            extra_deduction = min(shortfall * max_pts, max_pts)
            sub_signal_deductions["cpr_fraction_low"] = min(
                sub_signal_deductions["cpr_fraction_low"] + extra_deduction,
                max_pts
            )

        # ── Step 3: Build SubSignalScore objects ──────────────────────────
        sub_signals = []
        total_deducted = 0.0
        all_finding_ids = []

        for signal_key, deduction in sub_signal_deductions.items():
            max_pts = self._get_sub_signal_max(signal_key, 6.0)
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

        # ── Step 4: Compute final score ───────────────────────────────────
        final_score = self._clamp(self.max_points - total_deducted, lo=0.0, hi=self.max_points)

        # ── Step 5: Compute completeness ──────────────────────────────────
        completeness = _compute_cpr_completeness(timeline)
        flag = get_completeness_flag(completeness)
        note = get_completeness_note(flag, self.domain_label, completeness)

        # ── Step 6: CI ───────────────────────────────────────────────────
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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _estimate_ccf_from_timeline(timeline: UnifiedTimeline) -> float:
    """
    Estimate Chest Compression Fraction from CPR event timestamps.

    CCF = total_compression_seconds / total_arrest_seconds

    TODO: This is a stub. When real CPR_PAUSED/CPR_RESUMED events are in the
          timeline, replace the stub return with actual interval calculation.
    """
    arrest_start = timeline.arrest_start_ms
    arrest_end   = timeline.rosc_ms or timeline.total_duration_ms

    if arrest_start is None or arrest_end <= arrest_start:
        return 1.0   # Cannot calculate — assume full compressions (no penalty)

    total_arrest_ms = arrest_end - arrest_start

    pauses = timeline.get_events_by_type(EventType.CPR_PAUSED)
    resumes = timeline.get_events_by_type(EventType.CPR_RESUMED)

    if not pauses:
        return 1.0   # No pauses recorded → CCF = 1.0 (stub — replace with real data)

    # Pair each pause with the next resume
    total_pause_ms = 0
    resume_times = sorted(r.timestamp_ms for r in resumes)
    for pause in pauses:
        next_resume = next((r for r in resume_times if r > pause.timestamp_ms), None)
        if next_resume:
            total_pause_ms += (next_resume - pause.timestamp_ms)

    ccf = 1.0 - (total_pause_ms / total_arrest_ms)
    return max(0.0, min(1.0, ccf))


def _compute_cpr_completeness(timeline: UnifiedTimeline) -> float:
    """
    Measure completeness as fraction of arrest duration with CPR event coverage.

    TODO: Refine when real CPR event data is available.
    Currently: 1.0 if any CPR events exist, 0.0 if none.
    """
    cpr_events = (
        timeline.get_events_by_type(EventType.CPR_STARTED)
        + timeline.get_events_by_type(EventType.CPR_PAUSED)
        + timeline.get_events_by_type(EventType.CPR_RESUMED)
    )
    if not cpr_events:
        return 0.0

    # TODO: Replace with actual interval coverage calculation
    return min(len(cpr_events) / 5.0, 1.0)   # Rough proxy until real data available


def _severity_rank(severity) -> int:
    """Numeric rank of severity for worst-severity tracking."""
    order = {
        Severity.CRITICAL: 4,
        Severity.HIGH: 3,
        Severity.MODERATE: 2,
        Severity.LOW: 1,
        Severity.INFO: 0,
        "CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "LOW": 1, "INFO": 0,
    }
    return order.get(severity, 0)


def _get_signal_notes(signal_key: str, deduction: float, max_pts: float) -> str:
    if deduction == 0:
        return f"No deductions — {signal_key} within target."
    return f"{signal_key}: -{deduction:.1f}/{max_pts:.1f} pts from FSM findings."
