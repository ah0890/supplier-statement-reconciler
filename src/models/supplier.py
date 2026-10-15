"""Supplier model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Supplier(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int | None = None
    name: str
    normalized_name: str | None = None
    account_number: str | None = None
    contact_reference: str | None = None
    currency: str | None = None
