"""Generate fictitious statements with known values, including broken totals."""

import argparse
import io
import random
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

import pypdfium2
from PIL import ImageFilter, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

WIDTH, HEIGHT = 612, 792
PAYEES = ("Cedar Market", "Payroll", "Studio Rent", "Transit Pass", "Bank Transfer", "Bookstore")
INK = HexColor("#1d2d47")
BLUE = HexColor("#214c9c")


def money(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def printed(cents: int) -> str:
    amount = f"{abs(Decimal(cents) / 100):,.2f}"
    return f"({amount})" if cents < 0 else amount


def statement(
    seed: int,
    *,
    pages: int = 1,
    scanned: bool = False,
    broken: bool = False,
    split_columns: bool = False,
    rotation: int = 0,
    degraded: bool = False,
) -> tuple[bytes, dict]:
    rng = random.Random(seed)
    month = seed % 12 + 1
    start, end = date(2025, month, 1), date(2025, month, 28)
    account = f"{rng.randrange(100_000, 999_999)}"
    opening = rng.randrange(110_000, 830_000)
    balance = opening
    total_rows = pages * 5
    transactions = []
    for index in range(total_rows):
        amount = rng.randrange(900, 42000) * (1 if index % 3 == 1 else -1)
        balance += amount
        transactions.append(
            (start.replace(day=index + 2), PAYEES[index % len(PAYEES)], amount, balance)
        )
    truth = {
        "account_id": account,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "opening_balance": money(opening),
        "closing_balance": money(balance),
        "transactions": [
            dict(
                date=posted.isoformat(),
                description=desc,
                amount=money(amount),
                balance=money(row_balance),
            )
            for posted, desc, amount, row_balance in transactions
        ],
    }

    native = io.BytesIO()
    canvas = Canvas(native, pagesize=(WIDTH, HEIGHT), invariant=True)
    for page in range(pages):
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 24)
        canvas.drawString(46, 730, "NORTHSTAR BANK" if seed % 2 else "JUNIPER COMMUNITY BANK")
        canvas.setFillColor(BLUE)
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(46, 702, "Bank Statement")
        canvas.setStrokeColor(HexColor("#b0c2de"))
        canvas.line(46, 687, 566, 687)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica", 12)
        canvas.drawString(46, 656, f"Account Number: {account}")
        canvas.drawString(46, 633, f"Statement Period: {start:%m/%d/%Y} to {end:%m/%d/%Y}")
        canvas.drawString(46, 610, f"Opening Balance: {printed(opening)}")
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(46, 560, "Transaction Activity")
        canvas.setFillColor(HexColor("#e9eff9"))
        canvas.roundRect(44, 514, 524, 29, 4, fill=1, stroke=0)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 11)
        x_date, x_description, x_amount, x_credit, x_balance = 50, 166, 332, 419, 503
        columns = [(x_date, "Date"), (x_description, "Description"), (x_balance, "Balance")]
        columns.extend(
            [(x_amount, "Debit"), (x_credit, "Credit")] if split_columns else [(x_amount, "Amount")]
        )
        for x, name in columns:
            canvas.drawString(x, 524, name)
        y = 484
        canvas.setFont("Helvetica", 11)
        if page:
            canvas.drawString(x_description, y, "Balance Brought Forward")
            canvas.drawString(x_balance, y, printed(transactions[page * 5 - 1][3]))
            y -= 31
        for index in range(page * 5, (page + 1) * 5):
            posted, description, amount, row_balance = transactions[index]
            if broken and index == 2:
                amount += 1000
            canvas.drawString(x_date, y, posted.strftime("%m/%d/%Y"))
            canvas.drawString(x_description, y, description)
            x = x_amount if not split_columns or amount < 0 else x_credit
            canvas.drawString(x, y, printed(abs(amount) if split_columns else amount))
            canvas.drawString(x_balance, y, printed(row_balance))
            canvas.setStrokeColor(HexColor("#e0e7f1"))
            canvas.line(46, y - 12, 566, y - 12)
            y -= 31
        if page == pages - 1:
            canvas.setFont("Helvetica-Bold", 12)
            canvas.drawString(46, y - 12, f"Closing Balance: {printed(balance)}")
        else:
            canvas.drawString(46, y - 12, "Continued on next page")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(46, 50, f"Page {page + 1} of {pages}")
        canvas.showPage()
    canvas.save()
    if not scanned:
        return native.getvalue(), truth

    scanned_pdf = io.BytesIO()
    scan = Canvas(scanned_pdf, pagesize=(WIDTH, HEIGHT), invariant=True)
    with pypdfium2.PdfDocument(native.getvalue()) as pdf:
        for index in range(len(pdf)):
            with (
                closing(pdf[index]) as page,
                closing(page.render(scale=2.3 if degraded else 2.5)) as bitmap,
            ):
                image = ImageOps.grayscale(bitmap.to_pil()).convert("RGB")
            if rotation:
                image = image.rotate(-rotation, expand=True)
            if degraded:
                image = image.filter(ImageFilter.GaussianBlur(0.7))
                compressed = io.BytesIO()
                image.save(compressed, format="JPEG", quality=48)
                raster = ImageReader(io.BytesIO(compressed.getvalue()))
            else:
                raster = ImageReader(image)
            size = (HEIGHT, WIDTH) if rotation in {90, 270} else (WIDTH, HEIGHT)
            scan.setPageSize(size)
            scan.drawImage(raster, 0, 0, *size)
            scan.showPage()
    scan.save()
    return scanned_pdf.getvalue(), truth


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    source, _ = statement(51, pages=2, scanned=True, broken=True)
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_bytes(source)


if __name__ == "__main__":
    main()
