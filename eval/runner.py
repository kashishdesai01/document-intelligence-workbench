"""Reproducible synthetic evaluation, with an optional private labeled holdout."""

import argparse
import hashlib
import json
import platform
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx

from eval.baselines import ollama, text_support
from eval.metrics import (
    Measurements,
    Statement,
    calibrate,
    fit_isotonic,
    flatten,
    flatten_result,
    reliability,
)
from eval.synth import statement
from workbench.db import PIPELINE_VERSION
from workbench.extraction import extract, validate
from workbench.parser_client import parse_page, preflight
from workbench.parsing import MAX_BYTES, ParseFailure


def synthetic(seeds: range):
    for seed in seeds:
        yield statement(
            seed,
            pages=2 if seed % 3 == 0 else 1,
            scanned=seed % 4 != 0,
            split_columns=seed % 3 != 2,
            rotation=90 if seed % 8 == 3 else 0,
            degraded=seed % 5 == 1,
            broken=seed % 6 == 2,
        )


def load_manifest(path: Path):
    manifest = json.loads(path.read_text())
    if not manifest or not isinstance(manifest, list):
        raise ValueError("Provide a nonempty list of labeled documents")
    seen = set()
    for entry in manifest:
        with (path.parent / entry["file"]).open("rb") as file:
            raw = file.read(MAX_BYTES + 1)
        if not 0 < len(raw) <= MAX_BYTES:
            raise ValueError("A labeled document exceeds the size limit or is empty")
        fingerprint = hashlib.sha256(raw).digest()
        if fingerprint in seen:
            raise ValueError("Duplicate document in manifest")
        seen.add(fingerprint)
        yield raw, entry["expected"]


def evaluate(samples, model: str | None = None) -> dict[str, Measurements]:
    variants = {"full": Measurements()}
    if model:
        variants.update(text_only=Measurements(), vision_llm=Measurements())
    with httpx.Client(base_url="http://127.0.0.1:11434", timeout=240, trust_env=False) as client:
        for source, truth in samples:
            expected = flatten(Statement.model_validate(truth))
            if len(expected) != 5 + 4 * len(truth["transactions"]):
                raise ValueError("Labels must cover every required scalar and transaction cell")
            start = time.perf_counter()
            try:
                parsed = [parse_page(source, number) for number in range(1, preflight(source) + 1)]
                pages, images = map(list, zip(*parsed, strict=True))
                preprocessing_time = time.perf_counter() - start
                result = validate(extract(pages), {})
            except ParseFailure:
                for measurement in variants.values():
                    measurement.record(expected, {}, time.perf_counter() - start, failed=True)
                continue
            extraction_time = time.perf_counter() - start
            prediction = {
                key: item[0] for key, item in flatten_result(result).items() if item[0] is not None
            }
            variants["full"].record(expected, prediction, extraction_time, result=result)
            for name in ("text_only", "vision_llm") if model else ():
                start = time.perf_counter()
                failed = False
                try:
                    answer = ollama(client, model, name, pages, images)
                    values = flatten(answer.statement)
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    failed, values = True, {}
                duration = preprocessing_time + time.perf_counter() - start
                measurement = variants[name]
                measurement.record(expected, values, duration, failed=failed)
                if not failed:
                    unmatched, checked = text_support(values, pages)
                    measurement.source_unmatched += unmatched
                    measurement.evidence_checked += checked
                    measurement.prompt_tokens += answer.prompt_tokens
                    measurement.generated_tokens += answer.generated_tokens
    return variants


def revision() -> str | None:
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    return process.stdout.strip() if process.returncode == 0 else None


def tool_versions() -> dict:
    try:
        run = subprocess.run(["tesseract", "--version"], capture_output=True, text=True, check=True)
        ocr = run.stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        ocr = None
    return {"ocr": ocr, "pdfium": version("pypdfium2"), "pillow": version("pillow")}


def source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("workbench/**/*.py"), *root.glob("eval/*.py"), root / "uv.lock"]
    hasher = hashlib.sha256()
    for path in sorted(files):
        hasher.update(str(path.relative_to(root)).encode() + b"\0")
        hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()


def quality_gate(report: dict) -> None:
    full = report["variants"]["full"]
    if (
        full["failed"]
        or full["unsafe_straight_through_documents"]
        or not full["document_accuracy"]
        or full["document_accuracy"] < 0.70
        or not full["cell_f1"]
        or full["cell_f1"] < 0.92
        or full["auto_accepted_error_rate"] is None
        or full["auto_accepted_error_rate"] > 0.02
        or not full["straight_through"]
        or full["straight_through"] < 0.60
    ):
        raise SystemExit("Synthetic extraction fell outside the quality gates. Inspect the report.")


def markdown(report: dict) -> str:
    lines = [
        f"# Evaluation · {report['dataset']}",
        "",
        f"Pipeline `{report['pipeline']}` · {report['timestamp']} · "
        f"Git revision `{report['git_revision'] or 'unavailable'}`",
        "",
        "| Variant | Documents | Exact | Straight-through | Auto-accepted error | Cell F1 | p95 seconds | Failed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def percent(value):
        return "—" if value is None else f"{value:.1%}"

    for name, values in report["variants"].items():
        lines.append(
            f"| {name} | {values['documents']} | {percent(values['document_accuracy'])} | "
            f"{percent(values['straight_through'])} | {percent(values['auto_accepted_error_rate'])} | "
            f"{percent(values['cell_f1'])} | {values['p95_seconds']} | {values['failed']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--manifest", type=Path)
    cli.add_argument("--ollama-model")
    cli.add_argument("--out", type=Path, default=Path("eval/output"))
    cli.add_argument("--check", action="store_true")
    cli.add_argument(
        "--smoke", action="store_true", help="compare models on two generated statements"
    )
    args = cli.parse_args()
    if args.check and (args.manifest or args.ollama_model or args.smoke):
        cli.error("--check applies to the frozen synthetic suite")
    if args.smoke and (not args.ollama_model or args.manifest):
        cli.error("--smoke requires --ollama-model and uses generated documents")
    seeds = range(201, 203) if args.smoke else range(200, 230)
    dataset = (
        "private holdout"
        if args.manifest
        else (
            "synthetic adapter smoke (seeds 201–202)"
            if args.smoke
            else "synthetic evaluation split (seeds 200–229)"
        )
    )
    git_revision = revision()
    fingerprint = source_fingerprint()
    calibration = evaluate(synthetic(range(100, 112)))["full"].evidence_outcomes
    models = evaluate(
        load_manifest(args.manifest) if args.manifest else synthetic(seeds),
        args.ollama_model,
    )
    outcomes = models["full"].evidence_outcomes
    fit = fit_isotonic(calibration)
    report = {
        "dataset": dataset,
        "pipeline": PIPELINE_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "git_revision": git_revision,
        "platform": platform.platform(),
        "source_sha256": fingerprint,
        "tools": tool_versions(),
        "ollama_model": args.ollama_model,
        "variants": {name: values.report(name) for name, values in models.items()},
        "calibration": {
            "fit_split": "synthetic seeds 100–111",
            "fit_fields": len(calibration),
            "fit_blocks": fit,
            "raw": reliability(outcomes),
            "isotonic": reliability(
                [(calibrate(score, fit), correct) for score, correct in outcomes]
            ),
            "used_for_routing": False,
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "report.md").write_text(markdown(report))
    print(markdown(report))
    if args.check:
        quality_gate(report)
    if args.ollama_model and any(
        models[name].failed == models[name].documents for name in ("text_only", "vision_llm")
    ):
        raise SystemExit(
            "A model variant returned no usable responses. Check the local model and server."
        )


if __name__ == "__main__":
    main()
