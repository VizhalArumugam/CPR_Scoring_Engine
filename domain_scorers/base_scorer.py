"""
domain_scorers/base_scorer.py
------------------------------
Abstract base class that ALL six domain scorers must implement.

This interface enforces a consistent contract so that:
  1. Each scorer can be developed and tested independently by different team members.
  2. The scoring_engine.py orchestrator can call all six with the same API.
  3. Adding a new domain scorer in the future only requires implementing this interface.

Every scorer receives the same inputs but uses only what it needs:
  - timeline:        The full session event timeline
  - findings:        All FSM findings for the session (each scorer filters by its domain)
  - transcripts:     Dict with "lapel" and "ceiling" transcript lists
  - config:          The full acls.yaml scoring section

Every scorer returns a DomainScore object (defined in score_models.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from scoring.schemas.event_schema import FindingRecord, TranscriptSegment, UnifiedTimeline
from scoring.score_models import DomainScore


class BaseDomainScorer(ABC):
    """
    Abstract base for all domain scorers.

    Subclasses must implement:
        - domain_key    (class attribute)  — e.g. "cpr_quality"
        - domain_label  (class attribute)  — e.g. "CPR Quality"
        - max_points    (class attribute)  — e.g. 30.0
        - weight        (class attribute)  — e.g. 0.30
        - compute(...)  (method)           — returns a DomainScore
    """

    # ── Class-level constants (override in each subclass) ──────────────────
    domain_key:   str   = ""       # Used to route FSM findings
    domain_label: str   = ""       # Used in PDF report headers
    max_points:   float = 0.0      # Maximum score contribution
    weight:       float = 0.0      # Fraction of overall score (must sum to 1.0 across all domains)

    def __init__(self, scoring_config: dict = None):
        """
        Args:
            scoring_config: The full 'scoring' section from acls.yaml.
                            Each scorer reads its own sub-signal weights and thresholds.
        """
        self.config = scoring_config or {}
        self.thresholds = self.config.get("thresholds", {})
        self.sub_signal_weights = (
            self.config
            .get("sub_signal_weights", {})
            .get(self.domain_key, {})
        )

    @abstractmethod
    def compute(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        transcripts: Dict[str, List[TranscriptSegment]],
    ) -> DomainScore:
        """
        Compute the domain score from available data.

        Args:
            timeline:    Full UnifiedTimeline for the session.
                         Contains all clinical events with timestamps.
            findings:    All FindingRecord objects from the ACLS FSM.
                         Each scorer filters this list by self.domain_key.
            transcripts: Dict with two keys:
                           "lapel"   → List[TranscriptSegment] from team leader mic
                           "ceiling" → List[TranscriptSegment] from room mic

        Returns:
            A fully populated DomainScore object.

        Implementation contract:
            1. Filter findings:  domain_findings = [f for f in findings if f.domain == self.domain_key]
            2. Compute each sub-signal score using timeline events and findings.
            3. Compute completeness (0.0–1.0) based on available data quality.
            4. Compute CI using CICalculator.
            5. Assign completeness flag using get_completeness_flag().
            6. Return a populated DomainScore.
        """
        ...

    # ── Shared helper methods available to all scorers ─────────────────────

    def _filter_findings(self, findings: List[FindingRecord]) -> List[FindingRecord]:
        """Return only findings belonging to this domain."""
        return [f for f in findings if f.domain == self.domain_key]

    def _get_sub_signal_max(self, signal_name: str, fallback: float = 0.0) -> float:
        """Look up maximum points for a sub-signal from config."""
        return float(self.sub_signal_weights.get(signal_name, fallback))

    def _get_threshold(self, key: str, fallback: float = 0.0) -> float:
        """Look up a clinical threshold value from config."""
        return float(self.thresholds.get(key, fallback))

    def _clamp(self, value: float, lo: float = 0.0, hi: float = None) -> float:
        """Clamp value between lo and hi (hi defaults to self.max_points)."""
        if hi is None:
            hi = self.max_points
        return max(lo, min(hi, value))

    def _safe_mean(self, values: List[float], default: float = 0.0) -> float:
        """Mean of a list; returns default if list is empty."""
        return sum(values) / len(values) if values else default
