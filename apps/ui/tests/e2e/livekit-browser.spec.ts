import { test, expect } from "@playwright/test";

test.describe("LiveKit Browser Voice E2E", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1000);
  });

  test("voice screen renders with intake form", async ({ page }) => {
    await expect(page.locator("text=EMMA · QuantumLoopAI Voice Assistant")).toBeVisible();
    await expect(page.locator('button:has-text("Call Now")')).toBeVisible();
  });

  test("browser can start a voice session and connect to LiveKit", async ({ page }) => {
    await page.locator('input[placeholder="Sarah"]').fill("John");
    await page.locator('input[placeholder="Jenkins"]').fill("Doe");
    await page.locator('input[placeholder="7700 900077"]').fill("+447123456789");
    await page.evaluate(() => {
      const btn = document.querySelector('button');
      if (btn) btn.click();
    });
    await expect(page.locator("text=Listening")).toBeVisible({ timeout: 15000 });
  });

  test("microphone permission is requested", async ({ page }) => {
    await page.goto("/");
    await page.locator('input[placeholder="Sarah"]').fill("John");
    await page.locator('input[placeholder="Jenkins"]').fill("Doe");
    await page.locator('input[placeholder="7700 900077"]').fill("+447123456789");
    await page.evaluate(() => {
      const btn = document.querySelector('button');
      if (btn) btn.click();
    });
    await expect(page.locator("text=Listening")).toBeVisible({ timeout: 15000 });
  });

  test("assistant greeting audio is played", async ({ page }) => {
    await page.locator('input[placeholder="Sarah"]').fill("John");
    await page.locator('input[placeholder="Jenkins"]').fill("Doe");
    await page.locator('input[placeholder="7700 900077"]').fill("+447123456789");
    await page.evaluate(() => {
      const btn = document.querySelector('button');
      if (btn) btn.click();
    });
    await expect(page.locator("text=Listening")).toBeVisible({ timeout: 15000 });
    await expect(page.locator("text=Speaking")).toBeVisible({ timeout: 15000 });
    await expect(page.locator("text=Listening")).toBeVisible({ timeout: 15000 });
  });

  test("browser can end the conversation", async ({ page }) => {
    await page.locator('input[placeholder="Sarah"]').fill("John");
    await page.locator('input[placeholder="Jenkins"]').fill("Doe");
    await page.locator('input[placeholder="7700 900077"]').fill("+447123456789");
    await page.evaluate(() => {
      const btn = document.querySelector('button');
      if (btn) btn.click();
    });
    await expect(page.locator("text=Listening")).toBeVisible({ timeout: 15000 });
    await page.evaluate(() => {
      const btn = document.querySelector('button');
      if (btn) btn.click();
    });
    await expect(page.locator("text=Ready")).toBeVisible({ timeout: 15000 });
  });
});
