import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("scanned sample flows through evidence, correction, audit, and export", async ({ page }) => {
  const key = process.env.WORKBENCH_E2E_KEY;
  if (!key) throw new Error("WORKBENCH_E2E_KEY is required");
  const capture = process.env.WORKBENCH_DEMO_CAPTURE;
  if (capture) await page.setViewportSize({ width: 1280, height: 1180 });
  async function captureStep(name: string) {
    if (!capture) return;
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    await page.screenshot({ path: `${capture}/${name}.png`, animations: "disabled" });
  }
  await page.goto("/");
  await page.getByLabel("Workspace API key").fill(key);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const sample = page.getByRole("button", { name: "Try scanned sample" });
  await expect(sample).toBeVisible();
  if (capture) {
    await expect(page.getByRole("region", { name: "Uploaded documents" }).getByRole("status")).toHaveCount(0);
    await captureStep("documents");
  }
  await sample.click();
  await expect(page.getByText("Running balance breaks at transaction 3 on page 1.")).toBeVisible({ timeout: 35_000 });
  await expect(page.getByText("OCR scan", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Export JSON" })).toBeDisabled();
  await page.getByRole("button", { name: "Inspect", exact: true }).click();
  await expect(page.getByLabel("Value", { exact: true })).toHaveValue("-160.27");
  await expect(page.locator("rect.evidence.selected")).toHaveCount(1);
  await captureStep("review");
  await page.getByLabel("Value", { exact: true }).fill("-170.27");
  await page.getByLabel("Review note").fill("Confirmed against the previous and current running balances.");
  await captureStep("correction");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible();
  await expect(page.getByText("Running balance breaks at transaction 3 on page 1.")).toHaveCount(0);
  await page.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByText("Confirmed against the previous and current running balances.")).toBeVisible();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.getByRole("img", { name: "Statement source, page 2" })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export JSON" }).click();
  const saved = await download;
  expect(saved.suggestedFilename()).toBe("statement.json");
  const result = JSON.parse(await readFile((await saved.path())!, "utf8"));
  expect(result.transactions).toHaveLength(10);
  expect(result.data.transactions[2].amount).toBe("-170.27");
  expect(result.fields).toContainEqual(expect.objectContaining({ status: "corrected", value: "-170.27" }));
  await captureStep("completed");
  await page.getByRole("button", { name: "Delete document", exact: true }).click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Documents", exact: true })).toBeVisible();
});

test("the synthetic evaluation is readable and interactive on mobile", async ({ page }) => {
  const key = process.env.WORKBENCH_E2E_KEY;
  if (!key) throw new Error("WORKBENCH_E2E_KEY is required");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/evaluation");
  await page.getByLabel("Workspace API key").fill(key);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Extraction evaluation" })).toBeVisible();
  await expect(page.getByText("25 of 30 documents exact")).toBeVisible();
  await page.getByRole("button", { name: "Offline fit" }).click();
  await expect(page.getByRole("button", { name: "Offline fit" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("table", { name: "Held-out score bins" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});
