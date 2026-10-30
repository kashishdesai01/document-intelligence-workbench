from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workbench.extraction import ROW_KINDS, SCALARS
from workbench.grounding import normalize
from workbench.models import FieldKind, Result


class Record(BaseModel):
    """Shared schema for labels and model baselines."""

    model_config = ConfigDict(extra="forbid")
    date: str | None
    description: str | None
    amount: str | None
    balance: str | None


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str | None
    period_start: str | None
    period_end: str | None
    opening_balance: str | None
    closing_balance: str | None
    transactions: list[Record] = Field(max_length=5000)


def flatten(statement: Statement) -> dict[str, str]:
    def clean_value(kind: FieldKind, value: str | None) -> str | None:
        return normalize(kind, value) if value is not None else None

    values = {key: clean_value(kind, getattr(statement, key)) for key, (kind, _) in SCALARS.items()}
    for index, row in enumerate(statement.transactions):
        for key, kind in ROW_KINDS.items():
            values[f"{index}.{key}"] = clean_value(kind, getattr(row, key))
    return {key: value for key, value in values.items() if value is not None}


def flatten_result(result: Result) -> dict[str, tuple[str | None, float | None, bool]]:
    indices = {row.key: index for index, row in enumerate(result.transactions)}
    pairs = {}
    for item in result.fields:
        row, sep, column = item.key.partition(".")
        key = f"{indices[row]}.{column}" if sep else item.key
        pairs[key] = item.value, item.score, item.status == "auto_accepted"
    return pairs


@dataclass
class Measurements:
    documents: int = 0
    exact: int = 0
    straight_through: int = 0
    unsafe_straight_through: int = 0
    accepted: int = 0
    wrong_accepted: int = 0
    true_cells: int = 0
    expected_cells: int = 0
    predicted_cells: int = 0
    failed: int = 0
    source_unmatched: int = 0
    evidence_checked: int = 0
    prompt_tokens: int = 0
    generated_tokens: int = 0
    durations: list[float] = field(default_factory=list)
    evidence_outcomes: list[tuple[float, bool]] = field(default_factory=list)

    def record(
        self,
        expected: dict[str, str],
        prediction: dict[str, str],
        elapsed: float,
        *,
        result: Result | None = None,
        failed: bool = False,
    ) -> None:
        self.documents += 1
        self.failed += int(failed)
        exact = prediction == expected and not failed
        self.exact += int(exact)
        self.durations.append(elapsed)
        expected_cells = {key for key in expected if "." in key}
        predicted_cells = {key for key in prediction if "." in key}
        self.expected_cells += len(expected_cells)
        self.predicted_cells += len(predicted_cells)
        self.true_cells += sum(
            prediction[key] == expected[key] for key in expected_cells & predicted_cells
        )
        if result is None:
            return
        for item in result.fields:
            if item.value is not None and item.kind != "text":
                self.evidence_checked += 1
                self.source_unmatched += int(not item.evidence)
        straight = not result.issues and all(
            item.status == "auto_accepted" for item in result.fields
        )
        self.straight_through += int(straight)
        self.unsafe_straight_through += int(straight and not exact)
        for key, (value, score, accepted) in flatten_result(result).items():
            if value is None:
                continue
            correct = value == expected.get(key)
            if score is not None:
                self.evidence_outcomes.append((score, correct))
            if accepted:
                self.accepted += 1
                self.wrong_accepted += int(not correct)

    def report(self, variant: Literal["full", "text_only", "vision_llm"]) -> dict:
        return {
            "documents": self.documents,
            "failed": self.failed,
            "exact_documents": self.exact,
            "document_accuracy": ratio(self.exact, self.documents),
            "cell_f1": ratio(2 * self.true_cells, self.expected_cells + self.predicted_cells),
            "straight_through": ratio(self.straight_through, self.documents)
            if variant == "full"
            else None,
            "unsafe_straight_through_documents": self.unsafe_straight_through
            if variant == "full"
            else None,
            "auto_accepted_fields": self.accepted if variant == "full" else None,
            "wrong_auto_accepted_fields": self.wrong_accepted if variant == "full" else None,
            "auto_accepted_error_rate": ratio(self.wrong_accepted, self.accepted)
            if variant == "full"
            else None,
            "source_unmatched_rate": ratio(self.source_unmatched, self.evidence_checked),
            "source_values_checked": self.evidence_checked,
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "provider_cost_usd": None,
            "p95_seconds": round(sorted(self.durations)[ceil(len(self.durations) * 0.95) - 1], 3),
        }


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def fit_isotonic(samples: list[tuple[float, bool]]) -> list[dict]:
    """Pool adjacent violators, grouping tied scores before fitting."""
    groups: dict[float, list[bool]] = defaultdict(list)
    for score, correct in samples:
        groups[score].append(correct)
    blocks: list[dict] = []
    for score, outcomes in sorted(groups.items()):
        blocks.append(dict(upper=score, correct=sum(outcomes), count=len(outcomes)))
        while len(blocks) > 1:
            left, right = blocks[-2:]
            if left["correct"] / left["count"] <= right["correct"] / right["count"]:
                break
            blocks[-2:] = [
                dict(
                    upper=right["upper"],
                    correct=left["correct"] + right["correct"],
                    count=left["count"] + right["count"],
                )
            ]
    return blocks


def calibrate(score: float, blocks: list[dict]) -> float:
    block = next((block for block in blocks if score <= block["upper"]), blocks[-1])
    return block["correct"] / block["count"]


def reliability(samples: list[tuple[float, bool]], bins: int = 10) -> dict:
    groups: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for score, correct in samples:
        groups[min(bins - 1, int(score * bins))].append((score, correct))
    rows = []
    for index, group in sorted(groups.items()):
        count = len(group)
        confidence = sum(score for score, _ in group) / count
        accuracy = sum(correct for _, correct in group) / count
        rows.append(dict(bin=index, count=count, mean_score=confidence, accuracy=accuracy))
    ece = sum(abs(row["accuracy"] - row["mean_score"]) * row["count"] for row in rows)
    return {"ece": ece / len(samples) if samples else None, "bins": rows}
