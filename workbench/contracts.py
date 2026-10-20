"""Public API contracts and the versioned, business-facing statement shape."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from workbench.models import Result

Money = Annotated[str, Field(pattern=r"^-?[0-9]{1,15}\.[0-9]{2}$")]
ISODate = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class BankTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: ISODate
    description: str = Field(min_length=1, max_length=300)
    amount: Money
    balance: Money


class BankStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[a-zA-Z0-9*•×-]{3,40}$")
    period_start: ISODate
    period_end: ISODate
    opening_balance: Money
    closing_balance: Money
    transactions: list[BankTransaction] = Field(min_length=1)


class DocumentRecord(BaseModel):
    id: UUID
    filename: str
    schema_version: str
    pipeline_version: str
    status: Literal["queued", "processing", "needs_review", "completed", "failed"]
    error_code: str | None
    generation: int
    revision: int
    page_count: int | None
    created_at: datetime
    updated_at: datetime


class DocumentResult(Result):
    document: DocumentRecord


class UploadResponse(BaseModel):
    document: DocumentRecord
    reused: bool


class Cursor(BaseModel):
    before_time: datetime
    before_id: UUID


class DocumentList(BaseModel):
    documents: list[DocumentRecord]
    next: Cursor | None


class PageMetadata(BaseModel):
    number: int
    width: int
    height: int
    source: Literal["native", "ocr"]
    rotation: int


def statement_data(result: Result) -> BankStatement:
    values = {field.key: field.value for field in result.fields}
    scalars = {key: value for key, value in values.items() if "." not in key}
    return BankStatement.model_validate(
        {
            **scalars,
            "transactions": [
                {column: values[f"{row.key}.{column}"] for column in BankTransaction.model_fields}
                for row in result.transactions
            ],
        }
    )
