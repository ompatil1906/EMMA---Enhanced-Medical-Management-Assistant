import { test, expect } from "@playwright/test";

test("F invalid token debug", async ({ page }) => {
    await page.route("**/api/voice/token", async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                token: "definitely-not-a-valid-jwt",
                room: "emma-voice",
                livekit_url: "ws://127.0.0.1:7880",
            }),
        });
    });

    const errors: string[] = [];
    page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", (e) => errors.push(String(e.message ?? e)));

    await page.goto("/voice");
    await page.getByPlaceholder("Sarah").fill("Sarah");
    await page.getByPlaceholder("Jenkins").fill("Jenkins");
    await page.getByPlaceholder("7700 900077").fill("7700 900077");
    await page.getByRole("button", { name: "Call Now" }).click();

    await page.waitForTimeout(8000);
    const bodyText = await page.locator("body").textContent();
    console.log("BODY_TEXT_START:", bodyText?.substring(0, 300));
    console.log("ERRORS:", JSON.stringify(errors));
});
