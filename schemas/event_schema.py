"""
schemas/event_schema.py
-----------------------
Core data models shared across the entire CPR Debriefing System.
All pipeline modules (event_extractor, acls_fsm, scoring, pdf_generator)
import from this file.

NOTE: Fields marked # [SCORING] are required by the Scoring Engine.
Fields marked # [PLANNED] are stubs for future modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """All clinical event types the system can recognise from audio/transcript."""
    # Arrest lifecycle
    ARREST_RECOGNISED       = "ARREST_RECOGNISED"
    ROSC_ACHIEVED           = "ROSC_ACHIEVED"
    RESUSCITATION_TERMINATED = "RESUSCITATION_TERMINATED"

    # CPR
    CPR_STARTED             = "CPR_STARTED"
    CPR_PAUSED              = "CPR_PAUSED"
    CPR_RESUMED             = "CPR_RESUMED"

    # Rhythm
    RHYTHM_IDENTIFIED       = "RHYTHM_IDENTIFIED"
    RHYTHM_CALLOUT          = "RHYTHM_CALLOUT"

    # Defibrillation
    SHOCK_DELIVERED         = "SHOCK_DELIVERED"
    SHOCK_ADVISED           = "SHOCK_ADVISED"
    SHOCK_OMITTED           = "SHOCK_OMITTED"

    # Drugs
    DRUG_ADMINISTERED       = "DRUG_ADMINISTERED"
    DRUG_ORDERED            = "DRUG_ORDERED"

    # Airway
    AIRWAY_MANAGED          = "AIRWAY_MANAGED"

    # Communication
    ORDER_ISSUED            = "ORDER_ISSUED"
    ORDER_CONFIRMED         = "ORDER_CONFIRMED"
    CALLOUT_MADE            = "CALLOUT_MADE"

    # Hs & Ts
    HS_TS_DISCUSSED         = "HS_TS_DISCUSSED"

    # Generic
    UNKNOWN                 = "UNKNOWN"


class ActorRole(str, Enum):
    """Clinical roles of team members in a simulation session."""
    TEAM_LEADER     = "team_leader"
    COMPRESSOR      = "compressor"
    DEFIBRILLATOR   = "defibrillator"
    IV_MEMBER       = "iv_member"
    AIRWAY          = "airway"
    RECORDER        = "recorder"
    UNKNOWN         = "unknown"


class Severity(str, Enum):
    """Severity levels for FSM deviations / findings."""
    CRITICAL    = "CRITICAL"
    HIGH        = "HIGH"
    MODERATE    = "MODERATE"
    LOW         = "LOW"
    INFO        = "INFO"


class SourceSystem(str, Enum):
    """Which system produced this event or finding."""
    NLP         = "nlp"
    FSM         = "fsm"
    SIMMAN      = "simman"     # Reserved for future hardware integration
    MANUAL      = "manual"
    INFERRED    = "inferred"


class DataCompletenessFlag(str, Enum):
    """Data quality flag attached to every domain score in the ScoreReport."""
    COMPLETE        = "COMPLETE"        # completeness >= 0.85
    PARTIAL_DATA    = "PARTIAL_DATA"    # 0.50 <= completeness < 0.85
    LOW_DATA        = "LOW_DATA"        # completeness < 0.50


class RhythmType(str, Enum):
    """Cardiac rhythm classifications."""
    VF          = "VF"              # Ventricular Fibrillation (shockable)
    PVTACHYCARDIA = "pVT"           # Pulseless VT (shockable)
    PEA         = "PEA"             # Pulseless Electrical Activity (non-shockable)
    ASYSTOLE    = "ASYSTOLE"        # Non-shockable
    ROSC_RHYTHM = "ROSC_RHYTHM"     # Organised rhythm with pulse
    UNKNOWN     = "UNKNOWN"


# ---------------------------------------------------------------------------
# Core Event Model
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """
    A traceable reference back to the raw source that produced an event or finding.
    Allows auditors to verify every claim in the PDF report.
    """
    source_system: SourceSystem
    raw_text: Optional[str] = None          # The actual quote from transcript
    timestamp_ms: Optional[int] = None      # When this evidence occurred
    speaker_role: Optional[ActorRole] = None
    confidence: float = 1.0                 # 0.0 – 1.0


@dataclass
class UnifiedEvent:
    """
    A single timestamped clinical event extracted from the session transcript.
    This is the primary unit of data flowing through the pipeline.

    Produced by:  event_extractor.py
    Consumed by:  acls_fsm.py, scoring_engine.py, pdf_generator.py
    """
    event_id: str
    event_type: EventType
    timestamp_ms: int                           # Absolute ms from session start
    actor_role: ActorRole = ActorRole.UNKNOWN
    source_system: SourceSystem = SourceSystem.NLP
    description: str = ""                       # Human-readable description

    # Drug-specific fields
    drug_name: Optional[str] = None             # e.g. "epinephrine"
    drug_dose_mg: Optional[float] = None        # e.g. 1.0
    drug_route: Optional[str] = None            # e.g. "IV", "IO"

    # Rhythm-specific fields
    rhythm_type: Optional[RhythmType] = None
    shock_energy_joules: Optional[int] = None

    # [SCORING] Data quality fields — Required by Scoring Engine
    nlp_confidence: float = 1.0                 # 0.0–1.0: NLP extraction confidence
    verbalized_by_team: bool = False            # True if event was spoken aloud by a team member
    has_confirmed_timestamp: bool = True        # False if timestamp was inferred

    # Evidence chain
    evidence: List[Evidence] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session Timeline Container
# ---------------------------------------------------------------------------

@dataclass
class UnifiedTimeline:
    """
    The full container for an entire simulation session.
    Passed as the primary input to the ACLS FSM and Scoring Engine.

    Produced by:  event_extractor.py (Stage 3 + 5)
    Consumed by:  acls_fsm.py (Stage 6), scoring_engine.py (Stage 7)
    """
    session_id: str
    scenario_name: str
    total_duration_ms: int                      # Total session length

    events: List[UnifiedEvent] = field(default_factory=list)

    # Session-level metadata
    team_size: Optional[int] = None
    guideline_version: str = "AHA_2025_ACLS"
    outcome: Optional[str] = None               # "ROSC" | "TERMINATED" | "ONGOING"

    # Convenience helpers (populated by event_extractor)
    arrest_start_ms: Optional[int] = None
    rosc_ms: Optional[int] = None
    shock_count: int = 0

    def get_events_by_type(self, event_type: EventType) -> List[UnifiedEvent]:
        """Filter events by type."""
        return [e for e in self.events if e.event_type == event_type]

    def get_events_by_role(self, role: ActorRole) -> List[UnifiedEvent]:
        """Filter events by actor role."""
        return [e for e in self.events if e.actor_role == role]

    def get_events_in_window(self, start_ms: int, end_ms: int) -> List[UnifiedEvent]:
        """Filter events within a time window."""
        return [e for e in self.events if start_ms <= e.timestamp_ms <= end_ms]


# ---------------------------------------------------------------------------
# FSM Finding Model
# ---------------------------------------------------------------------------

@dataclass
class FindingRecord:
    """
    A structured clinical deviation detected by the ACLS FSM rule engine.
    One FindingRecord = one specific protocol violation or observation.

    Produced by:  acls_fsm.py (Stage 6)
    Consumed by:  scoring_engine.py (Stage 7), pdf_generator.py (Stage 9),
                  LLM narrative engine (Stage 8)

    [SCORING] The `domain` field is how the Scoring Engine routes each finding
    to the correct domain scorer (e.g. "drug_administration" → DrugAdminScorer).
    """
    finding_id: str                     # e.g. "fnd_epi_delay_001"
    title: str                          # e.g. "Delayed First Epinephrine"
    severity: Severity
    domain: str                         # Routes to correct domain scorer
                                        # Values: "cpr_quality" | "shock_delivery" |
                                        #         "drug_administration" | "rhythm_recognition" |
                                        #         "team_leadership" | "team_communication"

    # Clinical detail
    description: str                    # e.g. "Epi given at 5:02, target ≤ 3 min"
    guideline_citation: str             # e.g. "AHA 2025 Guideline Section 3.1"
    recommendation: str
    reflective_prompt: str

    # Traceability
    evidence_ids: List[str] = field(default_factory=list)   # event_ids from UnifiedTimeline
    responsible_role: Optional[ActorRole] = None            # Who is accountable

    # [SCORING] Timing context (used for linear-decay scoring in some domains)
    event_timestamp_ms: Optional[int] = None    # When the deviation occurred
    target_timestamp_ms: Optional[int] = None   # When it should have occurred
    delay_seconds: Optional[float] = None       # Pre-calculated delay (convenience)


# ---------------------------------------------------------------------------
# Transcript Segment Model (Diarization Output)
# ---------------------------------------------------------------------------

@dataclass
class TranscriptSegment:
    """
    A single utterance from a speaker, with role attribution.
    Produced by diarization.py, consumed by NLP engine and Scoring Engine.

    Used by:  Team Leadership scorer (lapel segments)
              Team Communication scorer (ceiling segments)
    """
    segment_id: str
    text: str                           # Raw spoken text
    speaker_role: ActorRole
    start_ms: int
    end_ms: int
    source: str                         # "lapel" | "ceiling"
    confidence: float = 1.0             # Diarization confidence


# ---------------------------------------------------------------------------
# STUB: SimMan Sample (Reserved — Not Used in Current System)
# ---------------------------------------------------------------------------

@dataclass
class SimManSample:
    """
    Reserved for future hardware integration with SimMan manikin.
    Currently NOT used — CPR quality is assessed via FSM findings.

    If SimMan is integrated in the future, activate simman_parser.py
    and wire SimManSample[] into CprQualityScorer.
    """
    timestamp_ms: int
    rate_bpm: float         # Compression rate
    depth_inches: float     # Compression depth
    is_compressing: bool    # Whether compressions are active
