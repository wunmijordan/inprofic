const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync(
  "apps/core/static/core/js/offline-workspace.js",
  "utf8",
);
const helpers = source.slice(
  source.indexOf("  const units ="),
  source.indexOf("  async function queue("),
);
const pricing = source.slice(
  source.indexOf("  function offlinePromotion("),
  source.indexOf("  if (shell)\n    window.INPROFICOfflinePOS"),
);
function calculate(
  items,
  offers,
  products = items.map((row) => ({
    ...row,
    stock: "1000.00",
    name: row.id,
    unit: "unit",
  })),
) {
  const context = {
    state: {
      snapshot: { timezone: "Africa/Lagos", promotions: offers, products },
    },
  };
  vm.createContext(context);
  vm.runInContext(
    `${helpers}\n${pricing}\nglobalThis.calculate = offlinePromotion;`,
    context,
  );
  return context.calculate(items);
}
const row = (id, quantity, price) => ({ id, quantity, price });
const offer = (overrides = {}) => ({
  id: 1,
  name: "Offer",
  kind: "buy_get",
  product_ids: ["x"],
  reward_product_ids: ["x"],
  qualifying_quantity: 2,
  free_quantity: 1,
  starts_on: "2020-01-01",
  ends_on: "2099-01-01",
  ...overrides,
});
test("buy/get preserves quantities and gives one of three units free", () => {
  const result = calculate([row("x", "3", "2.50")], [offer()]);
  assert.equal(result.total, 750n);
  assert.equal(result.promotion.saving, "2.50");
});
test("separate reward products must be present and cheapest reward is allocated first", () => {
  const campaign = offer({ reward_product_ids: ["y", "z"] });
  assert.equal(calculate([row("x", "2", "10")], [campaign]).total, 2000n);
  const result = calculate(
    [row("x", "2", "10"), row("y", "2", "3"), row("z", "1", "2")],
    [campaign],
  );
  assert.equal(result.total, 2800n);
  assert.equal(result.lines[2], 200n);
  assert.equal(result.promotion.rewards[0].id, "z");
});
test("overlapping qualifying/reward sets never count free units as purchased units", () => {
  const result = calculate([row("x", "6", "10")], [offer()]);
  assert.equal(result.total, 6000n);
  assert.equal(result.promotion.rewards[0].quantity, "3.00");
});
test("bundle allocation preserves exact cents and best offer wins without stacking", () => {
  const result = calculate(
    [row("x", "1", "1"), row("y", "2", "1")],
    [
      offer(),
      offer({
        id: 2,
        kind: "mix_match",
        product_ids: ["x", "y"],
        qualifying_quantity: 3,
        bundle_price: "2.00",
      }),
    ],
  );
  assert.equal(result.total, 200n);
  assert.equal(result.lines[0] + result.lines[1], 200n);
  assert.equal(result.promotion.id, 2);
});
test("fractional units and expired campaigns receive no discount", () => {
  assert.equal(calculate([row("x", "3.5", "2")], [offer()]).total, 700n);
  assert.equal(
    calculate([row("x", "3", "2")], [offer({ ends_on: "2020-01-02" })]).total,
    600n,
  );
});
test("offline product price overrides apply per product and channel before rewards", () => {
  const result = calculate(
    [row("x", "3", "2.50")],
    [offer({ unit_prices: { x: { physical_store: "2.00", online: "1.00" } } })],
  );
  assert.equal(result.total, 600n);
  assert.equal(result.prices[0], "2.00");
});
test("fractional scheduled unit prices apply without qualifying for whole-unit rewards", () => {
  const result = calculate(
    [row("x", "0.5", "2.50")],
    [offer({ unit_prices: { x: { physical_store: "2.00" } } })],
  );
  assert.equal(result.total, 100n);
});
test("portion-specific offers never discount ordinary offline units", () => {
  const result = calculate(
    [row("x", "3", "2.50")],
    [
      offer({
        product_portions: { x: "bulk:pack-id" },
        reward_portions: { x: "individual:unit-id" },
        unit_prices: { x: { physical_store: "1.00" } },
      }),
    ],
  );
  assert.equal(result.total, 750n);
  assert.equal(Object.keys(result.promotion).length, 0);
});
test("standard offline qualifiers cannot receive a reward in an unselected portion", () => {
  const result = calculate(
    [row("x", "3", "2.50")],
    [
      offer({
        product_portions: { x: "standard" },
        reward_portions: { x: "individual:unit-id" },
      }),
    ],
  );
  assert.equal(result.total, 750n);
});

test("automatic rewards are separate from billed customer quantities and respect combined stock", () => {
  const campaign = offer({ mode: "buy_get", reward_product_ids: ["y"] });
  const products = [
    { id: "x", stock: "10", price: "10", name: "X", unit: "pack" },
    { id: "y", stock: "2", price: "3", name: "Y", unit: "unit" },
  ];
  const result = calculate(
    [row("x", "2", "10"), row("y", "1", "3")],
    [campaign],
    products,
  );
  assert.equal(result.total, 2300n);
  assert.equal(result.promotion.rewards[0].quantity, "1.00");
  assert.equal(result.promotion.rewards[0].unit_price, "0.00");
  products[1].stock = "1";
  const unavailable = calculate(
    [row("x", "2", "10"), row("y", "1", "3")],
    [campaign],
    products,
  );
  assert.equal(Object.keys(unavailable.promotion).length, 0);
});
test("independent qualifying products use their own promotion prices and all earn a shared reward", () => {
  const offers = [
    offer({
      id: 1,
      qualifying_quantity: 1,
      reward_product_ids: ["y"],
      unit_prices: { x: { physical_store: "8.00" } },
    }),
    offer({
      id: 2,
      qualifying_quantity: 1,
      product_ids: ["z"],
      reward_product_ids: ["y"],
      unit_prices: { z: { physical_store: "16.00" } },
    }),
  ];
  const products = [
    { id: "x", name: "X", price: "10", stock: "20", unit: "unit" },
    { id: "z", name: "Z", price: "20", stock: "20", unit: "unit" },
    { id: "y", name: "Y", price: "1", stock: "20", unit: "unit" },
  ];
  const result = calculate(
    [row("x", "2", "10"), row("z", "3", "20")],
    offers,
    products,
  );
  assert.equal(result.total, 6400n);
  assert.equal(result.prices[0], "8.00");
  assert.equal(result.prices[1], "16.00");
  assert.equal(
    result.promotion.rewards.reduce(
      (sum, row) => sum + Number(row.quantity),
      0,
    ),
    5,
  );
  products[2].stock = "3";
  const limited = calculate(
    [row("x", "2", "10"), row("z", "3", "20")],
    offers,
    products,
  );
  assert.equal(
    limited.promotion.rewards.reduce(
      (sum, row) => sum + Number(row.quantity),
      0,
    ),
    3,
  );
  assert.equal(limited.total, 6400n);
});
test("overlapping offers cannot reuse the same paid quantities for extra rewards", () => {
  const result = calculate(
    [row("x", "2", "10")],
    [
      offer({ id: 1, qualifying_quantity: 1 }),
      offer({ id: 2, qualifying_quantity: 2 }),
    ],
  );
  assert.equal(
    result.promotion.rewards.reduce(
      (sum, row) => sum + Number(row.quantity),
      0,
    ),
    2,
  );
});
test("equal-price offline rewards use the same registered order as live checkout", () => {
  const products=[{id:"x",price:"10",stock:"10",promotion_order:3},{id:"a",price:"1",stock:"10",promotion_order:2},{id:"z",price:"1",stock:"10",promotion_order:1}];
  const result=calculate([row("x","1","10")],[offer({qualifying_quantity:1,reward_product_ids:["a","z"]})],products);
  assert.equal(result.promotion.rewards[0].product_id,"z");
});
