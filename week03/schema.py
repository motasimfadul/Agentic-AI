"""COSC726 Lab 2 (Colab track) — the triage contract as a Python type.

Student: Motasim Fadul (12345678)

Tasks 2 and 3 of `lab2_pydantic_colab.ipynb`, extracted so the same object is
importable by the notebook, by the gates, and (from Week 4) by the tool
dispatcher. One source of truth: the contract in the prompt is rendered from
this model, so the two cannot drift apart.

    TriageResult    gates 1 and 2 — shape, types, enums, null semantics
    ValidatedTriage gates 3 and 4 — referential lookup and policy coherence

Requires pydantic>=2.7. Pin it: this ecosystem breaks between releases.
"""

from __future__ import annotations

from enum import Enum

from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator, model_validator)

# ---------------------------------------------------------------------------
# Facts that live OUTSIDE the type — which is exactly why gates 3 and 4 exist.
# ---------------------------------------------------------------------------
KNOWN_ORDER_IDS = {"A1032", "A1044", "A1051", "A1067",
                   "A1078", "A1080", "A1091", "A1099"}
POLICY_THRESHOLD_DAYS = 3


class Intent(str, Enum):
    late_delivery = "late_delivery"
    refund = "refund"
    address_change = "address_change"
    cancel_and_refund = "cancel_and_refund"
    other = "other"


class Action(str, Enum):
    check_status = "check_status"
    request_approval = "request_approval"
    escalate_to_human = "escalate_to_human"
    reply_only = "reply_only"


# ---------------------------------------------------------------------------
# Task 2 — the output contract as a type (gates 1 and 2)
# ---------------------------------------------------------------------------
class TriageResult(BaseModel):
    """One triaged support email, as the workflow consumes it.

    Descriptions are written for the model, not for a maintainer: they are
    rendered straight into the system prompt by `compact_schema()`.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent = Field(
        description="What the customer is asking for. Choose the single "
                    "closest value; use 'other' when none applies.")
    order_id: str | None = Field(
        default=None, pattern=r"^A[0-9]{4}$",
        description="The order number as stated in the message, e.g. A1032. "
                    "Null if the message states none, or states one that is "
                    "not in the A#### format. Never repair or complete it.")
    days_late: int | None = Field(
        default=None, ge=0, le=365,
        description="Whole days between the promised date and today, counted "
                    "only from dates present in the message or EVIDENCE. Null "
                    "if no delay is stated. Never estimate.")
    proposed_action: Action = Field(
        description="What the workflow should do next. Proposed, never "
                    "performed: anything that changes an account is "
                    "request_approval.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs from the EVIDENCE block that you actually used. "
                    "Only IDs that literally appear there.")


# ---------------------------------------------------------------------------
# Task 3 — the gates a schema cannot express (gates 3 and 4)
# ---------------------------------------------------------------------------
class ValidatedTriage(TriageResult):
    """TriageResult plus the two gates that need the world.

    The error strings matter: the notebook's `run_gates()` classifies a
    failure as gate 3 or gate 4 by matching on them.
    """

    @field_validator("order_id")
    @classmethod
    def _order_must_exist(cls, v: str | None) -> str | None:
        """Gate 3 — refers. 'A9999' is shape-perfect and refers to nothing."""
        if v is not None and v not in KNOWN_ORDER_IDS:
            raise ValueError(f"order_id {v} is well-formed but unknown")
        return v

    @model_validator(mode="after")
    def _fields_cohere_with_policy(self) -> "ValidatedTriage":
        """Gate 4 — coheres. Cross-field rules and POL-LATE."""
        if (self.intent is Intent.late_delivery
                and self.proposed_action is Action.request_approval):
            if self.days_late is None:
                raise ValueError(
                    "approval proposed without a counted delay")
            if self.days_late < POLICY_THRESHOLD_DAYS:
                raise ValueError(
                    f"approval proposed at {self.days_late} days late; "
                    f"POL-LATE requires {POLICY_THRESHOLD_DAYS} or more")

        if self.intent is Intent.late_delivery and self.order_id is None:
            raise ValueError("late_delivery without an order_id")

        if self.days_late is not None and self.intent is not Intent.late_delivery:
            raise ValueError(
                f"days_late counted on intent {self.intent.value}")
        return self


# ---------------------------------------------------------------------------
# Rendering the contract for the prompt: same source of truth, cheaper form.
# ---------------------------------------------------------------------------
def compact_schema(m: type[BaseModel]) -> str:
    """A few lines a model can read, instead of a page of JSON Schema."""
    s = m.model_json_schema()
    defs = s.get("$defs", {})
    lines = []
    for name, spec in s["properties"].items():
        required = name in s.get("required", [])
        bits = []
        ref = spec.get("$ref") or next(
            (a.get("$ref") for a in spec.get("anyOf", []) if a.get("$ref")), None)
        if ref:
            bits.append(" | ".join(defs[ref.split("/")[-1]]["enum"]))
        else:
            types = [a.get("type") for a in spec.get("anyOf", [])] or [spec.get("type")]
            bits.append(" | ".join(t for t in types if t))
        for key in ("pattern", "minimum", "maximum"):
            for src in (spec, *spec.get("anyOf", [])):
                if key in src:
                    bits.append(f"{key}={src[key]}")
        lines.append(f"  {name}{'' if required else ' (optional)'}: "
                     f"{', '.join(b for b in bits if b)}")
    return "\n".join(lines)


if __name__ == "__main__":  # smoke test — run once pydantic is installed
    ok = {"intent": "other", "proposed_action": "reply_only"}
    print("valid  :", TriageResult.model_validate(ok))

    for bad, why in [
        ({"intent": "general", "proposed_action": "reply_only"}, "enum"),
        ({"intent": "other", "proposed_action": "reply_only",
          "order_id": "1102"}, "pattern"),
        ({"intent": "address_change", "order_id": "A9999",
          "proposed_action": "escalate_to_human"}, "gate 3"),
        ({"intent": "late_delivery", "order_id": "A1080", "days_late": 1,
          "proposed_action": "request_approval"}, "gate 4"),
    ]:
        try:
            ValidatedTriage.model_validate(bad)
            print(f"NOT REJECTED ({why}) — the gate is not working")
        except ValidationError as exc:
            print(f"rejected ({why}):", exc.errors()[0]["msg"][:70])
