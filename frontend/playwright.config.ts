import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 55_000,
  workers: 1,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.WORKBENCH_UI_URL ?? "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
});
