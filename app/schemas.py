from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoadBase(BaseModel):
    load_id: str = Field(..., max_length=64)
    origin: str
    destination: str
    pickup_datetime: str
    delivery_datetime: str
    equipment_type: str
    loadboard_rate: float
    notes: str
    weight: int = Field(..., ge=0)
    commodity_type: str
    num_of_pieces: int = Field(..., ge=0)
    miles: int = Field(..., ge=0)
    dimensions: str


class LoadCreate(LoadBase):
    pass


class LoadRead(LoadBase):
    model_config = ConfigDict(from_attributes=True)


class CarrierVerifyResponse(BaseModel):
    """Unified shape: either valid + details, or valid + reason.

    **HappyRobot:** Branch on ``valid`` (boolean). When false, read ``reason`` for a short spoken line.
    """

    valid: bool
    details: dict[str, Any] | None = None
    reason: str | None = None


class ProcessCallRequest(BaseModel):
    """JSON body for ``POST /v1/process-call`` (HappyRobot HTTP tool each turn).

    **HappyRobot:** Prefer flat primitives only (strings, numbers, booleans, list of numbers). The
    backend persists the row and returns ``outcome`` / ``next_action`` for the workflow to branch.

    **carrier_interested** — ``False`` = carrier explicitly declined (forces ``no-interest`` + ``end_call``).
    ``True`` = explicit interest; proceed with negotiation when MC and load are valid.
    ``None`` = infer from transcript phrases (e.g. “not interested”) or continue with offer logic.

    **interested_reason** — Optional short note from the agent (e.g. “liked lane, not rate”) stored on the call.

    **current_round** — Optional 1-based round index; when sent, **must equal** ``len(counter_offers)``.
    Use it to stay aligned with HappyRobot’s turn counter; omit when only using transcript/final price.
    """

    transcript: str
    mc_number: str
    interested_load_id: str | None = None
    counter_offers: list[float] = Field(default_factory=list)
    final_agreed_price: float | None = None
    carrier_interested: bool | None = Field(
        default=None,
        description="False = declined load (no-interest). None = infer. True = explicit interest.",
    )
    interested_reason: str | None = Field(
        default=None,
        max_length=512,
        description="Short agent note about interest/disinterest context (stored on Call).",
    )
    current_round: int | None = Field(
        default=None,
        ge=0,
        description="When set, must equal len(counter_offers); max 3 negotiation rounds.",
    )

    @model_validator(mode="after")
    def current_round_matches_counters(self) -> "ProcessCallRequest":
        if self.current_round is not None and self.current_round != len(self.counter_offers):
            raise ValueError("current_round must equal len(counter_offers).")
        return self


NextAction = Literal["transfer_to_sales", "continue_negotiation", "end_call"]


class ProcessCallResponse(BaseModel):
    """JSON response from ``POST /v1/process-call`` for branching TTS and graph nodes.

    **HappyRobot:** Map ``next_action`` and ``outcome`` in your workflow; optional fields are omitted
    when not applicable (OpenAPI lists them as optional).

    **next_action** — ``transfer_to_sales`` when booked; ``continue_negotiation`` when another
    counter may be collected; ``end_call`` otherwise.

    **suggested_counter** / **suggested_counter_reason** — Returned on ``negotiated``; speak the
    number and optionally paraphrase the reason.

    **transfer_initiated** / **transfer_status_message** — On ``booked``: mock transfer flags;
    read **transfer_status_message** for spec-accurate wrap-up copy, **transfer_message** for softer TTS.

    **followup_needed** — True for "soft no" or positive carrier after a non-book outcome (CRM follow-up).

    **sentiment_warning** — True when booked but transcript sentiment reads negative (data quality / tension flag).
    """

    outcome: str
    agreed_price: float | None = None
    next_action: NextAction
    sentiment: str
    load_id: str | None = Field(default=None, description="Resolved load id after this turn.")
    loadboard_rate: float | None = Field(default=None, description="Posted rate on the matched load, if any.")
    carrier_mc: str | None = Field(default=None, description="MC on the call (normalized when possible).")
    counter_offers: list[float] | None = Field(
        default=None,
        description="Echo of structured counters submitted this request (audit / UI).",
    )
    rounds_used: int | None = Field(
        default=None,
        description="Negotiation rounds counted for this decision (structured counters or 0 for transcript-only).",
    )
    carrier_interested: bool | None = Field(
        default=None,
        description="Echo of explicit interest flag if the client sent it.",
    )
    interested_reason: str | None = Field(default=None, description="Echo of agent note if provided.")
    suggested_counter: float | None = None
    suggested_counter_reason: str | None = Field(
        default=None,
        description="Why suggested_counter was chosen (e.g. midpoint capped at ceiling).",
    )
    transfer_message: str | None = Field(
        default=None,
        description="Generic TTS-friendly line for the current outcome.",
    )
    transfer_initiated: bool | None = Field(
        default=None,
        description="True when outcome is booked and a (mock) transfer should begin.",
    )
    transfer_status_message: str | None = Field(
        default=None,
        description="Spec-style status line after a successful book + mock transfer.",
    )
    followup_needed: bool = Field(
        default=False,
        description="True when a positive carrier still did not book (worth a callback).",
    )
    sentiment_warning: bool = Field(
        default=False,
        description="True when booked but transcript sentiment reads negative.",
    )


class SearchLoadSummary(BaseModel):
    """One row for ``GET /v1/search-loads``.

    **HappyRobot:** Feed ``pitch_text`` to the agent for TTS; use ``load_id`` in the next ``process-call``.
    """

    load_id: str
    origin: str
    destination: str
    equipment_type: str
    pickup_datetime: str
    delivery_datetime: str
    miles: int
    loadboard_rate: float
    notes: str
    pitch_text: str


class CallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    mc: str
    load_id: str | None
    offers_json: str
    outcome: str
    sentiment: str
    transcript_snippet: str
