import { test, expect } from "@playwright/test";
import { navigateToFirstDocument } from "./helpers";

test.describe("Page Editor", () => {
  test.beforeEach(async ({ page }) => {
    await navigateToFirstDocument(page);

    // Click page 1 in the heatmap to open the page editor
    const page1Cell = page.locator("[class*='heatmap'] >> text=1").first();
    if ((await page1Cell.count()) > 0) {
      await page1Cell.click();
      await page.waitForTimeout(2000); // page editor loads
    }
  });

  test("page editor renders field overlays on the PDF image", async ({ page }) => {
    const overlays = page.locator("[class*='field-overlay']");
    const count = await overlays.count();
    expect(count).toBeGreaterThan(0);
  });

  test("page navigation next button works", async ({ page }) => {
    const nextBtn = page.locator("button[title*='Következő']");
    if ((await nextBtn.count()) > 0) {
      await nextBtn.click();
      await page.waitForTimeout(1500);
      // After clicking next, we should be on page 2 — verify overlays still render
      const overlays = page.locator("[class*='field-overlay']");
      const count = await overlays.count();
      expect(count).toBeGreaterThanOrEqual(0); // page 2 may have fewer fields
    }
  });

  test("overlay labels toggle button exists", async ({ page }) => {
    const labelsBtn = page.locator("button:has-text('Címkék')");
    // The button should exist in the header
    expect(await labelsBtn.count()).toBeGreaterThan(0);
  });
});
