from typing import Literal
from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    hyp_id: str                      # "h-01", ...
    cause: str
    evidence_ids: list[str]
    status: str                      # must be "unverified" (forced in code; single-value Literal would emit `const`)


class Action(BaseModel):
    action_id: str                   # "a-01", ...
    title: str
    owner_role: Literal["water operator", "facilities coordinator", "resilience officer"]
    priority: int
    rationale: str
    prerequisites: list[str]
    evidence_ids: list[str]
    procedure_ids: list[str]
    requires_approval: bool          # forced True in code
    simulated_command: Literal["restore_power_feed", "conserve_demand", "repair_tank_sensor", "none"]


class Assessment(BaseModel):
    summary: str
    hypotheses: list[Hypothesis] = Field(max_length=4)
    data_quality: Literal["sufficient", "limited", "insufficient"]
    missing_evidence: list[str]
    actions: list[Action] = Field(max_length=5)
    limitations: list[str]
    evidence_ids: list[str]


class ClaimCheck(BaseModel):
    item_id: str                     # "summary", "h-01", "a-01"
    verdict: Literal["supported", "partially_supported", "unsupported"]
    problem: Literal["none", "evidence_does_not_support", "wrong_reference", "overreach",
                     "contradicts_calculation", "unsafe_or_out_of_scope"]
    reason: str


class JudgeResult(BaseModel):
    checks: list[ClaimCheck]
    overall: Literal["accept", "repair", "reject"]
    repair_instructions: str


class QAAnswer(BaseModel):
    answer: str
    evidence_ids: list[str]
    procedure_ids: list[str]
    used_what_if: bool


class PlannerAnswer(BaseModel):
    recommended_desal_hours: float
    recommended_conservation_pct: float
    summary: str
    rationale: list[str] = Field(max_length=4)
    watch_items: list[str] = Field(max_length=3)
    procedure_ids: list[str]
