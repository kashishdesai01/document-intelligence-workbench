"""Statement template mapping over geometry, independent of the OCR engine."""

import re
from decimal import Decimal
from statistics import median

from workbench.grounding import evidence, ground, normalize
from workbench.models import (
    Checkpoint,
    Evidence,
    ExtractedField,
    FieldKind,
    Issue,
    Page,
    Result,
    Transaction,
    Word,
)

SCALARS: dict[str, tuple[FieldKind, tuple[str, ...]]] = {
    "account_id": ("id", ("account number", "account no", "account id", "account")),
    "period_start": ("date", ("period start", "statement start", "start date", "from")),
    "period_end": ("date", ("period end", "statement end", "end date", "to")),
    "opening_balance": ("money", ("opening balance", "beginning balance", "previous balance")),
    "closing_balance": ("money", ("closing balance", "ending balance", "new balance")),
}
COLUMN_LABELS = {
    "date": {"date", "posted"},
    "description": {"description", "details", "activity", "memo"},
    "debit": {"debit", "debits", "withdrawal", "withdrawals"},
    "credit": {"credit", "credits", "deposit", "deposits"},
    "amount": {"amount"},
    "balance": {"balance"},
}
ROW_KINDS: dict[str, FieldKind] = {
    "date": "date",
    "description": "text",
    "amount": "money",
    "balance": "money",
}
CARRY = re.compile(r"balance (?:brought|carried) forward", re.I)
FOOTER = re.compile(
    r"(?:page \d+(?: of \d+)?|continued on next page|bank statement|transaction activity)", re.I
)
DATE_TOKEN = re.compile(r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.ASCII)
REVIEW_THRESHOLD = 0.70


def line_text(words: list[Word]) -> str:
    return " ".join(word.text for word in words)


def lines_for(page: Page) -> list[list[Word]]:
    if not page.words:
        return []
    tolerance = median(word.box.bottom - word.box.top for word in page.words) * 0.50
    lines: list[list[Word]] = []
    anchor = -1.0
    for word in sorted(page.words, key=lambda item: (item.box.top + item.box.bottom) / 2):
        center = (word.box.top + word.box.bottom) / 2
        if not lines or center - anchor > tolerance:
            lines.append([])
            anchor = center
        lines[-1].append(word)
    return [sorted(line, key=lambda word: word.box.left) for line in lines]


def resembles_transaction(line: list[Word]) -> bool:
    # Look for a date and two separate numeric cells without relying on the table headers.
    # A date-shaped token can signal a row even if it cannot be normalized for export.
    if not any(DATE_TOKEN.fullmatch(word.text) for word in line):
        return False
    amounts = [word for word in line if normalize("money", word.text) is not None]
    return len(amounts) >= 2 and amounts[-1].box.left - amounts[0].box.right >= 0.02


def field_from_words(key: str, kind: FieldKind, page: Page, words: list[Word]) -> ExtractedField:
    if not words:
        return ExtractedField(key=key, kind=kind)
    source = evidence(page, words)
    return ExtractedField(
        key=key,
        kind=kind,
        value=normalize(kind, source.text),
        evidence=[source],
        score=source.score,
    )


def scalar_value(key: str, kind: FieldKind, page: Page, line: list[Word]) -> ExtractedField | None:
    text = line_text(line)
    if key in {"period_start", "period_end"} and text.casefold().startswith("statement period"):
        dates = [word for word in line if normalize("date", word.text) is not None]
        if len(dates) != 2:
            return None
        return field_from_words(key, kind, page, [dates[int(key == "period_end")]])
    aliases = "|".join(re.escape(alias) for alias in SCALARS[key][1])
    match = re.fullmatch(rf"(?:{aliases})\b\s*[:#.]?\s*(.+)", text, flags=re.I)
    if not match:
        return None
    candidate = match[1]
    # Search only the value suffix for label/value pairs.
    source = ground(kind, candidate, page, [line[-len(candidate.split()) :]])
    if source is None:
        return None
    return ExtractedField(
        key=key, kind=kind, value=normalize(kind, candidate), evidence=[source], score=source.score
    )


def table_header(words: list[Word]) -> list[tuple[str, Word]] | None:
    columns = [
        (name, word)
        for word in words
        for name, labels in COLUMN_LABELS.items()
        if word.text.lower().strip(":") in labels
    ]
    names = {name for name, _ in columns}
    if len(names) != len(columns) or not {"date", "description", "balance"} <= names:
        return None
    if not (names & {"amount", "debit", "credit"}) or (
        "amount" in names and names & {"debit", "credit"}
    ):
        return None
    return columns


def split_cells(columns: list[tuple[str, Word]], words: list[Word]) -> dict[str, list[Word]]:
    # Headers and cells may align at either edge. Midpoints in the whitespace preceding
    # column headers allow right-aligned currency values while preserving descriptions.
    edges = []
    for index, (name, word) in enumerate(columns[1:], start=1):
        previous = columns[index - 1][1]
        padding = 0.04 if name in {"amount", "debit", "credit", "balance"} else 0.02
        edges.append(max((previous.box.left + word.box.left) / 2, word.box.left - padding))
    cells: dict[str, list[Word]] = {name: [] for name, _ in columns}
    for word in words:
        center = (word.box.left + word.box.right) / 2
        index = sum(center >= edge for edge in edges)
        cells[columns[index][0]].append(word)
    return cells


def amount_field(key: str, page: Page, cells: dict[str, list[Word]]) -> ExtractedField:
    if "amount" in cells:
        return field_from_words(key, "money", page, cells["amount"])
    debit = field_from_words(key, "money", page, cells.get("debit", []))
    credit = field_from_words(key, "money", page, cells.get("credit", []))
    present = [field for field in (debit, credit) if field.evidence]
    if not present:
        return debit
    sources = [item for field in present for item in field.evidence]
    score = min(item.score for item in sources)
    if any(field.value is None for field in present):
        return ExtractedField(key=key, kind="money", evidence=sources, score=score)
    debit_amount = abs(Decimal(debit.value or "0"))
    credit_amount = Decimal(credit.value or "0")
    if credit_amount < 0 or (debit_amount and credit_amount):
        return ExtractedField(key=key, kind="money", evidence=sources, score=score)
    return ExtractedField(
        key=key,
        kind="money",
        value=f"{credit_amount - debit_amount:.2f}",
        evidence=sources,
        score=score,
    )


def extract(pages: list[Page]) -> Result:
    scalars: dict[str, ExtractedField] = {}
    fields: list[ExtractedField] = []
    transactions: list[Transaction] = []
    issues: list[Issue] = []
    columns = None
    checkpoints: list[Checkpoint] = []
    for page in pages:
        if not page.words:
            issues.append(
                Issue(
                    code="unreadable_page", message=f"No text was recognized on page {page.number}."
                )
            )
        previous_description: ExtractedField | None = None
        table_active = False
        lines = lines_for(page)
        accounted_lines: set[int] = set()
        for line_no, line in enumerate(lines, start=1):
            text = line_text(line)
            found_scalar = False
            for key, (kind, _) in SCALARS.items():
                candidate = scalar_value(key, kind, page, line)
                if candidate is not None:
                    found_scalar = True
                    if key in scalars and candidate.value != scalars[key].value:
                        issues.append(
                            Issue(
                                code="source_conflict",
                                message=f"Conflicting {key.replace('_', ' ')} on page {page.number}.",
                                field_keys=[key],
                            )
                        )
                    else:
                        scalars.setdefault(key, candidate)
            if found_scalar:
                accounted_lines.add(line_no)
                if any(
                    text.casefold().startswith(alias) for alias in SCALARS["closing_balance"][1]
                ):
                    columns = None
                    table_active = False
                continue
            if header := table_header(line):
                columns = header
                table_active = True
                previous_description = None
                continue
            if columns is None or FOOTER.fullmatch(text):
                continue
            cells = split_cells(columns, line)
            carry = (
                not cells["date"]
                and CARRY.fullmatch(line_text(cells["description"]))
                and not any(cells.get(name) for name in ("debit", "credit", "amount"))
            )
            if carry:
                accounted_lines.add(line_no)
                anchor = f"{transactions[-1].key}.balance" if transactions else "opening_balance"
                checkpoint = field_from_words("checkpoint", "money", page, cells["balance"])
                checkpoints.append(
                    Checkpoint(
                        page=page.number,
                        previous_balance_key=anchor,
                        value=checkpoint.value,
                        evidence=checkpoint.evidence,
                    )
                )
                table_active = True
                previous_description = None
                continue
            if not table_active:
                if normalize("date", line_text(cells["date"])) is None:
                    continue
                table_active = True
            non_description = any(words for name, words in cells.items() if name != "description")
            if not non_description and previous_description and cells.get("description"):
                continuation = evidence(page, cells["description"])
                previous_description.evidence.append(continuation)
                previous_description.value = normalize(
                    "text", " ".join(item.text for item in previous_description.evidence)
                )
                previous_description.score = min(
                    item.score for item in previous_description.evidence
                )
                continue
            if not non_description:
                issues.append(
                    Issue(code="unmapped_line", message=f"Unmapped line on page {page.number}.")
                )
                continue
            row_key = f"p{page.number}-l{line_no}"
            accounted_lines.add(line_no)
            transactions.append(Transaction(key=row_key, page=page.number))
            row = {
                name: field_from_words(f"{row_key}.{name}", kind, page, cells.get(name, []))
                for name, kind in ROW_KINDS.items()
                if name != "amount"
            }
            row["amount"] = amount_field(f"{row_key}.amount", page, cells)
            fields.extend(row.values())
            previous_description = row["description"]
        if any(
            resembles_transaction(line)
            for line_no, line in enumerate(lines, start=1)
            if line_no not in accounted_lines
        ):
            issues.append(
                Issue(
                    code="unmapped_transactions",
                    message=f"Some transaction rows on page {page.number} could not be mapped. "
                    "Check the source layout.",
                )
            )
    return Result(
        fields=[
            scalars.get(key) or ExtractedField(key=key, kind=kind)
            for key, (kind, _) in SCALARS.items()
        ]
        + fields,
        transactions=transactions,
        issues=issues,
        checkpoints=checkpoints,
    )


def validate(result: Result, decisions: dict) -> Result:
    """Validate the effective values from scratch after every review action."""
    result = result.model_copy(deep=True)
    by_key = {field.key: field for field in result.fields}
    for key, decision in decisions.items():
        if key not in by_key or decision["source"] != signature(by_key[key]):
            result.issues.append(
                Issue(
                    code="review_conflict",
                    message=f"The evidence for {key} changed. Review the current extraction.",
                    field_keys=[key] if key in by_key else [],
                    conflict_key=key,
                )
            )
    for field in result.fields:
        decision = decisions.get(field.key)
        if decision and decision["source"] == signature(field):
            field.status = {"approve": "approved", "correct": "corrected", "reject": "rejected"}[
                decision["action"]
            ]
            field.value = decision["value"]
            if decision["evidence"] is not None:
                field.evidence = [Evidence.model_validate(decision["evidence"])]
            field.score = None
        elif field.value is not None and field.evidence and (field.score or 0) >= REVIEW_THRESHOLD:
            field.status = "auto_accepted"
        if field.value is None:
            field.issues.append("missing_or_invalid")
        elif not field.evidence:
            field.issues.append("missing_evidence")
        elif field.status == "needs_review":
            field.issues.append("low_evidence_score")

    def issue(code: str, message: str, *keys: str) -> None:
        result.issues.append(Issue(code=code, message=message, field_keys=list(keys)))

    def money(key: str) -> Decimal | None:
        value = by_key[key].value
        return Decimal(value) if value is not None else None

    start, end = (by_key[key].value for key in ("period_start", "period_end"))
    if start and end and start > end:
        issue("invalid_period", "The statement period is reversed.", "period_start", "period_end")
    opening, closing = money("opening_balance"), money("closing_balance")
    previous = opening
    amounts: list[Decimal] = []
    breaks = 0
    for index, row in enumerate(result.transactions, start=1):
        key = row.key
        date_key, amount_key, balance_key = (
            f"{key}.{name}" for name in ("date", "amount", "balance")
        )
        posted = by_key[date_key].value
        if start and end and posted and not start <= posted <= end:
            issue(
                "date_outside_period",
                f"Transaction {index} falls outside the statement period.",
                date_key,
            )
        amount, balance = money(amount_key), money(balance_key)
        if amount is not None:
            amounts.append(amount)
        if (
            previous is not None
            and amount is not None
            and balance is not None
            and previous + amount != balance
        ):
            breaks += 1
            issue(
                "running_balance",
                f"Running balance breaks at transaction {index} on page {row.page}.",
                amount_key,
                balance_key,
            )
        # Anchor each check to the previous printed balance to avoid cascading flags.
        previous = balance
    if not result.transactions:
        issue("missing_transactions", "No transaction table was recognized.")
    if opening is not None and closing is not None and len(amounts) == len(result.transactions):
        if opening + sum(amounts, Decimal(0)) != closing:
            keys = () if breaks else ("opening_balance", "closing_balance")
            issue(
                "statement_total",
                "Transaction amounts do not reconcile to the closing balance.",
                *keys,
            )
    if result.transactions and previous is not None and closing is not None and previous != closing:
        issue(
            "closing_balance",
            "The final running balance differs from the closing balance.",
            "closing_balance",
            f"{result.transactions[-1].key}.balance",
        )
    for checkpoint in result.checkpoints:
        previous = money(checkpoint.previous_balance_key)
        if checkpoint.value is None or (
            previous is not None and Decimal(checkpoint.value) != previous
        ):
            issue(
                "carry_balance",
                f"Balance carried across page {checkpoint.page} disagrees with the preceding balance or is unreadable.",
                checkpoint.previous_balance_key,
            )
    result.issues = [
        item
        for item in result.issues
        if not (
            item.code == "source_conflict"
            and by_key[item.field_keys[0]].status == "corrected"
            and decisions[item.field_keys[0]]["evidence"] is not None
        )
    ]
    for item in result.issues:
        for key in item.field_keys:
            by_key[key].issues.append(item.code)
    for field in result.fields:
        if field.issues and field.status != "rejected":
            field.status = "needs_review"
    return result


def signature(field: ExtractedField) -> dict:
    return {
        "value": field.value,
        "evidence": [item.model_dump(mode="json") for item in field.evidence],
    }


def needs_review(result: Result) -> bool:
    return bool(result.issues) or any(
        field.status in {"needs_review", "rejected"} for field in result.fields
    )
