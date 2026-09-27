import { expect, test } from "@playwright/test";

import { BLOCKED_CODE, BLOCKED_NUMBER, openFromQueue, scrollsSideways, signIn } from "./helpers";

// One seeded invoice, in order: the first test finds it in the queue and the others go straight to it
// (closing its exceptions moves it down the queue).
test.describe.configure({ mode: "serial" });
let invoiceUrl = "";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("approval is blocked until a blocking exception is closed with a note", async ({ page }) => {
  await openFromQueue(page, BLOCKED_NUMBER, BLOCKED_CODE);
  invoiceUrl = page.url();
  const approve = page.getByRole("button", { name: "Approve" });
  await expect(approve).toBeDisabled();
  await expect(page.getByTestId("approve-blocked")).toBeVisible();

  const block = page.locator("article").filter({ hasText: "Purchase order over-billed" });
  const note = block.getByLabel(/Note/);
  await expect(note).toHaveAttribute("required", "");
  await block.getByRole("button", { name: "Resolve" }).click(); // no note: the browser refuses
  await expect(block.getByRole("button", { name: "Resolve" })).toBeVisible();
  await expect(page.getByText(/open exceptions?/).first()).toBeVisible();

  await note.fill("Confirmed with purchasing: the PO was amended");
  await block.getByRole("button", { name: "Resolve" }).click();
  await expect(page.getByText("1 open exception")).toBeVisible();
  await expect(approve).toBeDisabled();

  const review = page.locator("article").filter({ hasText: "Line arithmetic" });
  await review.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText("Ready to approve")).toBeVisible();
  await expect(approve).toBeEnabled();
});

test("a correction that cannot be read is refused, and a good one is saved", async ({ page }) => {
  await page.goto(invoiceUrl);
  await page.getByRole("tab", { name: /Fields/ }).click();

  await page.getByRole("button", { name: "Correct Total" }).click();
  await page.getByLabel("New value for Total").fill("not an amount");
  await page.getByRole("button", { name: "Save and re-check" }).click();
  await expect(page.locator("form").getByRole("alert")).toContainText("could not be read");

  await page.getByLabel("New value for Total").fill("585.88");
  await page.getByRole("button", { name: "Save and re-check" }).click();
  await expect(page.getByText("Corrected by reviewer").first()).toBeVisible();
});

test("the queue and an invoice do not scroll sideways on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/queue");
  await expect(page.locator('main a[href^="/invoices/"]').first()).toBeVisible();
  expect(await scrollsSideways(page)).toBe(false);

  await page.goto(invoiceUrl);
  await page.getByRole("tab", { name: /Fields/ }).click();
  await page.getByRole("button", { name: "Correct Total" }).click();
  expect(await scrollsSideways(page)).toBe(false);
});
