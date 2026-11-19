(() => {
  const form = document.querySelector("[data-quotation-form]");
  if (!form) return;
  const prices = JSON.parse(
    document.getElementById("quotation-channel-prices")?.textContent || "{}",
  );
  const channel = form.querySelector('[name="channel"]');
  const updatePrice = (row, replace = false) => {
    const product = row.querySelector('select[name$="-product"]');
    const price = row.querySelector('input[name$="-unit_price"]');
    const baseline = prices[product.value]?.[channel.value];
    if (replace) price.value = baseline ?? "";
    const note = row.querySelector("[data-quote-price-note]");
    if (note)
      note.textContent =
        baseline === undefined
          ? ""
          : `Channel price: ${baseline}. ${price.value && Number(price.value) !== Number(baseline) ? "Adjusted price will be recorded internally." : "You can adjust this price."}`;
    if (replace) price.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const rows = form.querySelector("[data-quote-rows]");
  const total = form.querySelector('input[name$="-TOTAL_FORMS"]');
  const restoreDeleted = () =>
    rows.querySelectorAll("[data-quote-line]").forEach((row) => {
      if (row.querySelector('input[name$="-DELETE"]')?.checked) {
        row.hidden = true;
        row
          .querySelectorAll("[required]")
          .forEach((field) => (field.required = false));
      }
    });
  restoreDeleted();
  rows.querySelectorAll("[data-quote-line]").forEach((row) => updatePrice(row));
  rows.addEventListener("change", (event) => {
    const row = event.target.closest("[data-quote-line]");
    if (row) updatePrice(row, event.target.matches('select[name$="-product"]'));
  });
  rows.addEventListener("input", (event) => {
    if (event.target.matches('input[name$="-unit_price"]'))
      updatePrice(event.target.closest("[data-quote-line]"));
  });
  channel.addEventListener("change", () =>
    rows.querySelectorAll("[data-quote-line]").forEach((row) => {
      if (!row.hidden) updatePrice(row, true);
    }),
  );
  form.querySelector("[data-quote-add]").addEventListener("click", () => {
    const html = form
      .querySelector("[data-quote-empty]")
      .innerHTML.replaceAll("__prefix__", total.value);
    rows.insertAdjacentHTML("beforeend", html);
    total.value = String(Number(total.value) + 1);
    const row = rows.lastElementChild;
    row.querySelector("select")?.focus();
  });
  rows.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quote-remove]");
    if (!button) return;
    const row = button.closest("[data-quote-line]");
    row.querySelector('input[name$="-DELETE"]').checked = true;
    row.hidden = true;
    row
      .querySelectorAll("[required]")
      .forEach((field) => (field.required = false));
  });
})();
