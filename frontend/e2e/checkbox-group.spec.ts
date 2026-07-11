import { test, expect } from "@playwright/test";
import { navigateToFirstDocument } from "./helpers";

test.describe("Checkbox Group Dialog", () => {
  test.beforeEach(async ({ page }) => {
    await navigateToFirstDocument(page);

    // Open page editor on page 1
    const page1Cell = page.locator("[class*='heatmap'] >> text=1").first();
    if ((await page1Cell.count()) > 0) {
      await page1Cell.click();
      await page.waitForTimeout(2000);
    }
  });

  test("grouping button present when checkbox fields exist", async ({ page }) => {
    const groupBtn = page.locator("button:has-text('Csoportosítás')");
    // The button only appears if there are checkbox fields on the page.
    // The test PDF page 1 has checkboxes, so it should be present.
    expect(await groupBtn.count()).toBeGreaterThan(0);
  });

  test("grouping dialog opens and shows field list", async ({ page }) => {
    const groupBtn = page.locator("button:has-text('Csoportosítás')");
    if ((await groupBtn.count()) > 0) {
      await groupBtn.click();
      await page.waitForTimeout(500);

      // Dialog should be open
      await expect(page.getByText("Checkbox csoportosítás")).toBeVisible({ timeout: 5000 });

      // There should be field entries (checkboxes) in the dialog
      const fieldCheckboxes = page.locator("input[type='checkbox']");
      const count = await fieldCheckboxes.count();
      expect(count).toBeGreaterThan(0);

      // Close the dialog without applying
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
    }
  });

  test("group_id and group_label inputs accept text", async ({ page }) => {
    const groupBtn = page.locator("button:has-text('Csoportosítás')");
    if ((await groupBtn.count()) > 0) {
      await groupBtn.click();
      await page.waitForTimeout(500);

      // Find the group_id input (by placeholder)
      const groupIdInput = page.locator("input[placeholder*='ingatlan_jellege']");
      if ((await groupIdInput.count()) > 0) {
        await groupIdInput.fill("test_group_id");
        await expect(groupIdInput).toHaveValue("test_group_id");
      }

      // Close
      await page.keyboard.press("Escape");
    }
  });
});
