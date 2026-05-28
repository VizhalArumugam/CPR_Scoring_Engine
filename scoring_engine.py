"""
scoring_engine.py
-----------------
The main orchestrator of the Scoring Engine (Stage 7 in the pipeline).

This module:
  1. Receives all inputs from upstream pipeline stages.
  2. Instantiates and calls all 6 domain scorers.
  3. Aggregates domain scores into an overall score.
  4. Computes the overall confidence interval.
  5. Assigns a letter grade (with hard-fail cap if needed).
  6. Returns a single ScoreReport object.

Usage:
    from scoring.scoring_engine import ScoringEngine

    engine = ScoringEngine(scoring_config=config["scoring"])
    report = engine.score(
        session_id="ses_scn_001",
        timeline=unified_timeline,
        findings=fsm_findings,
        lapel_transcript=lapel_segments,
        ceiling_transcript=ceiling_segments,
    )
    print(report.summary_str())

The ScoreReport is then passed to:
  - pdf_generator.py  (Stage 9) — renders the full PDF report
  - ollama_api.py     (Stage 8) — provides quantitative context for LLM narrative
"""

from __future__ import annotations

import time
from typing import List, Optional

from scoring.confidence import CICalculator
from scoring.domain_scorers.cpr_quality import CprQualityScorer
from scoring.domain_scorers.drug_admin import DrugAdminScorer
from scoring.domain_scorers.rhythm_recognition import RhythmRecognitionScorer
from scoring.domain_scorers.shock_delivery import ShockDeliveryScorer
from scoring.domain_scorers.team_communication import TeamCommunicationScorer
from scoring.domain_scorers.team_leadership import TeamLeadershipScorer
from scoring.grade_mapper import GradeMapper
from scoring.schemas.event_schema import (
    DataCompletenessFlag,
    FindingRecord,
    TranscriptSegment,
    UnifiedTimeline,
)
from scoring.score_models import DomainScore, ScoreReport


class ScoringEngine:
    """
    Orchestrates all 6 domain scorers and produces a ScoreReport.

    Parameters:
        scoring_config (dict): The 'scoring' section from acls.yaml.
                               Pass an empty dict to use all defaults.
    """

    def __init__(self, scoring_config: dict = None):
        self.config = scoring_config or {}

        # Instantiate all 6 domain scorers
        self.scorers = [
            CprQualityScorer(self.config),
            ShockDeliveryScorer(self.config),
            DrugAdminScorer(self.config),
            RhythmRecognitionScorer(self.config),
            TeamLeadershipScorer(self.config),
            TeamCommunicationScorer(self.config),
        ]

        self.grade_mapper  = GradeMapper(self.config)
        self.ci_calculator = CICalculator(self.config.get("confidence_interval"))

    def score(
        self,
        timeline: UnifiedTimeline,
        findings: List[FindingRecord],
        lapel_transcript: Optional[List[TranscriptSegment]] = None,
        ceiling_transcript: Optional[List[TranscriptSegment]] = None,
        session_id: str = "",
    ) -> ScoreReport:
        """
        Run all domain scorers and produce a complete ScoreReport.

        Args:
            timeline:           The full session UnifiedTimeline (from event_extractor.py).
            findings:           All FSM FindingRecord objects (from acls_fsm.py).
            lapel_transcript:   Team Leader mic segments (from diarization.py). Optional.
            ceiling_transcript: Full room mic segments (from diarization.py). Optional.
            session_id:         Session identifier for the report metadata.

        Returns:
            ScoreReport — the complete scoring output for this session.

        Integration note:
            When connecting with the rest of the team's pipeline, call this method
            from pipeline/worker.py after acls_fsm.py and nlp_engine.py have finished.
        """
        transcripts = {
            "lapel":   lapel_transcript   or [],
            "ceiling": ceiling_transcript or [],
        }

        # ── Step 1: Run all 6 domain scorers ─────────────────────────────
        domain_scores: List[DomainScore] = []
        for scorer in self.scorers:
            domain_score = scorer.compute(
                timeline=timeline,
                findings=findings,
                transcripts=transcripts,
            )
            domain_scores.append(domain_score)

        # ── Step 2: Overall score (sum of all domain final_scores) ────────
        overall_score = round(sum(ds.final_score for ds in domain_scores), 2)

        # ── Step 3: Overall confidence interval ───────────────────────────
        ci_lower, ci_upper = self.ci_calculator.compute_overall_ci(domain_scores)

        # ── Step 4: Grade + hard-fail check ──────────────────────────────
        grade, descriptor, hard_fail, reason = self.grade_mapper.assign_grade(
            overall_score, domain_scores
        )

        # ── Step 5: Data quality summary ─────────────────────────────────
        overall_completeness = round(
            sum(ds.completeness * ds.weight for ds in domain_scores), 3
        )
        has_low_data = any(
            ds.completeness_flag == DataCompletenessFlag.LOW_DATA
            for ds in domain_scores
        )

        # ── Step 6: Build and return ScoreReport ─────────────────────────
        report = ScoreReport(
            overall_score=overall_score,
            grade=grade,
            grade_descriptor=descriptor,
            hard_fail_override=hard_fail,
            hard_fail_reason=reason,
            ci_lower=round(ci_lower, 2),
            ci_upper=round(ci_upper, 2),
            domain_scores=domain_scores,
            overall_completeness=overall_completeness,
            has_low_data_domains=has_low_data,
            protocol_version="AHA_2025_ACLS",
            session_id=session_id or timeline.session_id,
            session_duration_ms=timeline.total_duration_ms,
            scored_at_ms=int(time.time() * 1000),
        )

        return report
