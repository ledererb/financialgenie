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
  await page.waitForSelector("text=Dokumentumtár", { timeout: 10_000 });

  // Wait for document rows in LibraryView and click sza_ig_igenylő
  const docRow = page.locator("[role='button']", { hasText: /sza_ig_igenyl/i }).first();
  await docRow.waitFor({ state: "visible", timeout: 15_000 });
  await docRow.click();

  // Wait for the review dashboard heatmap
  await page.waitForSelector(".heatmap-grid", { timeout: 30_000 });
}
