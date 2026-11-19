const { test, expect } = require("@playwright/test");
test.use({ serviceWorkers: "allow" });
test.setTimeout(180000);
async function openOfflinePage(page, name) {
  const link = page.getByRole("link", { name, exact: true });
  if (!(await link.isVisible())) {
    const toggle = page.getByRole("button", {
      name: "Toggle navigation",
      exact: true,
    });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  }
  await link.click();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("inprofic:essential-privacy-notice:v1", "dismissed"),
  );
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/accounts/login/ui-fixture/admin/");
});
test("branch creation and profile forms use the shared modal and global field styles", async ({
  page,
}) => {
  await page.goto("/retail/");
  await page.getByRole("link", { name: "Add branch", exact: true }).click();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame
    .getByLabel("Branch manager", { exact: true })
    .selectOption({ label: "UI Admin" }, { force: true });
  await frame.getByLabel("Name", { exact: true }).fill("Island branch");
  await frame.getByLabel("Code", { exact: true }).fill("island");
  await frame.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await expect(page.locator("main")).toContainText("Island branch");
  await page
    .locator("main")
    .getByRole("link", { name: "My profile", exact: true })
    .click();
  await expect(page).toHaveURL(/\/retail\/profile\/$/);
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await page
    .locator('input[name="photo"]')
    .setInputFiles({
      name: "avatar.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=",
        "base64",
      ),
    });
  await expect(page.locator("[data-profile-photo]")).toBeVisible();
  await expect(page.locator("[data-profile-placeholder]")).toBeHidden();
  await expect(page.getByLabel("Full name", { exact: true })).toHaveValue(
    "UI Admin",
  );
});
test("product promotions validate dates and retain errors inside their modal", async ({
  page,
}) => {
  await page.goto("/retail/promotions/");
  await page.getByRole("link", { name: "Add promotion", exact: true }).click();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await expect(frame.getByLabel("Name", { exact: true })).toBeVisible();
  await expect(frame.locator("input[name=active]").locator("..")).toHaveClass(
    /cf-switch/,
  );
  await frame.getByLabel("Name", { exact: true }).fill("Weekend mix");
  await frame
    .locator("select[name=products]")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  await frame
    .locator("select[name=mode]")
    .selectOption("mix_match", { force: true });
  await frame.locator("input[name=starts_on]").fill("2026-11-18");
  await frame.locator("input[name=ends_on]").fill("2026-11-17");
  await frame.getByRole("button", { name: "Save", exact: true }).click();
  await expect(frame.locator("main")).toContainText("End date must follow");
  await expect(frame.locator("main")).toContainText(
    "Enter a positive bundle price",
  );
});
test("offline expiry flags wait for explicit sync confirmation", async ({
  page,
  context,
}) => {
  await page.goto("/retail/profile/");
  await expect(page.locator("[data-connection-dialog]")).toHaveCount(0);
  await page
    .getByRole("link", { name: "Prepare offline access", exact: true })
    .click();
  await page
    .getByLabel("Offline passphrase", { exact: true })
    .fill("test-offline-passphrase");
  await page
    .getByRole("button", { name: "Prepare / refresh packages", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Packages prepared." }),
  ).toBeVisible();
  await page.locator("[data-offline-toggle]").check({ force: true });
  await page.waitForURL("**/pwa/offline/");
  await page.reload();
  await page
    .getByLabel("Offline passphrase", { exact: true })
    .fill("test-offline-passphrase");
  await page
    .getByRole("button", { name: "Unlock saved packages", exact: true })
    .click();
  await expect(page.locator("[data-offline-workspace]")).toContainText(
    "Offline mode",
  );
  await expect(page.locator(".desktop-sidebar")).toHaveCount(1);
  await expect(
    page.locator("[data-offline-package]").locator("xpath=ancestor::details"),
  ).toHaveCount(0);
  await openOfflinePage(page, "Inventory");
  await expect(page.locator("#raw-panel")).toBeVisible();
  await expect(page.locator("#finished-panel")).toBeVisible();
  await expect(page.getByText("Import products", { exact: true })).toHaveCount(
    0,
  );
  await openOfflinePage(page, "Expiration check");
  await context.setOffline(true);
  await page
    .getByRole("button", { name: /flag for review/i })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Pending entries (1)", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Review & confirm sync" }),
  ).toBeDisabled();
  await context.setOffline(false);
  await expect(
    page.getByRole("heading", { name: "Pending entries (1)", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Review & confirm sync" }).click();
  await page.getByRole("button", { name: "Confirm sync", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Pending entries (0)", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await page.goto("/retail/expiration/");
  await expect(page.locator("main")).toContainText(
    "Flagged for expiry review offline",
  );
});
test("install prompt uses the same switch component", async ({ page }) => {
  await expect(
    page.locator("[data-pwa-never]").first().locator(".."),
  ).toHaveClass(/cf-switch/);
});
test("offline cash POS and dispatch survive reconnect until confirmed sync", async ({
  page,
  context,
}) => {
  page.on("pageerror", (error) =>
    console.log("Offline browser error:", error.message),
  );
  test.skip(
    process.env.INPROFIC_UI_RETAIL_FIXTURE !== "1",
    "Requires the retail stock and dispatch fixture.",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/retail/profile/");
  await page
    .getByRole("link", { name: "Prepare offline access", exact: true })
    .click();
  await page
    .getByLabel("Offline passphrase", { exact: true })
    .fill("offline-sales-test");
  await page
    .getByRole("button", { name: "Prepare / refresh packages", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Packages prepared." }),
  ).toBeVisible();
  await page.locator("[data-offline-toggle]").check({ force: true });
  await page.waitForURL("**/pwa/offline/");
  await page.reload();
  await page
    .getByLabel("Offline passphrase", { exact: true })
    .fill("offline-sales-test");
  await page
    .getByRole("button", { name: "Unlock saved packages", exact: true })
    .click();
  await expect(page.locator(".desktop-sidebar")).toHaveCount(1);
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => true));
  await context.setOffline(true);
  await openOfflinePage(page, "POS");
  const pos = page.frameLocator('iframe[title="Offline POS"]');
  await pos.locator('[name="customer_name"]').fill("Offline cash customer");
  await pos.getByLabel("Quantity for Fixture Loaf", { exact: true }).fill("2");
  await pos.locator('[name="manual_payment_received"]').check({ force: true });
  await pos.locator("#checkout-button").click();
  await pos.locator("#pos-review-confirm").click();
  await expect(pos.locator("#pos-receipt-id")).toContainText("Pending sync");
  await expect(pos.locator("#pos-receipt-total")).toContainText("500");
  await pos.locator("#pos-receipt-done").click();
  await expect(
    pos.getByLabel("Quantity for Fixture Loaf", { exact: true }),
  ).toHaveAttribute("max", "28.00");
  await openOfflinePage(page, "Delivery");
  const rider = page.getByRole("combobox", { name: /Dispatch rider for/ });
  await rider.click();
  await page.getByRole("option", { name: /Fixture rider/ }).click();
  await page
    .getByRole("button", { name: "Save dispatch assignment", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Pending entries (2)", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Review & confirm sync", exact: true }),
  ).toBeDisabled();
  await page.reload();
  await page
    .getByLabel("Offline passphrase", { exact: true })
    .fill("offline-sales-test");
  await page
    .getByRole("button", { name: "Unlock saved packages", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Pending entries (2)", exact: true }),
  ).toBeVisible();
  await context.setOffline(false);
  await expect(
    page.getByRole("heading", { name: "Pending entries (2)", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Review & confirm sync", exact: true })
    .click();
  await page.getByRole("button", { name: "Confirm sync", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Pending entries (0)", exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await openOfflinePage(page, "Offline receipts");
  const receipt = page.locator("#offline-receipt");
  await page.getByRole("button", { name: /Offline cash customer ·/ }).click();
  await expect(receipt).toContainText("Synced · Receipt");
  await receipt.getByRole("button", { name: "Close", exact: true }).click();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  expect(overflow).toBe(false);
});
test("connection loss automatically shows the existing offline card without a sidebar button", async ({
  page,
  context,
}) => {
  await expect(page.locator("[data-connection-dialog]")).toHaveCount(0);
  await page.evaluate(() => localStorage.setItem("inprofic-theme", "dark"));
  await page.reload();
  await context.setOffline(true);
  const dialog = page.locator("#connection-availability-dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("heading", { name: "INPROFIC is offline" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Try again", exact: true }),
  ).toBeVisible();
  await expect(
    dialog.locator("[data-offline-toggle]").locator(".."),
  ).toHaveClass(/cf-switch/);
  await expect(dialog.locator("[data-offline-access]")).toBeHidden();
  expect(
    await dialog
      .getByRole("button", { name: "Try again", exact: true })
      .evaluate((node) => node.getBoundingClientRect().height),
  ).toBeGreaterThanOrEqual(36);
  const logo = dialog.locator(".connection-logo-dark");
  await expect(logo).toBeVisible();
  expect(
    await logo.evaluate((node) => node.complete && node.naturalWidth > 0),
  ).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await dialog.evaluate((node) => node.getBoundingClientRect().width),
  ).toBeLessThanOrEqual(358);
  await page.screenshot({
    path: "test-results/connection-dialog-dark-mobile.png",
  });
  await context.setOffline(false);
  await dialog.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
test("an unreachable server shows the same offline dialog even when the browser reports online", async ({
  page,
}) => {
  await page.route("**/health/", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: '{"status":"unavailable"}',
    }),
  );
  await page.reload();
  const dialog = page.locator("#connection-availability-dialog");
  await expect(
    dialog.getByRole("heading", { name: "INPROFIC is offline" }),
  ).toBeVisible();
  await page.unroute("**/health/");
  await dialog.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
test("offline package actions stay visible when no packages are saved", async ({
  page,
  context,
}) => {
  await page.goto("/pwa/offline/");
  const dialog = page.locator("#connection-availability-dialog");
  await context.setOffline(true);
  await dialog
    .getByLabel("Offline passphrase", { exact: true })
    .fill("test-passphrase");
  await expect(
    dialog.getByRole("button", { name: "Unlock saved packages", exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Unlock saved packages", exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByRole("button", {
      name: "Prepare / refresh packages",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", {
      name: "Prepare / refresh packages",
      exact: true,
    }),
  ).toBeDisabled();
  await expect(dialog).toContainText(
    "No offline packages are saved on this device yet.",
  );
});
test("preparation immediately exposes open action and passphrase Enter unlock survives a wrong attempt", async ({
  page,
  context,
}) => {
  await page.goto("/pwa/offline/?prepare=1");
  const dialog = page.locator("#connection-availability-dialog");
  const pass = dialog.getByLabel("Offline passphrase", { exact: true });
  await pass.fill("saved-package-passphrase");
  await pass.press("Enter");
  const open = dialog.getByRole("button", {
    name: "Open offline workspace",
    exact: true,
  });
  await expect(open).toBeVisible();
  await expect(open).toBeEnabled();
  await open.click();
  await expect(page.locator(".desktop-sidebar")).toHaveCount(1);
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => true));
  await context.setOffline(true);
  await page.reload();
  await pass.fill("wrong-passphrase");
  await pass.press("Enter");
  await expect(dialog.getByRole("status")).toContainText(
    "Could not unlock. Check the passphrase.",
  );
  await expect(
    dialog.getByRole("button", { name: "Unlock saved packages", exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Unlock saved packages", exact: true }),
  ).toBeEnabled();
  await expect(
    dialog.getByRole("button", {
      name: "Prepare / refresh packages",
      exact: true,
    }),
  ).toBeVisible();
  await pass.fill("saved-package-passphrase");
  await pass.press("Enter");
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".desktop-sidebar")).toHaveCount(1);
});
test("quotation products can be added and deleted, then share the storefront document layout", async ({
  page,
}) => {
  test.skip(process.env.INPROFIC_UI_RETAIL_FIXTURE !== "1");
  await page.goto("/retail/quotations/");
  await page
    .getByRole("link", { name: "Create quotation", exact: true })
    .click();
  const frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame
    .getByLabel("Title", { exact: true })
    .fill("Customer supply request");
  await frame
    .getByLabel("Customer name", { exact: true })
    .fill("Quotation UI customer");
  await frame.locator('input[name="valid_until"]').fill("2026-12-31");
  await frame
    .locator('select[name="channel"]')
    .selectOption("online", { force: true });
  const rows = frame.locator("[data-quote-line]");
  await expect(rows).toHaveCount(1);
  await rows
    .nth(0)
    .locator("select")
    .selectOption({ label: "Fixture Loaf" }, { force: true });
  const price = rows.nth(0).locator('input[name$="-unit_price"]');
  await expect(price).toHaveValue("300.00");
  await frame
    .locator('select[name="channel"]')
    .selectOption("distribution", { force: true });
  await expect(price).toHaveValue("250.00");
  await price.fill("240");
  await expect(rows.nth(0).locator("[data-quote-price-note]")).toContainText(
    "recorded internally",
  );
  await rows.nth(0).locator('input[name$="-quantity"]').fill("2");
  await rows.nth(0).locator('input[name$="-unit_price"]').fill("250");
  await frame
    .getByRole("button", { name: "Add new product", exact: true })
    .click();
  await expect(rows).toHaveCount(2);
  await rows
    .nth(1)
    .getByRole("button", { name: "Delete product", exact: true })
    .click();
  await expect(rows.nth(1)).toBeHidden();
  await frame
    .getByRole("button", { name: "Add new product", exact: true })
    .click();
  await rows
    .nth(2)
    .locator("select")
    .selectOption({ label: "Fixture Cake" }, { force: true });
  await rows.nth(2).locator('input[name$="-quantity"]').fill("1");
  await rows.nth(2).locator('input[name$="-unit_price"]').fill("500");
  await frame
    .getByRole("button", { name: "Save quotation", exact: true })
    .click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await page.locator("tbody a").last().click();
  await expect(page.locator(".receipt-card")).toContainText(
    "UI Fixture Bakery",
  );
  await expect(page.locator(".receipt-card")).toContainText("Fixture Cake");
  await expect(page.locator(".receipt-card")).toContainText(
    "Customer supply request",
  );
  await page
    .getByRole("link", { name: "Open customer acceptance link", exact: true })
    .click();
  await expect(page.locator(".receipt-card")).toBeVisible();
  await expect(page.locator(".receipt-card")).not.toContainText(
    "Payment confirmed",
  );
});
test("new retail tables keep the shared rounded wrapper and full available workspace width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 2000, height: 1000 });
  await page.goto("/retail/");
  const wrapper = page.locator(".workspace-table").first();
  expect(
    await wrapper.evaluate((node) =>
      parseFloat(getComputedStyle(node).borderRadius),
    ),
  ).toBeGreaterThan(0);
  expect(
    await wrapper
      .locator("th")
      .first()
      .evaluate((node) => parseFloat(getComputedStyle(node).paddingLeft)),
  ).toBeGreaterThanOrEqual(12);
  await expect(
    page.locator('.desktop-sidebar-nav a[aria-current="page"]'),
  ).toContainText("Branches & cashiers");
  const expanded = await page
    .locator(".workspace-content")
    .evaluate((node) => node.getBoundingClientRect().width);
  expect(expanded).toBeGreaterThan(1600);
  await page
    .getByRole("button", { name: "Collapse navigation", exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute(
    "data-sidebar",
    "collapsed",
  );
  await expect
    .poll(async () =>
      page.locator(".workspace-content").evaluate((node) => {
        const r = node.getBoundingClientRect();
        return Math.abs((r.left + r.right) / 2 - (innerWidth + 72) / 2);
      }),
    )
    .toBeLessThan(2);
  expect(
    await page
      .locator(".workspace-content")
      .evaluate((node) => node.getBoundingClientRect().width),
  ).toBeLessThanOrEqual(1536);
});
test("Founder policy editor opens wide without automatically showing help tips", async ({
  page,
}) => {
  test.skip(process.env.INPROFIC_UI_FOUNDER_FIXTURE !== "1");
  await page.goto("about:blank");
  await page.context().clearCookies();
  await page.goto("/accounts/login/ui-fixture/founder/");
  await page.goto("/users/founder/subscriptions/");
  await page
    .getByRole("button", { name: "Platform management", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Publish policy", exact: true })
    .click();
  const dialog = page.locator(".ui-legacy-dialog");
  await expect(dialog).toHaveAttribute("data-dialog-size", "wide");
  await expect(dialog.locator('[role="tooltip"]:visible')).toHaveCount(0);
  expect(
    await dialog.evaluate((node) => node.getBoundingClientRect().width),
  ).toBeGreaterThan(900);
  const trigger = dialog.locator("[data-form-help-trigger]").first();
  await trigger.hover();
  const tip = page.locator('[role="tooltip"]:visible').first();
  await expect(tip).toBeVisible();
  expect(
    await tip.evaluate((node) => node.parentElement === document.body),
  ).toBe(true);
  const bounds = await tip.evaluate((node) => {
    const r = node.getBoundingClientRect();
    return { left: r.left, right: r.right, width: innerWidth };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(bounds.width);
});

test("parked baskets use the shared modal, preserve active baskets and restore saved products", async ({
  page,
}) => {
  test.skip(process.env.INPROFIC_UI_RETAIL_FIXTURE !== "1");
  await page.goto("/commerce/storefront-pos/");
  const first = page.locator("[data-qty]:visible:not(:disabled)").first();
  await first.fill("2");
  await first.dispatchEvent("input");
  await page.locator('[name="customer_name"]').fill("Parked customer");
  await page.getByRole("button", { name: "Park basket", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Basket parked." }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Parked baskets", exact: true })
    .click();
  const dialog = page.locator(".ui-legacy-dialog");
  await expect(
    dialog.getByRole("heading", { name: "Parked baskets", exact: true }),
  ).toBeVisible();
  await expect(dialog.locator(".workspace-table")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Close", exact: true }),
  ).toHaveCount(1);
  await dialog
    .getByRole("button", { name: "Restore Parked customer", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(first).toHaveValue("2");
  await expect(page.locator('[name="customer_name"]')).toHaveValue(
    "Parked customer",
  );
  await page.setViewportSize({width:390,height:844});
  await page.getByRole("button", {name:"Park basket", exact:true}).click();
  await expect(page.getByRole("status").filter({hasText:"Basket parked."})).toBeVisible();
  await page.getByRole("button", {name:"Parked baskets", exact:true}).click();
  await expect(dialog).toBeVisible();
  expect((await dialog.boundingBox()).width).toBeLessThanOrEqual(390);
  await expect(dialog.getByRole("button",{name:"Close",exact:true})).toHaveCount(1);
  await dialog.getByRole("button",{name:"Close",exact:true}).click();
});

test("business vocabulary applies to desktop navigation and workspace headings", async ({
  page,
}) => {
  await page.goto("/business/settings/");
  await page.getByText("Module & menu names", { exact: true }).click();
  await page.locator('[name="label_inventory"]').fill("Stock room");
  await page
    .getByRole("button", { name: "Save preferences", exact: true })
    .click();
  await expect(
    page.locator('.desktop-sidebar-nav a[href="/inventory/"] .nav-label'),
  ).toHaveText("Stock room");
  await page.goto("/inventory/");
  await expect(
    page.locator('.desktop-sidebar-nav a[href="/inventory/"] .nav-label'),
  ).toHaveText("Stock room");
});

test("cashier drawer opens from POS, closes with a count and receives manager review", async ({
  page,
}) => {
  await page.goto("/commerce/storefront-pos/");
  await page
    .getByRole("link", { name: "Open cashier session", exact: true })
    .click();
  let frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame.getByLabel("Opening float", { exact: true }).fill("500");
  await frame.getByRole("button", { name: "Open drawer", exact: true }).click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await page.goto("/commerce/storefront-pos/");
  await page.getByRole("link", { name: "View drawer", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /Cashier session #/ }),
  ).toBeVisible();
  await expect(
    page.getByText("Expected in drawer", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Count & close", exact: true }).click();
  frame = page.frameLocator('iframe[name="inprofic-form-dialog"]');
  await frame.getByLabel("Cash counted", { exact: true }).fill("500");
  await frame
    .getByRole("button", { name: "Record count & close", exact: true })
    .click();
  await expect(page.locator(".ui-form-dialog")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Mark reviewed", exact: true })
    .click();
  await expect(page.locator("main")).toContainText("Reviewed by UI Admin");
});
