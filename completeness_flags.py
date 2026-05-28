"""
completeness_flags.py
---------------------
Assigns a data completeness flag (COMPLETE / PARTIAL_DATA / LOW_DATA)
to every domain score based on how much reliable data was available.

These flags are visually rendered in the PDF report as colour-coded badges:
  COMPLETE     → Green badge
  PARTIAL_DATA → Amber badge + CI shown in italic
  LOW_DATA     → Red badge + CI shown wide + explanatory note

Thresholds (configurable via acls.yaml):
  completeness >= 0.85  → COMPLETE
  0.50 <= comp < 0.85   → PARTIAL_DATA
  completeness < 0.50   → LOW_DATA
"""

from scoring.schemas.event_schema import DataCompletenessFlag


# Default thresholds — can be overridden via YAML config
_COMPLETE_THRESHOLD: float = 0.85
_PARTIAL_THRESHOLD: float = 0.50


def get_completeness_flag(completeness: float) -> DataCompletenessFlag:
    """
    Return a DataCompletenessFlag based on the completeness score.

    Args:
        completeness: Float between 0.0 and 1.0.
                      1.0 = full data available.
                      0.0 = no reliable data.

    Returns:
        DataCompletenessFlag enum value.
    """
    if completeness >= _COMPLETE_THRESHOLD:
        return DataCompletenessFlag.COMPLETE
    elif completeness >= _PARTIAL_THRESHOLD:
        return DataCompletenessFlag.PARTIAL_DATA
    else:
        return DataCompletenessFlag.LOW_DATA


def get_completeness_note(
    flag: DataCompletenessFlag,
    domain_label: str,
    completeness: float,
    detail: str = "",
) -> str:
    """
    Return a human-readable note explaining the completeness flag.
    This note is shown in the PDF report alongside the flag badge.

    Args:
        flag:           The computed DataCompletenessFlag.
        domain_label:   E.g. "CPR Quality"
        completeness:   Float 0.0–1.0
        detail:         Optional extra context (e.g. "Lapel mic active for 60% of session")

    Returns:
        A short explanatory string for the PDF report.
    """
    pct = int(completeness * 100)

    if flag == DataCompletenessFlag.COMPLETE:
        return f"{domain_label}: high data confidence ({pct}% coverage). {detail}".strip()

    elif flag == DataCompletenessFlag.PARTIAL_DATA:
        base = (
            f"{domain_label}: partial data ({pct}% coverage). "
            f"Score confidence interval is widened. {detail}"
        )
        return base.strip()

    else:  # LOW_DATA
        base = (
            f"{domain_label}: low data ({pct}% coverage). "
            f"Score has wide uncertainty — interpret with caution. {detail}"
        )
        return base.strip()
