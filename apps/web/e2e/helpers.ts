import { expect, type Page } from "@playwright/test";

export const REVIEWER = process.env.E2E_REVIEWER_USERNAME ?? "reviewer";
export const PASSWORD = process.env.E2E_REVIEWER_PASSWORD ?? "e2e-reviewer-pass";

/** The seed invoice the stack holds back so the test can upload it (see HOLD_OUT in scripts/e2e.sh). */
export const UPLOAD_FILE = process.env.E2E_UPLOAD_FILE ?? "";
export const UPLOAD_NUMBER = "BL-2026-0540";

/** A seeded invoice with a blocking exception and a review exception (purchase order over-billed). */
export const BLOCKED_NUMBER = "AFS-2026-0749";

export async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(REVIEWER);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/queue/);
}

/** The exception that puts BLOCKED_NUMBER in the queue: the filter finds it however the queue is ordered. */
export const BLOCKED_CODE = "PO_OVERBILLED";

/** Opens a seeded invoice from the queue, filtered to one exception, by its number. */
export async function openFromQueue(page: Page, number: string, code: string) {
  await page.goto(`/queue?code=${code}`);
  // the row's first link (the supplier) opens the invoice; the number is text beside it
  await page.getByRole("listitem").filter({ hasText: number }).getByRole("link").first().click();
  await expect(page).toHaveURL(/\/invoices\//);
}

/** True when the page scrolls sideways. */
export async function scrollsSideways(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
}
