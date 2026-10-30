import io
import shutil

import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen.canvas import Canvas

from eval.synth import statement
from workbench.extraction import extract, lines_for, needs_review, validate
from workbench.grounding import ground, normalize
from workbench.models import Box, Page, Word
from workbench.parser_client import parse_page, preflight
from workbench.parsing import ParseFailure


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("($1,284.56)", "-1284.56"),
        ("$ 32.14 DR", "-32.14"),
        ("18 CR", "18.00"),
        ("−12.03", "-12.03"),
        ("-0.00", "0.00"),
        ("12,34.00", None),
        ("--23.21", None),
        ("2.100", None),
        ("(-32.14)", None),
        ("(32.14 DR)", None),
        ("1e4", None),
    ],
)
def test_money_normalization(raw, expected):
    assert normalize("money", raw) == expected


def test_exact_grounding_rejects_digit_and_substring_matches():
    word = Word(text="1,284.56", box=Box(left=0.2, top=0.3, right=0.5, bottom=0.4), score=0.91)
    page = Page(number=1, width=100, height=100, source="ocr", words=[word])
    assert ground("money", "1234.56", page, [[word]]) is None
    assert ground("money", "284.56", page, [[word]]) is None
    assert ground("money", "1284.56", page, [[word]]).box == word.box
    assert ground("text", "284", page, [[word]]) is None


def test_invalid_date_and_account_substring():
    assert normalize("date", "02/29/2025") is None
    assert normalize("date", "02/29/2024") == "2024-02-29"
    assert normalize("date", "01/02/2025") == "2025-01-02"
    word = Word(text="***8109", box=Box(left=0.2, top=0.3, right=0.5, bottom=0.4), score=1)
    page = Page(number=1, width=100, height=100, source="native", words=[word])
    assert ground("id", "8109", page, [[word]]) is None


def run(source):
    pages = [parse_page(source, number)[0] for number in range(1, preflight(source) + 1)]
    return pages, validate(extract(pages), {})


def test_native_multipage_debit_credit():
    source, truth = statement(17, pages=2, split_columns=True)
    pages, result = run(source)
    assert [page.source for page in pages] == ["native", "native"]
    assert len(result.transactions) == 10
    assert not needs_review(result)
    fields = {item.key: item for item in result.fields}
    for row, expected in zip(result.transactions, truth["transactions"], strict=True):
        for name, value in expected.items():
            field = fields[f"{row.key}.{name}"]
            assert field.value == value
            assert field.evidence[0].page == row.page


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract is not installed")
def test_rotated_scan_and_localized_break():
    source, truth = statement(51, pages=2, scanned=True, broken=True, rotation=90)
    pages, result = run(source)
    assert all(page.source == "ocr" and page.rotation == 270 for page in pages)
    assert len(result.transactions) == 10
    failures = [issue for issue in result.issues if issue.code == "running_balance"]
    row = result.transactions[2]
    assert [issue.field_keys for issue in failures] == [[f"{row.key}.amount", f"{row.key}.balance"]]
    assert truth["transactions"][2]["amount"] == "-170.27"
    assert all(field.evidence for field in result.fields)


def test_file_type_and_encryption():
    with pytest.raises(ParseFailure, match="unsupported_type"):
        preflight(b"not a document")
    encrypted = io.BytesIO()
    canvas = Canvas(encrypted, encrypt=StandardEncryption("a password"))
    canvas.drawString(100, 100, "Encrypted.")
    canvas.save()
    with pytest.raises(ParseFailure, match="encrypted_pdf"):
        preflight(encrypted.getvalue())


def test_page_boundary_mismatch_blocks_export_without_dropping_dated_row():
    source, _ = statement(17, pages=2)
    pages, _ = run(source)
    # Retain all source boxes and change only the carry-forward amount.
    carry_words = next(
        line for line in lines_for(pages[1]) if "Brought" in [word.text for word in line]
    )
    carry_words[-1].text = "123.45"
    result = validate(extract(pages), {})
    issues = [item for item in result.issues if item.code == "carry_balance"]
    assert len(result.transactions) == 10
    assert len(issues) == 1
    assert issues[0].field_keys == [f"{result.transactions[4].key}.balance"]
    assert needs_review(result)


def test_empty_page_cannot_silently_complete():
    source, _ = statement(21)
    pages, _ = run(source)
    pages.append(Page(number=2, width=100, height=100, source="ocr", words=[]))
    result = validate(extract(pages), {})
    assert any(issue.code == "unreadable_page" for issue in result.issues)
    assert needs_review(result)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract is not installed")
def test_invisible_pdf_text_is_not_treated_as_visible_evidence():
    source = io.BytesIO()
    canvas = Canvas(source)
    canvas.drawString(70, 640, "Visible bank statement")
    hidden = canvas.beginText(70, 600)
    hidden.setTextRenderMode(3)
    hidden.textLine("Closing Balance: 99999.99")
    canvas.drawText(hidden)
    canvas.save()
    page, _ = parse_page(source.getvalue(), 1)
    assert page.source == "ocr"
    assert "99999.99" not in [word.text for word in page.words]


def test_aliases_right_aligned_values_and_multiline_description():
    source = io.BytesIO()
    canvas = Canvas(source)
    canvas.setFont("Helvetica", 11)
    for y, line in (
        (760, "Account ID: ***8109"),
        (730, "Statement Period: 2025-03-01 to 2025-03-31"),
        (700, "Beginning Balance: 1,050.00"),
        (485, "Ending Balance: 1,039.50"),
    ):
        canvas.drawString(56, y, line)
    for x, text in ((55, "Posted"), (190, "Memo"), (354, "Amount"), (488, "Balance")):
        canvas.drawString(x, 660, text)
    for y, date, description, amount, balance in (
        (620, "2025-03-02", "Subscription", "23.00 DR", "1,027.00"),
        (540, "2025-03-04", "Refund", "12.50 CR", "1,039.50"),
    ):
        canvas.drawString(55, y, date)
        canvas.drawString(190, y, description)
        canvas.drawRightString(404, y, amount)
        canvas.drawRightString(544, y, balance)
    canvas.drawString(190, 600, "Quarterly plan")
    canvas.save()
    _, result = run(source.getvalue())
    assert not needs_review(result)
    assert len(result.transactions) == 2
    fields = {field.key: field for field in result.fields}
    first, second = (row.key for row in result.transactions)
    assert fields[f"{first}.amount"].value == "-23.00"
    assert fields[f"{second}.amount"].value == "12.50"
    assert fields[f"{first}.description"].value == "Subscription Quarterly plan"
    assert len(fields[f"{first}.description"].evidence) == 2
