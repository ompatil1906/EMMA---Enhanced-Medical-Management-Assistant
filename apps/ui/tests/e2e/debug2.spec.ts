import { test, expect } from "@playwright/test";

test("debug page title", async ({ page }) => {
  await page.goto("http://localhost:3000");
  await page.waitForTimeout(2000);
  const title = await page.title();
  console.log("PAGE TITLE:", title);
  const bodyText = await page.locator("body").textContent();
  console.log("BODY TEXT:", bodyText?.substring(0, 500));
  await page.screenshot({ path: "/tmp/debug-page.png", fullPage: true });
  console.log("Screenshot saved");
});
