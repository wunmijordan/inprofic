const { test, expect } = require("@playwright/test");
test("offer ribbons keep card layout, show portaled terms and price POS and storefront baskets", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("inprofic:essential-privacy-notice:v1", "dismissed"),
  );
  await page.goto("/accounts/login/ui-fixture/admin/");
  await page.goto("/retail/promotions/");
  await page.getByRole("link", { name: "Add promotion", exact: true }).click();
  const form = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await form.getByLabel("Name", { exact: true }).fill("Three loaves for two");
  await form
    .locator("[name=products]")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  await form
    .locator("[name=reward_products]")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  await form.locator("[name=mode]").selectOption("buy_get", { force: true });
  await form.locator("[name=qualifying_quantity]").fill("2");
  await form.locator("[name=free_quantity]").fill("1");
  await form.locator("[name=starts_on]").fill("2026-01-01");
  await form.locator("[name=ends_on]").fill("2027-12-31");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await page.goto("/commerce/products/1/");
  await page.locator("[name=merchandising_badge]").selectOption("staff_pick");
  await page.getByRole("button", { name: "Save product", exact: true }).click();
  await page.goto("/commerce/storefront-pos/");
  const card = page.locator(".product-card").filter({
    has: page.getByRole("heading", { name: "Fixture Loaf", exact: true }),
  });
  await expect(card.locator("[data-product-offers]")).toBeVisible();
  await expect(
    card.locator("[data-merchandising-badge=staff_pick]"),
  ).toBeVisible();
  await card
    .getByRole("button", { name: "Help for Three loaves for two" })
    .click();
  const tip = page.locator("body > [data-form-help-bubble]:visible");
  await expect(tip).toContainText("Buy");
  await expect(tip.locator("li")).toHaveCount(3);
  expect(await tip.evaluate((el) => getComputedStyle(el).fontFamily)).toContain(
    "Sora",
  );
  expect(
    await tip
      .locator(".form-help-tip__heading")
      .evaluate((el) => getComputedStyle(el).fontFamily),
  ).toContain("Fraunces");
  const bounds = await tip.boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(
    page.viewportSize().width,
  );
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(
    page.viewportSize().height,
  );
  await page.keyboard.press("Escape");
  await card.locator("[data-qty]").fill("3");
  await card.locator("[data-qty]").dispatchEvent("change");
  await expect(page.locator("#cart-promotion")).toContainText("Saved ₦250.00");
  await expect(page.locator("#cart-total")).toContainText("750.00");
  await expect(page.locator("[data-promotion-reward]")).toHaveCount(1);
  await page.screenshot({
    path: "/tmp/inprofic-promotion-pos.png",
    fullPage: true,
  });
  await page.goto("/shop/ui-fixture/");
  const publicCard = page.locator("[data-product-card]").filter({
    has: page.getByRole("heading", { name: "Fixture Loaf", exact: true }),
  });
  await expect(publicCard.locator("[data-product-offers]")).toBeVisible();
  await publicCard.locator(".add-product").click();
  await page.locator("#open-basket").click();
  const qty = page.getByRole("spinbutton", {
    name: "Quantity for Fixture Loaf",
  });
  await qty.fill("3");
  await expect(page.locator("#basket-promotion")).toContainText(
    "Saved ₦300.00",
  );
  await expect(
    page.locator('[data-checkout-mode="online"] .mode-total'),
  ).toContainText("900.00");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.screenshot({
    path: "/tmp/inprofic-promotion-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await publicCard
    .getByRole("button", { name: "Help for Three loaves for two" })
    .click();
  const mobileTip = page.locator("body > [data-form-help-bubble]:visible");
  await expect(mobileTip.locator("li")).toHaveCount(3);
  const mobileBounds = await mobileTip.boundingBox();
  expect(mobileBounds.x).toBeGreaterThanOrEqual(0);
  expect(mobileBounds.y).toBeGreaterThanOrEqual(0);
  expect(mobileBounds.x + mobileBounds.width).toBeLessThanOrEqual(390);
  expect(mobileBounds.y + mobileBounds.height).toBeLessThanOrEqual(844);
  await page.screenshot({
    path: "/tmp/inprofic-promotion-help-mobile.png",
    fullPage: true,
  });
});

test("multi-channel promotion prices populate per selected product and public dialogs remain opaque", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() =>
    localStorage.setItem("inprofic:essential-privacy-notice:v1", "dismissed"),
  );
  await page.goto("/accounts/login/ui-fixture/admin/");
  await page.goto("/retail/promotions/");
  await page.getByRole("link", { name: "Add promotion", exact: true }).click();
  const form = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await form
    .getByLabel("Name", { exact: true })
    .fill("Two channel price offer");
  await form.locator("[name=mode]").selectOption("buy_get", { force: true });
  await form
    .locator("[name=channels]")
    .selectOption(["physical_store", "online"], { force: true });
  await form
    .locator("[name=products]")
    .selectOption([{ label: "Fixture Loaf" }, { label: "Fixture Cake" }], {
      force: true,
    });
  await form
    .locator("[name=reward_products]")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  await expect(
    form.getByLabel("Fixture Loaf · Online", { exact: true }),
  ).toHaveValue("300.00");
  await expect(
    form.getByLabel("Fixture Loaf · Physical Store", { exact: true }),
  ).toHaveValue("250.00");
  await form
    .getByLabel("Fixture Loaf · Online", { exact: true })
    .fill("240.00");
  await form
    .getByLabel("Fixture Loaf · Physical Store", { exact: true })
    .fill("200.00");
  await form
    .getByLabel("Fixture Cake · Online", { exact: true })
    .fill("400.00");
  await form
    .getByLabel("Fixture Cake · Physical Store", { exact: true })
    .fill("450.00");
  await form.locator("[name=starts_on]").fill("2026-01-01");
  await form.locator("[name=ends_on]").fill("2027-12-31");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  const row = page
    .locator("tbody tr")
    .filter({ hasText: "Two channel price offer" });
  await expect(row).toContainText("Physical Store, Online");
  await page.goto("/commerce/storefront-pos/");
  const card = page
    .locator(".product-card")
    .filter({
      has: page.getByRole("heading", { name: "Fixture Loaf", exact: true }),
    });
  await expect(card.locator("[data-pos-price-label]")).toContainText("200.00");
  await card.locator("[data-qty]").fill("3");
  await card.locator("[data-qty]").dispatchEvent("change");
  await expect(page.locator("#cart-total")).toContainText("600.00");
  await page.goto("/shop/ui-fixture/");
  const publicCard = page
    .locator("[data-product-card]")
    .filter({
      has: page.getByRole("heading", { name: "Fixture Loaf", exact: true }),
    });
  await expect(publicCard).toHaveAttribute("data-price-online", "240.00");
  await publicCard.locator(".add-product").click();
  await page.locator("#open-basket").click();
  const dialog = page.locator(".ui-legacy-dialog");
  await expect(dialog).toBeVisible();
  const surface = await dialog.evaluate((el) => ({
    background: getComputedStyle(el).backgroundColor,
    opacity: getComputedStyle(el).opacity,
  }));
  expect(surface.background).not.toBe("rgba(0, 0, 0, 0)");
  expect(surface.opacity).toBe("1");
  await page
    .getByRole("spinbutton", { name: "Quantity for Fixture Loaf" })
    .fill("3");
  await expect(
    page.locator("[data-checkout-mode=online] .mode-total"),
  ).toContainText("720.00");
});
