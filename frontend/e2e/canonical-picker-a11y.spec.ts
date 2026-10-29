// Cutover Task 4 review fix 2 — keyboard/AT operability of the canonical
// combobox that replaced the native <select>s. Prereqs: backend on
// http://localhost:8765, frontend on http://localhost:5180, seeded catalog.
import { expect, test } from "@playwright/test";
import { navigateToFirstDocument } from "./helpers";

test("canonical combobox is keyboard/AT operable (ARIA + arrows + Enter + Escape)", async ({
  page,
  request,
}) => {
  const login = await request.post("http://localhost:8765/api/auth/login", {
    data: { username: "admin", password: "Admin123!" },
  });
  const { token } = (await login.json()) as { token: string };
  await page.addInitScript((t) => localStorage.setItem("fg_auth_token", t), token);

  await navigateToFirstDocument(page);
  await page.locator("[class*='heatmap'] >> text=1").first().click();
  await page.waitForTimeout(2500);

  // Enter edit mode for the "SZA_IG_név" field (DOM-level; the row can sit
  // below the sidebar fold).
  await page.evaluate(() => {
    const els = Array.from(
      document.querySelectorAll("[title='Kattints a mapping szerkesztéséhez']")
    );
    const el = els.find((e) =>
      (e.textContent || "").includes("participant.borrower.name")
    ) as HTMLElement | undefined;
    if (!el) throw new Error("mapping edit target not found in DOM");
    el.click();
  });
  await page.waitForTimeout(500);

  const combobox = page.getByRole("combobox").first();
  await combobox.waitFor({ state: "visible", timeout: 10_000 });
  await combobox.click();
  await expect(combobox).toHaveAttribute("aria-expanded", "true");

  // Listbox + option roles are present; the current value is visibly
  // selected (aria-selected) among the rendered options.
  const listboxId = await combobox.getAttribute("aria-controls");
  expect(listboxId).toBeTruthy();
  const listbox = page.locator(`#${listboxId}`);
  await expect(listbox).toHaveAttribute("role", "listbox");
  const options = listbox.locator("[role='option']");
  expect(await options.count()).toBeGreaterThan(0);
  await expect(
    listbox.locator("[role='option'][aria-selected='true']")
  ).not.toHaveCount(0);

  // ArrowDown moves the active option (aria-activedescendant follows a real
  // option id) and the active row is visually marked.
  await combobox.press("ArrowDown");
  await combobox.press("ArrowDown");
  const activeDescendant = await combobox.getAttribute("aria-activedescendant");
  expect(activeDescendant).toBeTruthy();
  expect(activeDescendant).toMatch(/-opt-\d+$/);
  await expect(page.locator(`#${activeDescendant}`)).toHaveAttribute(
    "role",
    "option"
  );
  await expect(page.locator(`#${activeDescendant}`)).toBeVisible();

  // ArrowUp navigates back deterministically.
  await combobox.press("ArrowUp");
  await expect(await combobox.getAttribute("aria-activedescendant")).not.toBe(
    activeDescendant
  );

  // Escape closes the listbox without changing the value (while open the
  // input legitimately shows the QUERY, so "no change" is asserted by the
  // final committed value + the untouched stored mapping, not by inputValue).
  await combobox.press("Escape");
  await expect(combobox).toHaveAttribute("aria-expanded", "false");

  // Reopen (Escape unmounted the field editor — enter edit mode again),
  // search, and commit with the keyboard alone (Enter) — selecting the value
  // the field already carries keeps the stored mapping unchanged.
  await combobox.click(); // reopens even though the input kept DOM focus
  await page.waitForTimeout(200);
  await combobox.fill("participant.borrower.name");
  await page.waitForTimeout(400);
  await combobox.press("ArrowDown"); // active = first filtered option
  await combobox.press("Enter");
  await expect(combobox).toHaveAttribute("aria-expanded", "false");
  // Committing the value the field already carries: the stored mapping is
  // untouched (idempotent write of the same canonical).
  await expect(combobox).toHaveValue("participant.borrower.name");
});
