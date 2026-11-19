(() => {
  const form = document.querySelector("[data-promotion-form]");
  if (!form) return;
  const catalog = JSON.parse(
    document.getElementById("promotion-price-catalog").textContent,
  );
  const products = form.querySelector("[name=products]"),
    channels = form.querySelector("[name=channels]"),
    rewards = form.querySelector("[name=reward_products]");
  const portionTarget = form.querySelector("[data-promotion-portions]");
  const target = form.querySelector("[data-promotion-prices]");
  const labels = {
    physical_store: "Physical Store",
    online: "Online",
    distribution: "Distribution",
  };
  const saved = new Map();
  function update() {
    const selected = [...products.selectedOptions].map((o) => o.value);
    let codes = [...channels.selectedOptions].map((o) => o.value);
    if (!codes.length || codes.includes("all")) codes = Object.keys(labels);
    const portionNames = new Set();
    for (const [role, control] of [
      ["x", products],
      ["y", rewards],
    ]) {
      for (const option of control.selectedOptions) {
        const id = option.value,
          name = `portion_${role}_${id}`;
        if (!catalog[id]) continue;
        portionNames.add(name);
        if (portionTarget.querySelector(`[name="${name}"]`)) continue;
        const row = document.createElement("div");
        row.dataset.portionField = name;
        const label = document.createElement("label");
        label.htmlFor = `id_${name}`;
        label.className = "block text-sm font-semibold mb-1";
        label.textContent = `${role === "x" ? "Buy X" : "Get Y"} · ${catalog[id].name}`;
        const select = document.createElement("select");
        Object.assign(select, {
          name,
          id: label.htmlFor,
          className: "w-full rounded-md border px-3 py-2",
        });
        for (const [key, value] of Object.entries(catalog[id].portions))
          select.add(new Option(value.label, key));
        select.value = saved.get(name) || "standard";
        row.append(label, select);
        portionTarget.append(row);
        window.INPROFICEnhanceForms?.(row);
      }
    }
    portionTarget.querySelectorAll("[data-portion-field]").forEach((row) => {
      if (!portionNames.has(row.dataset.portionField)) {
        saved.set(row.dataset.portionField, row.querySelector("select").value);
        row.remove();
      }
    });
    const selection = (id) =>
      portionTarget.querySelector(`[name="portion_x_${id}"]`)?.value ||
      "standard";
    const needed = new Set(
      selected.flatMap((id) =>
        codes
          .filter(
            (code) =>
              catalog[id]?.portions[selection(id)]?.prices[code] != null,
          )
          .map((code) => `price_${id}_${code}`),
      ),
    );
    target.querySelectorAll("[data-price-field]").forEach((row) => {
      if (!needed.has(row.dataset.priceField)) {
        saved.set(
          `${row.dataset.priceField}:${row.dataset.selection || "standard"}`,
          row.querySelector("input").value,
        );
        row.remove();
      }
    });
    selected.forEach((id) =>
      codes.forEach((code) => {
        const name = `price_${id}_${code}`,
          portion = selection(id);
        if (!needed.has(name)) return;
        const existing = target.querySelector(`[data-price-field="${name}"]`);
        if (
          existing &&
          (existing.dataset.selection || "standard") !== portion
        ) {
          saved.set(
            `${name}:${existing.dataset.selection || "standard"}`,
            existing.querySelector("input").value,
          );
          existing.remove();
        }
        if (
          target.querySelector(`[data-price-field="${name}"]`) ||
          !catalog[id]
        )
          return;
        const row = document.createElement("div");
        row.dataset.priceField = name;
        row.dataset.selection = portion;
        const label = document.createElement("label");
        label.className = "block text-sm font-semibold mb-1";
        label.htmlFor = `id_${name}`;
        label.textContent = `${catalog[id].name} · ${labels[code]}`;
        const input = document.createElement("input");
        Object.assign(input, {
          id: `id_${name}`,
          name,
          type: "number",
          min: "0.01",
          step: "0.01",
          value:
            saved.get(`${name}:${portion}`) ??
            catalog[id].portions[portion].prices[code],
          className: "w-full rounded-md border px-3 py-2",
        });
        row.append(label, input);
        target.append(row);
        window.INPROFICEnhanceForms?.(row);
      }),
    );
  }
  target.querySelectorAll("[data-price-field]").forEach((row) => {
    const id = row.dataset.priceField.split("_")[1];
    row.dataset.selection =
      portionTarget.querySelector(`[name="portion_x_${id}"]`)?.value ||
      "standard";
  });
  products.addEventListener("change", update);
  rewards.addEventListener("change", update);
  portionTarget.addEventListener("change", update);
  let previousChannels = [...channels.selectedOptions].map((o) => o.value);
  channels.addEventListener("change", () => {
    const current = [...channels.selectedOptions].map((o) => o.value);
    if (current.includes("all") && current.length > 1) {
      const justSelectedAll = !previousChannels.includes("all");
      for (const option of channels.options) {
        if (justSelectedAll) option.selected = option.value === "all";
        else if (option.value === "all") option.selected = false;
      }
    }
    previousChannels = [...channels.selectedOptions].map((o) => o.value);
    update();
  });
  update();
})();
