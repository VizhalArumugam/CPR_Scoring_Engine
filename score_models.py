"""
score_models.py
---------------
Dataclasses for the Scoring Engine output.

These are the objects the Scoring Engine produces and every downstream
module (pdf_generator, LLM narrative, trend engine) consumes.

Hierarchy:
    SubSignalScore        (one sub-metric within a domain)
        ↑ collected into
    DomainScore           (one domain's full result)
        ↑ collected into
    ScoreReport           (the complete session scoring output)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from scoring.schemas.event_schema import DataCompletenessFlag


@dataclass
class SubSignalScore:
    """
    The score for a single sub-metric within a domain.

    Example (CPR Quality domain):
        SubSignalScore(
            name="cpr_pause_excessive",
            points_deducted=4.5,
            points_possible=6.0,
            finding_ids=["fnd_cpr_pause_001", "fnd_cpr_pause_002"],
            severity="HIGH",
            notes="Two pauses exceeded 10 s (12.3 s and 14.7 s)",
        )
    """
    name: str                           # Sub-signal key (matches acls.yaml)
    points_deducted: float              # How many points were lost
    points_possible: float              # Maximum contribution of this sub-signal
    finding_ids: List[str] = field(default_factory=list)  # FSM finding IDs that caused deduction
    severity: str = ""                  # Worst severity among linked findings
    notes: str = ""                     # Human-readable explanation

    @property
    def points_earned(self) -> float:
        """Convenience: points earned = possible − deducted, floored at 0."""
        return max(self.points_possible - self.points_deducted, 0.0)


@dataclass
class DomainScore:
    """
    The complete scoring result for one domain.

    Produced by:  Each BaseDomainScorer subclass
    Consumed by:  scoring_engine.py (aggregation), pdf_generator.py, LLM narrative
    """
    domain_key: str                     # e.g. "cpr_quality"
    domain_label: str                   # e.g. "CPR Quality"
    weight: float                       # Domain weight (e.g. 0.30 for CPR Quality)
    max_points: float                   # e.g. 30.0

    final_score: float = 0.0            # Actual points earned (0 → max_points)
    completeness: float = 1.0           # Data completeness 0.0–1.0
    completeness_flag: DataCompletenessFlag = DataCompletenessFlag.COMPLETE
    completeness_note: str = ""         # PDF-ready explanation of the flag

    ci_lower: float = 0.0              # CI lower bound
    ci_upper: float = 0.0              # CI upper bound

    sub_signals: List[SubSignalScore] = field(default_factory=list)
    fsm_findings_applied: List[str] = field(default_factory=list)  # All finding_ids used

    # [PLANNED] For per-member debrief (Phase 3)
    responsible_roles: List[str] = field(default_factory=list)


@dataclass
class ScoreReport:
    """
    The complete output of the Scoring Engine for one simulation session.

    This is the single object passed to:
      - pdf_generator.py  → renders the full PDF report
      - ollama_api.py     → provides quantitative anchors for LLM narrative
      - [PLANNED] trend_engine.py → persisted for cohort comparison

    Fields are self-documenting. See scoring_engine.py for how they are populated.
    """
    # ── Overall result ──────────────────────────────────────────────────────
    overall_score: float                # 0.0 – 100.0
    grade: str                          # "A" | "B" | "C" | "D" | "F"
    grade_descriptor: str               # Human-readable grade explanation
    hard_fail_override: bool            # True if grade was capped by hard-fail rule
    hard_fail_reason: Optional[str]     # Explanation of cap (if any)

    # ── Overall CI ──────────────────────────────────────────────────────────
    ci_lower: float
    ci_upper: float

    # ── Domain breakdown ────────────────────────────────────────────────────
    domain_scores: List[DomainScore] = field(default_factory=list)

    # ── Data quality summary ─────────────────────────────────────────────────
    overall_completeness: float = 1.0   # Weighted mean of domain completeness scores
    has_low_data_domains: bool = False   # True if any domain is LOW_DATA

    # ── Metadata ────────────────────────────────────────────────────────────
    protocol_version: str = "AHA_2025_ACLS"
    session_id: str = ""
    session_duration_ms: int = 0
    scored_at_ms: int = 0

    def get_domain(self, domain_key: str) -> Optional[DomainScore]:
        """Look up a domain score by key."""
        return next((d for d in self.domain_scores if d.domain_key == domain_key), None)

    def summary_str(self) -> str:
        """One-line human-readable summary for logging / debugging."""
        hf = f" [HARD-FAIL: {self.hard_fail_reason}]" if self.hard_fail_override else ""
        return (
            f"Score: {self.overall_score:.1f}/100 | Grade: {self.grade}{hf} | "
            f"CI: [{self.ci_lower:.1f}, {self.ci_upper:.1f}] | "
            f"Completeness: {self.overall_completeness:.0%}"
        )
