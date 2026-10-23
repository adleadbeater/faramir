"""Structured-output schema for Claude's pick selection.

Mirrors the output format the (unchanged) selection prompt already asks for,
so the model's task is the same; the API now enforces the shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


ClaimType = Literal[
    "passed_comp", "nearing_comp", "crossed_milestone", "nearing_milestone", "relation", "genre_descriptor",
    # Declared so the model can report them honestly; validation rejects every one in Phase 0.
    "rank", "record", "superlative", "chart_position", "first_or_only", "count",
]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ClaimType
    comp_tmdb_id: int | None
    threshold: int | None
    axis: Literal["domestic", "worldwide"] | None
    relation: Literal["franchise", "same_director", "same_star"] | None
    genre: str | None
    text: str


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_tmdb_id: int
    active_title: str
    comp_tmdb_id: int | None
    comp_title: str | None
    axis: Literal["domestic", "worldwide"]
    kind: Literal["just_passed", "about_to_pass", "milestone"]
    threshold_value: int | None
    angle_category: str
    headline: str
    angle: str
    claims: list[Claim]


class PickSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    picks: list[Pick]


def output_format() -> dict:
    """The output_config.format block for messages.create (SDK transform)."""
    import anthropic

    return {"type": "json_schema", "schema": anthropic.transform_schema(PickSet)}


class Headline(BaseModel):
    """Structured output for the one-shot headline shortening call."""

    model_config = ConfigDict(extra="forbid")

    headline: str


def headline_format() -> dict:
    import anthropic

    return {"type": "json_schema", "schema": anthropic.transform_schema(Headline)}
