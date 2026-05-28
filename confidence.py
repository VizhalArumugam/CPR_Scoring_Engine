"""
confidence.py
-------------
Computes per-domain and overall confidence intervals (CI) for the ScoreReport.

CI width is driven entirely by DATA COMPLETENESS, not by finding count.
This makes the uncertainty transparent:
  - Full audio coverage + high NLP confidence → narrow CI (±5 pts)
  - Missing lapel mic for 40% of session → wide CI (±10 pts)
  - No CPR event data → very wide CI (±20 pts)

Formula:
  domain_half_width = clamp(
      base_half_width / sqrt(completeness + epsilon),
      min_half_width,
      max_half_width
  )

Overall CI is propagated from domain CIs using weighted variance:
  overall_variance = sum((weight * half_width) ** 2 for each domain)
  overall_half_width = sqrt(overall_variance)
"""

import math
from typing import List


# Default CI parameters — overridden by acls.yaml scoring.confidence_interval section
_DEFAULT_BASE_HALF_WIDTH: float = 5.0
_DEFAULT_MIN_HALF_WIDTH: float = 3.0
_DEFAULT_MAX_HALF_WIDTH: float = 20.0
_DEFAULT_OVERALL_MAX: float = 15.0
_DEFAULT_EPSILON: float = 0.01   # Prevents division by zero when completeness = 0.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class CICalculator:
    """
    Calculates confidence intervals per domain and for the overall score.

    Usage:
        calc = CICalculator(config["scoring"]["confidence_interval"])
        ci_lower, ci_upper = calc.compute_domain_ci(score=22.4, max_points=30.0, completeness=0.40)
        overall_lower, overall_upper = calc.compute_overall_ci(domain_scores)
    """

    def __init__(self, ci_config: dict = None):
        cfg = ci_config or {}
        self.base_half_width   = cfg.get("base_half_width",   _DEFAULT_BASE_HALF_WIDTH)
        self.min_half_width    = cfg.get("min_half_width",    _DEFAULT_MIN_HALF_WIDTH)
        self.max_half_width    = cfg.get("max_half_width",    _DEFAULT_MAX_HALF_WIDTH)
        self.overall_max       = cfg.get("overall_max_half_width", _DEFAULT_OVERALL_MAX)
        self.epsilon           = cfg.get("epsilon",           _DEFAULT_EPSILON)

    def compute_domain_half_width(self, completeness: float) -> float:
        """
        Compute the half-width (±) of the CI for a single domain.

        Args:
            completeness: Float 0.0–1.0. Higher = more data = narrower CI.

        Returns:
            Half-width in score points.

        Examples:
            completeness = 1.00  →  ±5.0 pts  (full data)
            completeness = 0.50  →  ±7.1 pts
            completeness = 0.25  →  ±10.0 pts
            completeness = 0.00  →  ±20.0 pts (capped)
        """
        raw = self.base_half_width / math.sqrt(completeness + self.epsilon)
        return _clamp(raw, self.min_half_width, self.max_half_width)

    def compute_domain_ci(
        self,
        score: float,
        max_points: float,
        completeness: float,
    ) -> tuple[float, float]:
        """
        Compute the lower and upper CI bounds for a domain score.

        Args:
            score:       The domain's final_score (0.0 → max_points).
            max_points:  The maximum possible points for this domain.
            completeness: Float 0.0–1.0.

        Returns:
            (ci_lower, ci_upper) clamped to [0, max_points].
        """
        hw = self.compute_domain_half_width(completeness)
        ci_lower = _clamp(score - hw, 0.0, max_points)
        ci_upper = _clamp(score + hw, 0.0, max_points)
        return ci_lower, ci_upper

    def compute_overall_ci(self, domain_scores: List) -> tuple[float, float]:
        """
        Propagate per-domain CIs into an overall CI using weighted variance.

        Formula:
            overall_variance = Σ (weight_i × half_width_i)²
            overall_half_width = sqrt(overall_variance)

        Args:
            domain_scores: List of DomainScore objects (must have .weight and .completeness).

        Returns:
            (overall_ci_lower, overall_ci_upper) clamped to [0, 100].
        """
        variance = sum(
            (ds.weight * self.compute_domain_half_width(ds.completeness)) ** 2
            for ds in domain_scores
        )
        overall_hw = _clamp(math.sqrt(variance), self.min_half_width, self.overall_max)

        overall_score = sum(ds.final_score for ds in domain_scores)
        ci_lower = _clamp(overall_score - overall_hw, 0.0, 100.0)
        ci_upper = _clamp(overall_score + overall_hw, 0.0, 100.0)
        return ci_lower, ci_upper
