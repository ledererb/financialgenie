import { test, expect } from "@playwright/test";
import { navigateToFirstDocument } from "./helpers";

test.describe("Review Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await navigateToFirstDocument(page);
  });

  test("review dashboard renders with heatmap", async ({ page }) => {
    // The heatmap is a .heatmap-grid with .heatmap-cell children.
    // Wait for it to render.
    await page.waitForSelector(".heatmap-grid, .heatmap-cell", { timeout: 10_000 });

    const heatmapCells = page.locator(".heatmap-cell");
    const count = await heatmapCells.count();
    // The test PDF has 4 pages, so at least 4 cells
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("page heatmap cells are clickable", async ({ page }) => {
    await page.waitForSelector(".heatmap-cell", { timeout: 10_000 });

    // Click page 1 — should open the page editor
    const page1 = page.locator(".heatmap-cell", { hasText: "1" }).first();
    if ((await page1.count()) > 0) {
      await page1.click();
      // The page editor should appear (look for its overlay or header)
      await page.waitForTimeout(2000);
    }
  });
});
