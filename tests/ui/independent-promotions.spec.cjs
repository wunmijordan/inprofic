const { test, expect } = require("@playwright/test");
test("each qualifying product keeps its offer price and earns the shared reward", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() =>
    localStorage.setItem("inprofic:essential-privacy-notice:v1", "dismissed"),
  );
  await page.goto("/accounts/login/ui-fixture/admin/");
  for (const [product, price] of [
    ["Fixture Loaf", "240.00"],
    ["Fixture Cake", "400.00"],
  ]) {
    await page.goto("/retail/promotions/");
    await page
      .getByRole("link", { name: "Add promotion", exact: true })
      .click();
    const form = page.frameLocator('iframe[name="inprofic-form-dialog"]');
    await form
      .getByLabel("Name", { exact: true })
      .fill(`${product} reward offer`);
    await form
      .locator("[name=channels]")
      .selectOption("online", { force: true });
    await form
      .locator("[name=products]")
      .selectOption({ label: product }, { force: true });
    await form
      .locator("[name=reward_products]")
      .selectOption({ label: "Fixture Loaf" }, { force: true });
    await form.locator("[name=mode]").selectOption("buy_get", { force: true });
    await form.locator("[name=qualifying_quantity]").fill("1");
    await form.locator("[name=free_quantity]").fill("1");
    await form.locator("[name=starts_on]").fill("2026-01-01");
    await form.locator("[name=ends_on]").fill("2027-12-31");
    await form.getByLabel(`${product} · Online`, { exact: true }).fill(price);
    await form.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  }
  await page.goto("/shop/ui-fixture/");
  for (const product of ["Fixture Loaf", "Fixture Cake"]) {
    const card = page
      .locator("[data-product-card]")
      .filter({
        has: page.getByRole("heading", { name: product, exact: true }),
      });
    await card.locator(".add-product").click();
  }
  await page.locator("#open-basket").click();
  await page
    .getByRole("spinbutton", { name: "Quantity for Fixture Loaf", exact: true })
    .fill("2");
  await page
    .getByRole("spinbutton", { name: "Quantity for Fixture Cake", exact: true })
    .fill("3");
  await expect(
    page.locator('[data-checkout-mode="online"] .mode-total'),
  ).toContainText("1,680.00");
  await expect(page.locator("[data-promotion-reward]")).toHaveCount(2);
  const quantity = await page
    .locator("[data-promotion-reward] p")
    .evaluateAll((nodes) =>
      nodes.reduce((sum, node) => sum + parseFloat(node.textContent), 0),
    );
  expect(quantity).toBe(5);
});
