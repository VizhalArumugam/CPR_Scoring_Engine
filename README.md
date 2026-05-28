# CPR Debriefing System - Scoring Engine

This repository contains **Stage 7 (The Scoring Engine)** of the AI-Assisted ACLS CPR Debriefing System.

The Scoring Engine takes structured clinical data (events and protocol deviations) from earlier pipeline stages and evaluates team performance across 6 clinical domains using AHA 2025 guidelines.

## 🏗 Architecture & Interfaces

The Scoring Engine is completely **hardware-free** and does not use SimMan manikin data. All clinical scores are calculated exclusively from two upstream data objects:
1. `UnifiedTimeline` (from Stage 3/5: Event Extractor)
2. `List[FindingRecord]` (from Stage 6: ACLS FSM Rule Engine)

### Core Integration Point

When wiring the final pipeline together, the main orchestrator should invoke the engine like this:

```python
from scoring.scoring_engine import ScoringEngine

# Initialize with optional configuration
engine = ScoringEngine(scoring_config={})

# Generate the full ScoreReport
report = engine.score(
    timeline=unified_timeline,           # Required
    findings=fsm_findings,               # Required
    lapel_transcript=lapel_segments,     # Optional (for leadership/comm metrics)
    ceiling_transcript=ceiling_segments, # Optional (for leadership/comm metrics)
    session_id="ses_12345"
)
```

The output `ScoreReport` contains the final scores, letter grades, confidence intervals, and data completeness flags. This object is passed directly to the LLM Narrative Engine (Stage 8) and PDF Generator (Stage 9).

---

## 🔌 API Contract for Teammates

For the scoring engine to function correctly, the **Event Extractor** and **ACLS FSM** teams must ensure their outputs match these schema requirements. All schemas are defined in `scoring/schemas/event_schema.py`.

### For the ACLS FSM Team (`acls_fsm.py`)
Each `FindingRecord` you emit must contain:
1. **`domain`**: This MUST exactly match one of the 6 domain keys so the engine routes it correctly:
   - `"cpr_quality"`
   - `"shock_delivery"`
   - `"drug_administration"`
   - `"rhythm_recognition"`
   - `"team_leadership"`
   - `"team_communication"`

2. **`title`**: This is used to map the finding to specific sub-signal deductions. Example titles the engine listens for:
   - CPR: `CPR_START_DELAYED`, `CPR_PAUSE_EXCESSIVE`, `CPR_FRACTION_LOW`, `CPR_RATE_DEVIATION`
   - Shock: `TIME_TO_FIRST_SHOCK_DELAYED`, `INTER_SHOCK_INTERVAL_VIOLATION`, `SHOCK_IN_NON_SHOCKABLE_RHYTHM`, `SHOCK_OMITTED`
   - Drug: `EPI_FIRST_DOSE_DELAYED`, `EPI_REPEAT_INTERVAL_VIOLATION`, `ANTIARRHYTHMIC_OMITTED`, `ANTIARRHYTHMIC_CONTRAINDICATED`

3. **`severity`**: Determines the penalty fraction.
   - `CRITICAL` (100% deduction of sub-signal points)
   - `HIGH` (80% deduction)
   - `MODERATE` (50% deduction)
   - `LOW` (20% deduction)

### For the Event Extractor Team (`event_extractor.py`)
Your `UnifiedTimeline` must output events containing these exact `EventType` strings for completeness checks and CCF calculations:
- `CPR_STARTED`, `CPR_PAUSED`, `CPR_RESUMED`
- `SHOCK_DELIVERED`
- `DRUG_ADMINISTERED`

---

## 📊 The 6 Scoring Domains

| Domain | Max Points | Description |
|---|---|---|
| **CPR Quality** | 30 | CCF fraction, pauses, delay to start |
| **Shock Delivery** | 20 | Defibrillation timing, appropriate rhythms |
| **Drug Administration** | 20 | Epinephrine/Amiodarone timing & correct dosing |
| **Rhythm Recognition** | 10 | Rapid identification of rhythm changes |
| **Team Leadership** | 10 | Directing the team, clear orders |
| **Team Communication** | 10 | Closed-loop communication, confirming orders |

## 🚀 Running Locally

All logic runs on standard Python 3.9+. No external dependencies are currently required for the base logic. 

**Note on Configuration**: Scoring weights, thresholds, and grade boundaries can be modified at runtime by passing a dictionary into `ScoringEngine(scoring_config=...)`.
