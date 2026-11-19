(() => {
  const form = document.getElementById("pos-form");
  if (!form) return;
  const csrf = () => form.querySelector("[name=csrfmiddlewaretoken]").value;
  const tools = document.createElement("div");
  tools.className = "flex flex-wrap gap-2 my-3";
  const status = document.createElement("span");
  status.setAttribute("role", "status");
  status.className = "text-xs";
  const park = document.createElement("button");
  park.type = "button";
  park.className = "workspace-action";
  park.textContent = "Park basket";
  const resume = document.createElement("button");
  resume.type = "button";
  resume.className = "workspace-action";
  resume.textContent = "Parked baskets";
  const scan = document.createElement("button");
  scan.type = "button";
  scan.className = "workspace-action";
  scan.textContent = "Scan barcode";
  tools.append(park, resume, scan, status);
  form.before(tools);
  const call = async (payload) => {
    const response = await fetch("/retail/baskets/", {
      method: payload ? "POST" : "GET",
      headers: payload
        ? { "Content-Type": "application/json", "X-CSRFToken": csrf() }
        : {},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    if (!response.ok) throw Error("Could not update parked baskets.");
    return response.json();
  };
  park.onclick = async () => {
    try {
      const contents = Object.fromEntries(new FormData(form));
      if (
        !Object.entries(contents).some(
          ([name, value]) => name.startsWith("qty_") && Number(value) > 0,
        )
      ) {
        status.textContent = "Add a product before parking this basket.";
        return;
      }
      await call({ label: contents.customer_name || "Parked sale", contents });
      form.reset();
      form.dispatchEvent(new Event("change", { bubbles: true }));
      status.textContent =
        "Basket parked. Stock and prices will be checked when sold.";
    } catch (error) {
      status.textContent = error.message;
    }
  };
  resume.onclick = async () => {
    try {
      const { baskets } = await call();
      const dialog = document.getElementById("pos-parked-dialog");
      const rows = dialog.querySelector("[data-parked-rows]");
      const message = dialog.querySelector("[data-parked-status]");
      rows.replaceChildren();
      message.textContent = baskets.length
        ? `${baskets.length} saved basket${baskets.length === 1 ? "" : "s"}`
        : "No parked baskets yet.";
      dialog.querySelector("[data-parked-close]").onclick = () =>
        dialog.close();
      baskets.forEach((basket) => {
        const row = document.createElement("tr");
        for (const value of [
          basket.label,
          new Date(basket.created_at).toLocaleString(undefined, {
            day: "numeric",
            month: "short",
            hour: "numeric",
            minute: "2-digit",
          }),
          basket.product_count,
        ]) {
          const cell = document.createElement("td");
          cell.textContent = String(value);
          row.append(cell);
        }
        const actions = document.createElement("td");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "workspace-action";
        button.textContent = "Restore basket";
        button.setAttribute("aria-label", "Restore " + basket.label);
        button.onclick = async () => {
          const active = [...new FormData(form)].some(
            ([name, value]) => name.startsWith("qty_") && Number(value) > 0,
          );
          if (active) {
            message.textContent =
              "Park or complete the current basket before restoring another.";
            return;
          }
          button.disabled = true;
          try {
            const { contents } = await call({ resume: basket.id });
            form.reset();
            Object.entries(contents).forEach(([name, value]) => {
              const field = form.elements.namedItem(name);
              if (field instanceof RadioNodeList) field.value = value;
              else if (field) field.value = value;
            });
            form.dispatchEvent(new Event("change", { bubbles: true }));
            form.dispatchEvent(new Event("input", { bubbles: true }));
            dialog.close();
            status.textContent =
              "Basket restored; review current prices and availability.";
          } catch (error) {
            message.textContent = error.message;
            button.disabled = false;
          }
        };
        actions.append(button);
        row.append(actions);
        rows.append(row);
      });
      dialog.showModal();
    } catch (error) {
      status.textContent = error.message;
    }
  };
  const search = document.getElementById("pos-search");
  const applyBarcode = (value) => {
    if (search) {
      search.value = value;
      search.dispatchEvent(new Event("input", { bubbles: true }));
    }
    const card = [...document.querySelectorAll("[data-barcode]")].find(
      (card) => card.dataset.barcode === value,
    );
    if (card) {
      const field = card.querySelector("input[type=number]");
      field?.focus();
    }
  };
  search?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyBarcode(search.value.trim());
    }
  });
  scan.onclick = async () => {
    if (!("BarcodeDetector" in window)) {
      status.textContent =
        "Use a keyboard barcode scanner or enter the code in product search on this browser.";
      search?.focus();
      return;
    }
    let stream;
    const dialog = document.createElement("dialog");
    dialog.className = "p-5 rounded-xl";
    const video = document.createElement("video");
    video.autoplay = true;
    video.playsInline = true;
    video.style.maxWidth = "min(80vw,30rem)";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close scanner";
    close.className = "workspace-action";
    close.onclick = () => dialog.close();
    dialog.append(video, close);
    document.body.append(dialog);
    dialog.addEventListener(
      "close",
      () => {
        stream?.getTracks().forEach((track) => track.stop());
        dialog.remove();
      },
      { once: true },
    );
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      video.srcObject = stream;
      dialog.showModal();
      await video.play();
      const detector = new BarcodeDetector();
      const read = async () => {
        if (!dialog.open) return;
        const results = await detector.detect(video);
        if (results.length) {
          applyBarcode(results[0].rawValue);
          dialog.close();
        } else setTimeout(() => read().catch(() => {}), 250);
      };
      await read();
    } catch (_) {
      status.textContent =
        "Camera scanning is unavailable. Use a scanner or type the barcode.";
      stream?.getTracks().forEach((track) => track.stop());
      dialog.remove();
    }
  };
})();
