(function () {
  "use strict";
  function update() {
    const channel =
      document.querySelector('[name="order_mode"]:checked')?.value ||
      document.querySelector('[name="order_mode"]')?.value;
    document.querySelectorAll("[data-product-ribbon-slot]").forEach((slot) => {
      const card = slot.closest(".product-card"),
        select = card.querySelector("[data-product-variant-select]");
      const option = select?.selectedOptions[0];
      const id =
        option?.dataset.productId ||
        option?.value ||
        card.dataset.primaryProductId ||
        card.dataset.productId;
      if (id && slot.dataset.ribbonProduct !== id) {
        const config = card.querySelector(
          `template[data-ribbon-config="${CSS.escape(id)}"]`,
        );
        if (config) {
          slot.replaceChildren(config.content.cloneNode(true));
          slot.dataset.ribbonProduct = id;
        }
      }
    });
    document.querySelectorAll("[data-product-offers]").forEach((ribbon) => {
      const card = ribbon.closest(".product-card");
      const variant = card?.querySelector("[data-product-variant-select]")
        ?.selectedOptions[0];
      const differentProduct =
        variant?.dataset.productId &&
        variant.dataset.productId !== ribbon.dataset.offerProduct;
      let visible = false;
      ribbon.querySelectorAll("[data-offer-channel]").forEach((offer) => {
        offer.hidden =
          !!channel && !["all", channel].includes(offer.dataset.offerChannel);
        visible ||= !offer.hidden;
      });
      ribbon.hidden = !visible || !!differentProduct;
    });
  }
  document.addEventListener("change", update);
  document.addEventListener("DOMContentLoaded", update);
  update();
})();

// Generated rewards are read-only and never become customer-submitted paid items.
window.INPROFICRenderPromotionRewards = function (container, rewards) {
  if (!container) return;
  container.replaceChildren();
  container.hidden = !rewards?.length;
  for (const reward of rewards || []) {
    const row = document.createElement("div");
    row.dataset.promotionReward = "";
    row.className = "border-b py-3 text-sm";
    const title = document.createElement("strong");
    title.textContent = reward.name;
    const detail = document.createElement("p");
    detail.className = "mt-1 text-xs";
    detail.textContent = `${reward.quantity || reward.payable_quantity} ${reward.unit || ""} · Free promotion reward · Automatically added`;
    row.append(title, detail);
    container.append(row);
  }
};
