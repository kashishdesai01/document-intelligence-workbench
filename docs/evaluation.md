# Evaluation

Run the frozen synthetic suite from the project root.

```sh
uv run python -m eval.runner --check
```

The evaluator produces JSON and Markdown in `eval/output`. Seeds 100–111 fit the isotonic model. Seeds 200–229 form the frozen evaluation split. The generator varies page count, debit/credit versus signed amount columns, OCR degradation, and right-angle rotation. It injects one incorrect printed amount into five evaluation statements and keeps the correct value in the labels.

## Metrics

- **Exact document accuracy** requires exact normalized agreement on all scalar fields and transaction cells in statement order. A missing or extra row counts against this metric.
- **Straight-through rate** measures documents with no review items after routing. The report counts incorrect straight-through documents separately.
- **Auto-accepted error rate** measures wrong fields among values accepted without human input. An observed zero on a small synthetic split does not estimate risk on other formats.
- **Cell F1** uses exact matches at the same row position and field. A missing row can shift later rows and lower this score.
- **Expected calibration error** compares average evidence score with observed correctness across ten bins. The offline isotonic fit uses a disjoint synthetic split. Hard grounding and reconciliation gates remain active. The deployed review console presents raw scores because real-data calibration is unverified.
- **Source unmatched rate** measures exact-value outputs without source support. Full pipeline outputs carry field-linked evidence. Raw model baselines are checked against normalized OCR or native text anywhere in the document, with allowance for a sign derived from a debit column. A document-wide match gives no row-level provenance.
- **Model tokens** record provider-reported prompt and generated token counts. Local provider cost is left unset.
- **p95** measures preprocessing and extraction for each document in the harness. Model variants add their request latency to shared preprocessing. Production queueing and infrastructure costs are outside this measurement.

The CI gate checks minimum exact accuracy, cell F1, and straight-through rate, a maximum accepted-field error rate, zero unsafe straight-through documents, and successful parsing on the frozen suite. Latency is reported for repeatable local comparisons. Runner timing varies across hosts.

## Optional text and vision baselines

With a local Ollama server on port 11434 and a vision-capable model already available, pass its tag:

```sh
uv run python -m eval.runner --ollama-model "$LOCAL_VISION_MODEL" --out eval/output/model-comparison
```

Add `--smoke` to exercise both adapter paths on two generated statements. The same model and output schema run once with page text and once with page images. Baselines return raw extraction. They do not have a review policy, so their straight-through and accepted-field metrics are omitted. Failed or truncated responses count as failed documents. The service never invokes these adapters. The local adapter smoke run used Ollama 0.40.16 and Qwen2.5-VL 3B (digest prefix `fb90415cde1e`). Both modalities returned schema-valid responses for both generated statements. Two documents establish request and response handling. A larger comparison needs a representative labeled holdout. [Smoke-run JSON](model-smoke.json) · [Model card](https://ollama.com/library/qwen2.5vl:3b). [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) · [Ollama vision input](https://docs.ollama.com/capabilities/vision)

## Private holdout

Keep appropriately authorized, redacted statement PDFs and labels outside version control. For a private model comparison, use local weights with cloud features disabled in Ollama. Pass `--manifest /path/to/manifest.json` to score a labeled dataset. Keep the holdout fixed before tuning the extractor. Each manifest entry specifies a path relative to the manifest and a complete expected statement:

```json
[
  {
    "file": "statement-001.pdf",
    "expected": {
      "account_id": "***8109",
      "period_start": "2025-04-01",
      "period_end": "2025-04-30",
      "opening_balance": "100.00",
      "closing_balance": "75.00",
      "transactions": [
        {"date":"2025-04-02","description":"Transit Pass","amount":"-25.00","balance":"75.00"}
      ]
    }
  }
]
```

The project includes generated statements and a frozen synthetic report. Real-document accuracy, production load, and live-model comparisons require separate evidence.
