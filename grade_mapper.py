"""
grade_mapper.py
---------------
Assigns a letter grade (A–F) to the overall score and enforces
hard-fail rules that can cap the grade regardless of the overall score.

Hard-fail rules (from AHA 2025 guidelines):
  - If CPR Quality < 50% of its max → grade cannot exceed C
  - If Shock Delivery < 50% of its max → grade cannot exceed C

These two domains represent the most time-critical survival actions.
A team that fails them cannot be graded Excellent or Good.

Grade scale:
  90–100 → A  Excellent
  80–89  → B  Good
  70–79  → C  Acceptable
  60–69  → D  Needs Improvement
  0–59   → F  Unsatisfactory
"""

from typing import List, Optional, Tuple


# Default grade thresholds — overridden by acls.yaml scoring.grade_thresholds
_DEFAULT_THRESHOLDS = {
    "A": 90,
    "B": 80,
    "C": 70,
    "D": 60,
}

_GRADE_DESCRIPTORS = {
    "A": "Excellent — Protocol adherence meets AHA 2025 standard",
    "B": "Good — Minor deviations, no critical failures",
    "C": "Acceptable — Moderate deviations requiring focused review",
    "D": "Needs Improvement — Multiple significant deviations",
    "F": "Unsatisfactory — Critical protocol failures detected",
}

# Hard-fail rules: if a domain fails this check, the grade is capped at max_grade
_DEFAULT_HARD_FAIL_RULES = [
    {
        "domain": "cpr_quality",
        "threshold_fraction": 0.50,    # < 50% of max_points triggers cap
        "max_grade": "C",
        "reason": "Critically inadequate chest compressions detected",
    },
    {
        "domain": "shock_delivery",
        "threshold_fraction": 0.50,
        "max_grade": "C",
        "reason": "Critical failures in defibrillation delivery",
    },
]

# Grade ordering for hard-fail comparison (lower index = worse grade)
_GRADE_ORDER = ["F", "D", "C", "B", "A"]


def _raw_grade(score: float, thresholds: dict) -> str:
    """Assign raw grade from score, ignoring hard-fail rules."""
    if score >= thresholds.get("A", 90):
        return "A"
    elif score >= thresholds.get("B", 80):
        return "B"
    elif score >= thresholds.get("C", 70):
        return "C"
    elif score >= thresholds.get("D", 60):
        return "D"
    else:
        return "F"


def _cap_grade(current: str, max_allowed: str) -> str:
    """
    If current grade is better than max_allowed, cap it.
    E.g. current = "B", max_allowed = "C" → returns "C"
    """
    current_idx   = _GRADE_ORDER.index(current)
    max_idx       = _GRADE_ORDER.index(max_allowed)
    # Lower index = worse grade; cap means we cannot go higher than max_allowed
    return _GRADE_ORDER[min(current_idx, max_idx)]


class GradeMapper:
    """
    Assigns a letter grade to an overall score and enforces hard-fail domain caps.

    Usage:
        mapper = GradeMapper(config["scoring"])
        grade, descriptor, hard_fail, reason = mapper.assign_grade(74.3, domain_scores)
    """

    def __init__(self, scoring_config: dict = None):
        cfg = scoring_config or {}
        self.thresholds   = cfg.get("grade_thresholds", _DEFAULT_THRESHOLDS)
        self.hard_fail_rules = cfg.get("hard_fail_rules", _DEFAULT_HARD_FAIL_RULES)

    def assign_grade(
        self,
        overall_score: float,
        domain_scores: List,
    ) -> Tuple[str, str, bool, Optional[str]]:
        """
        Assign a final letter grade with optional hard-fail cap.

        Args:
            overall_score:  The sum of all domain final_scores (0–100).
            domain_scores:  List of DomainScore objects with .domain_key,
                            .final_score, .max_points attributes.

        Returns:
            Tuple of (grade, descriptor, hard_fail_override, hard_fail_reason)
              - grade:              "A" | "B" | "C" | "D" | "F"
              - descriptor:         Human-readable grade explanation
              - hard_fail_override: True if grade was capped by a hard-fail rule
              - hard_fail_reason:   The reason for the cap (or None)
        """
        grade = _raw_grade(overall_score, self.thresholds)
        hard_fail_override = False
        hard_fail_reason: Optional[str] = None

        # Build a quick lookup: domain_key → DomainScore
        domain_lookup = {ds.domain_key: ds for ds in domain_scores}

        for rule in self.hard_fail_rules:
            domain_key = rule["domain"]
            ds = domain_lookup.get(domain_key)
            if ds is None:
                continue

            threshold = rule["threshold_fraction"] * ds.max_points
            if ds.final_score < threshold:
                capped_grade = rule["max_grade"]
                original = grade
                grade = _cap_grade(grade, capped_grade)
                if grade != original:
                    hard_fail_override = True
                    hard_fail_reason = rule["reason"]
                    # Take the most restrictive cap (do not break — check all rules)

        descriptor = _GRADE_DESCRIPTORS.get(grade, "")
        return grade, descriptor, hard_fail_override, hard_fail_reason
