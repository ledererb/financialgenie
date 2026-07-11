/**
 * E2E test helpers.
 *
 * Assumes:
 *   - Backend on http://localhost:8765
 *   - Frontend on http://localhost:5180
 *   - Test PDF registered in catalog with a mapping
 */

export const TEST_PDF_ID = "samples/Szemelyi_adatlap_Igenylo_4old.pdf";

/**
 * Navigate to the review dashboard for the first document.
 *
 * The sidebar tree: Bank (expanded by default) → Product → Document.
 * Clicking a document in the tree opens it in the wizard (onOpenDocument).
 */
export async function navigateToFirstDocument(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/");
  await page.waitForSelector("text=Mapping Stúdió", { timeout: 10_000 });

  // Products are visible (bank expanded by default). Click the product
  // that has documents — find it by the "dok." badge.
  const productBadge = page.locator("text=/\\d+\\s*dok\\./").first();
  await productBadge.waitFor({ state: "visible", timeout: 15_000 });
  await productBadge.click();

  // Documents (.tree-leaf) appear after the product is clicked
  await page.waitForSelector(".tree-leaf", { timeout: 5000 });
  await page.locator(".tree-leaf").first().click();

  // The document click opens the wizard at the review step (or analysis
  // auto-skip). Wait for the review dashboard heatmap.
  await page.waitForSelector(".heatmap-grid", { timeout: 30_000 });
}
