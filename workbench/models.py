from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Coordinate = Annotated[float, Field(ge=0, le=1)]


class Box(BaseModel):
    """Normalized display coordinates, measured from the top left."""

    model_config = ConfigDict(frozen=True)
    left: Coordinate
    top: Coordinate
    right: Coordinate
    bottom: Coordinate

    @model_validator(mode="after")
    def positive_area(self) -> "Box":
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("box must have positive area")
        return self


class Word(BaseModel):
    text: str
    box: Box
    score: float = Field(ge=0, le=1)


class Page(BaseModel):
    number: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source: Literal["native", "ocr"]
    rotation: int = 0
    words: list[Word]


FieldKind = Literal["money", "date", "id", "text"]


class Evidence(BaseModel):
    page: int = Field(ge=1)
    box: Box
    text: str
    score: float = Field(ge=0, le=1)


class ExtractedField(BaseModel):
    key: str
    kind: FieldKind
    value: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    score: float | None = None
    issues: list[str] = Field(default_factory=list)
    status: Literal["auto_accepted", "needs_review", "corrected", "approved", "rejected"] = (
        "needs_review"
    )


class Transaction(BaseModel):
    key: str
    page: int


class Checkpoint(BaseModel):
    page: int
    previous_balance_key: str
    value: str | None
    evidence: list[Evidence]


class Issue(BaseModel):
    code: str
    message: str
    field_keys: list[str] = Field(default_factory=list)
    conflict_key: str | None = None


class Result(BaseModel):
    fields: list[ExtractedField]
    transactions: list[Transaction]
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["correct", "approve", "reject", "discard"]
    value: str | None = Field(default=None, max_length=300)
    page: int | None = Field(default=None, ge=1)
    box: Box | None = None
    revision: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check_action(self) -> "Review":
        if "\x00" in self.note or (self.value is not None and "\x00" in self.value):
            raise ValueError("review text contains a null character")
        if self.action == "correct" and (not self.value or not self.value.strip()):
            raise ValueError("a correction requires a value")
        if self.action != "correct" and self.value is not None:
            raise ValueError("only corrections can set a value")
        if (self.page is None) != (self.box is None):
            raise ValueError("page and box must be supplied together")
        if self.box and self.action in {"reject", "discard"}:
            raise ValueError("this action cannot set evidence")
        if not self.note.strip():
            raise ValueError("a review note is required")
        return self
