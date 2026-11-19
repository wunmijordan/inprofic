const { test, expect } = require("@playwright/test");
test("promotion portions populate prices and show specific offer help through the public basket", async ({
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
  await page.goto("/retail/promotions/");
  await page.getByRole("link", { name: "Add promotion", exact: true }).click();
  const form = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await form.getByLabel("Name", { exact: true }).fill("Two packs and a single");
  await form
    .locator("[name=channels]")
    .selectOption(["online", "distribution"], { force: true });
  await form
    .locator("[name=products]")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  await form
    .locator("[name=reward_products]")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  await form
    .locator("select[name=portion_x_1]")
    .selectOption({ label: "Pack of four" }, { force: true });
  await form
    .locator("select[name=portion_y_1]")
    .selectOption({ label: "Single loaf · loaf" }, { force: true });
  await expect(
    form.getByLabel("Fixture Loaf · Online", { exact: true }),
  ).toHaveValue("1000.00");
  await form
    .getByLabel("Fixture Loaf · Online", { exact: true })
    .fill("900.00");
  await form
    .getByLabel("Fixture Loaf · Distribution", { exact: true })
    .fill("900.00");
  await form.locator("[name=mode]").selectOption("buy_get", { force: true });
  await form.locator("[name=qualifying_quantity]").fill("2");
  await form.locator("[name=free_quantity]").fill("1");
  await form.locator("[name=starts_on]").fill("2026-01-01");
  await form.locator("[name=ends_on]").fill("2027-12-31");
  await form
    .getByLabel("Pricing rule", { exact: true })
    .fill("An extra loaf for you");
  await form
    .getByLabel("Eligibility rule", { exact: true })
    .fill("Take two family packs.\nEnjoy one single loaf free.");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await page
    .locator("tbody tr")
    .filter({ hasText: "Two packs and a single" })
    .getByRole("link", { name: "Edit", exact: true })
    .click();
  await expect(form.locator("select[name=portion_x_1]")).not.toHaveValue(
    "standard",
  );
  await expect(
    form.getByLabel("Eligibility rule", { exact: true }),
  ).toHaveValue("Take two family packs.\nEnjoy one single loaf free.");
  await expect(
    form.locator("[name=tooltip_title],[name=tooltip_text]"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.goto("/shop/ui-fixture/");
  const card = page.locator("[data-product-card]").filter({
    has: page.getByRole("heading", { name: "Fixture Loaf", exact: true }),
  });
  await expect(card.locator("[data-product-offers]")).toBeVisible();
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
  await card
    .getByRole("button", { name: "Help for Two packs and a single" })
    .click();
  const tip = page.locator("body > [data-form-help-bubble]:visible");
  await expect(tip).toContainText("Take two family packs.");
  await expect(tip).toContainText("Single loaf");
  await page.keyboard.press("Escape");
  await card.locator(".add-product").click();
  await card
    .locator("[data-product-addon]")
    .selectOption(
      await card
        .locator("[data-product-addon] option")
        .filter({ hasText: "Single loaf" })
        .getAttribute("value"),
      { force: true },
    );
  await page.locator("#open-basket").click();
  await page
    .getByRole("spinbutton", { name: /Quantity for Fixture Loaf/ })
    .fill("2");
  await expect(
    page.locator('[data-checkout-mode="online"] .mode-total'),
  ).toContainText("1,890.00");
  await expect(page.locator("[data-promotion-reward]")).toHaveCount(1);
  await expect(page.locator("[data-promotion-reward]")).toContainText(
    "Free promotion reward",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "/tmp/inprofic-portion-offer-mobile.png",
    fullPage: true,
  });
});
