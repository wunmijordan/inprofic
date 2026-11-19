const { test, expect } = require("@playwright/test");
test("multiple offers retain full scrollable terms and ribbons stay visible across portions", async ({
  page,
}) => {
  test.skip(
    process.env.INPROFIC_UI_PROMOTION_PORTIONS_FIXTURE !== "1",
    "Requires selling portion fixture",
  );
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() =>
    localStorage.setItem("inprofic:essential-privacy-notice:v1", "dismissed"),
  );
  await page.goto("/accounts/login/ui-fixture/admin/");
  for (const [name, pack] of [
    ["Standard offer", false],
    ["Pack offer", true],
  ]) {
    await page.goto("/retail/promotions/");
    await page
      .getByRole("link", { name: "Add promotion", exact: true })
      .click();
    const form = page.frameLocator('iframe[name="inprofic-form-dialog"]');
    await form.getByLabel("Name", { exact: true }).fill(name);
    await form
      .locator("[name=channels]")
      .selectOption("online", { force: true });
    await form
      .locator("[name=products]")
      .selectOption({ label: "Fixture Loaf" }, { force: true });
    await form
      .locator("[name=reward_products]")
      .selectOption({ label: "Fixture Loaf" }, { force: true });
    if (pack)
      await form
        .locator("select[name=portion_x_1]")
        .selectOption({ label: "Pack of four" }, { force: true });
    await form.locator("[name=mode]").selectOption("buy_get", { force: true });
    await form.locator("[name=qualifying_quantity]").fill("2");
    await form.locator("[name=free_quantity]").fill("1");
    await form.locator("[name=starts_on]").fill("2026-01-01");
    await form.locator("[name=ends_on]").fill("2027-12-31");
    await form
      .getByLabel("Eligibility rule", { exact: true })
      .fill("Available to customers ordering the listed portions. ".repeat(10));
    await form.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  }
  await page.goto("/shop/ui-fixture/");
  const card = page
    .locator("[data-product-card]")
    .filter({
      has: page.getByRole("heading", { name: "Fixture Loaf", exact: true }),
    });
  await expect(card.locator("[data-product-offers]")).toBeVisible();
  await card
    .getByRole("button", { name: "Help for 2 offers", exact: true })
    .click();
  const tip = page.locator("body > [data-form-help-bubble]:visible");
  await expect(tip.locator("[data-help-section]")).toHaveCount(2);
  await expect(tip.locator("[data-help-section] ul")).toHaveCount(2);
  await expect(tip).toContainText("Standard offer");
  await expect(tip).toContainText("Pack offer");
  await expect(tip).toContainText("Pack of four");
  expect(
    await tip.evaluate((el) => el.scrollHeight > el.clientHeight),
  ).toBeTruthy();
  await tip.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  expect(await tip.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await card
    .locator("[data-bulk-pack-select]")
    .selectOption(
      await card
        .locator("[data-bulk-pack-select] option")
        .filter({ hasText: "Pack of four" })
        .getAttribute("value"),
      { force: true },
    );
  await expect(card.locator("[data-product-offers]")).toBeVisible();
  await expect(
    card.getByRole("button", { name: "Help for 2 offers", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await card
    .getByRole("button", { name: "Help for 2 offers", exact: true })
    .click();
  const bounds = await tip.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
  await tip.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  expect(await tip.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
});
