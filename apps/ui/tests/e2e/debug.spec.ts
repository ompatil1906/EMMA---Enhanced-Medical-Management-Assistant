import { test, expect } from "@playwright/test";

test("debug: trigger click via evaluate", async ({ page }) => {
  await page.goto("/");
  await page.waitForTimeout(1000);
  
  await page.locator('input[placeholder="Sarah"]').fill("John");
  await page.locator('input[placeholder="Jenkins"]').fill("Doe");
  await page.locator('input[placeholder="7700 900077"]').fill("+447123456789");
  
  // Try clicking via evaluate
  await page.evaluate(() => {
    const btn = document.querySelector('button');
    if (btn) {
      console.log("Button found:", btn.textContent, btn.className);
      btn.click();
    } else {
      console.log("Button not found");
    }
  });
  
  await page.waitForTimeout(2000);
  
  const bodyText = await page.locator("body").textContent();
  console.log("BODY AFTER CLICK:", bodyText?.substring(0, 500));
  
  await page.screenshot({ path: "/tmp/debug-click.png", fullPage: true });
});
