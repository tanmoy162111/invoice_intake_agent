import path from "node:path";

import { expect, test } from "@playwright/test";

import { UPLOAD_FILE, UPLOAD_NUMBER, signIn } from "./helpers";

// The M8 acceptance test: upload -> review -> approve, through the browser, on recorded model answers.
test("upload an invoice, see it read and checked, and approve it", async ({ page }) => {
  expect(UPLOAD_FILE, "E2E_UPLOAD_FILE must point at the held-out seed invoice").not.toBe("");
  await signIn(page);

  await page.goto("/upload");
  await page.locator("#file").setInputFiles(path.resolve(UPLOAD_FILE));
  await expect(page.getByTestId("chosen-file")).toContainText(path.basename(UPLOAD_FILE));
  await page.getByRole("button", { name: "Upload and check" }).click();
  const result = page.getByTestId("upload-result");
  await expect(result).toContainText("Uploaded");

  // The worker reads it (recorded answer), checks it and routes it. Follow the link the upload gave.
  await result.getByRole("link").click();
  await expect(page).toHaveURL(/\/invoices\//);
  await expect(async () => {
    await page.reload();
    await expect(page.getByText(UPLOAD_NUMBER).first()).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 90_000, intervals: [2_000] });

  // The system is not sure about this one (a similar earlier invoice may be the same supplier), so it
  // goes to a person: close what is open, and approval unlocks.
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
  for (let open = await page.getByRole("button", { name: "Dismiss" }).count(); open > 0; open--) {
    const card = page.locator("article").filter({ has: page.getByRole("button", { name: "Dismiss" }) }).first();
    await card.getByLabel(/Note/).fill("Different supplier: the address and bank details differ");
    await card.getByRole("button", { name: "Dismiss" }).click();
    await expect(page.getByRole("button", { name: "Dismiss" })).toHaveCount(open - 1);
  }
  await expect(page.getByText("Ready to approve")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByLabel(/Note/).fill("Checked against the PO");
  await page.getByRole("button", { name: "Confirm approval" }).click();

  await expect(page.getByText("This decision is final and recorded.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reject" })).toHaveCount(0);
});
