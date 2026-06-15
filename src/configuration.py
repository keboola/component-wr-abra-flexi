"""Configuration model for the ABRA Flexi writer.

The platform merges root config (connection) and row config (evidence/write
options) before the component runs, so this single model receives both.
"""

import logging

from keboola.component.exceptions import UserException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LOGGER = logging.getLogger(__name__)


class ColumnMapping(BaseModel):
    """Maps one input CSV column to a FlexiBee destination field name."""

    model_config = ConfigDict(extra="ignore")
    source: str
    destination: str


class Configuration(BaseModel):
    # --- connection (root config) ---
    base_url: str
    company: str
    username: str
    password: str = Field(alias="#password")
    ssl_verify: bool = True
    debug: bool = False

    # --- write options (row config) ---
    evidence: str = ""
    id_column: str = ""
    id_type: str = "ext"
    batch_size: int = 100
    fail_on_error: bool = False
    column_mapping: list[ColumnMapping] = []

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            error_messages = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
            raise UserException(f"Validation Error: {', '.join(error_messages)}")

        if self.debug:
            LOGGER.debug("Component will run in Debug mode")

    @field_validator("id_type")
    @classmethod
    def _validate_id_type(cls, v: str) -> str:
        if v not in ("ext", "internal"):
            raise ValueError("id_type must be 'ext' or 'internal'")
        return v

    @field_validator("batch_size")
    @classmethod
    def _validate_batch_size(cls, v: int) -> int:
        if v < 1 or v > 500:
            raise ValueError("batch_size must be between 1 and 500")
        return v
