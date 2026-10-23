"""Structured-output schema for Claude's pick selection.

Mirrors the output format the (unchanged) selection prompt already asks for,
so the model's task is the same; the API now enforces the shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


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


class PickSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    picks: list[Pick]


def output_format() -> dict:
    """The output_config.format block for messages.create (SDK transform)."""
    import anthropic

    return {"type": "json_schema", "schema": anthropic.transform_schema(PickSet)}
