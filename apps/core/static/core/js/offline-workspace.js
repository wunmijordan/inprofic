/* Offline packages are encrypted on this device and replayed only on confirmation. */
(() => {
  const encoder = new TextEncoder(),
    decoder = new TextDecoder();
  let activePackage = "",
    key = null,
    state = null,
    recordId = "",
    db;
  try {
    recordId = localStorage.getItem("inprofic:offline:last") || "";
  } catch (_) {}
  const ready = new Promise((resolve, reject) => {
    const request = indexedDB.open("inprofic-offline-v1", 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore("packages");
    request.onsuccess = () => {
      db = request.result;
      resolve();
    };
    request.onerror = () => reject(request.error);
  });
  const storage = async (mode, action) => {
    await ready;
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("packages", mode);
      const request = action(transaction.objectStore("packages"));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () =>
        reject(transaction.error || Error("Offline storage was interrupted."));
    });
  };
  let locked = false,
    releaseLock;
  async function acquireLock() {
    if (locked) return;
    if (!navigator.locks)
      throw Error(
        "Use a browser that supports secure offline storage locking.",
      );
    let granted;
    const acquired = new Promise((resolve) => {
      granted = resolve;
    });
    navigator.locks.request(
      "inprofic-offline-workspace",
      { ifAvailable: true },
      async (lock) => {
        if (!lock) {
          granted(false);
          return;
        }
        locked = true;
        granted(true);
        await new Promise((resolve) => {
          releaseLock = resolve;
        });
      },
    );
    if (!(await acquired))
      throw Error(
        "Close the other offline workspace tab before continuing here.",
      );
  }
  addEventListener("pagehide", () => {
    releaseLock?.();
    locked = false;
  });
  const derive = async (pass, salt) => {
    const material = await crypto.subtle.importKey(
      "raw",
      encoder.encode(pass),
      "PBKDF2",
      false,
      ["deriveKey"],
    );
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations: 210000, hash: "SHA-256" },
      material,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  };
  const save = async () => {
    const previous = await storage("readonly", (store) => store.get(recordId));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      encoder.encode(JSON.stringify(state)),
    );
    await storage("readwrite", (store) =>
      store.put({ salt: previous.salt, iv, ciphertext }, recordId),
    );
  };
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const shell = document.querySelector("[data-offline-workspace]");
  const status = element("p", "", "text-sm my-3");
  status.setAttribute("role", "status");
  const dialog = document.getElementById("connection-availability-dialog");
  if (!dialog) return;
  let reachable = navigator.onLine;
  let checking = null;
  const preparing = new URL(location.href).searchParams.has("prepare");
  if (shell && preparing) history.replaceState({}, "", location.pathname);
  const connection = () => {
    dialog.querySelector("[data-connection-state]").textContent = reachable
      ? "Connection available"
      : "Connection unavailable";
    dialog.querySelector("#connection-title").textContent = reachable
      ? "Offline access"
      : "INPROFIC is offline";
    prepare.disabled = !reachable;
    unlock.disabled = !recordId;
    unlock.textContent = state
      ? "Open offline workspace"
      : "Unlock saved packages";
    packageHint.textContent = !recordId
      ? "No offline packages are saved on this device yet. Prepare them while connected first."
      : !reachable
        ? "Saved packages can be unlocked without a connection. Preparing or refreshing requires a connection."
        : "Use the passphrase chosen when preparing this device.";
    dialog.querySelector("[data-connection-retry]").hidden =
      reachable && preparing;
  };
  const open = () => {
    connection();
    if (!dialog.open) dialog.showModal();
  };
  async function checkConnection(showFailure = true) {
    if (checking) {
      await checking;
      return reachable;
    }
    let finish;
    checking = new Promise((resolve) => {
      finish = resolve;
    });
    try {
      if (!navigator.onLine) throw Error("Disconnected");
      const response = await fetch("/health/", {
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
        headers: { Accept: "application/json" },
      });
      const health = await response.json();
      reachable =
        navigator.onLine &&
        response.ok &&
        health.status === "ok" &&
        health.service === "inprofic";
    } catch (_) {
      reachable = false;
    } finally {
      finish();
      checking = null;
    }
    connection();
    if (!reachable && showFailure && (!shell || !state)) open();
    return reachable;
  }
  dialog.querySelector("[data-connection-retry]").onclick = async () => {
    if (await checkConnection(false)) {
      if (shell)
        location.href =
          location.pathname === "/pwa/offline/" ? "/" : location.href;
      else dialog.close();
    } else
      status.textContent =
        "Connection is still unavailable. Try again or use your saved offline packages.";
  };
  dialog.addEventListener("cancel", (event) => {
    if (shell && !state) event.preventDefault();
  });
  const toggle = dialog.querySelector("[data-offline-toggle]"),
    access = dialog.querySelector("[data-offline-access]");
  const pass = element("input");
  pass.id = "offline-passphrase";
  pass.type = "password";
  pass.className = "border px-3 py-2";
  pass.autocomplete = "off";
  pass.minLength = 6;
  pass.setAttribute("aria-label", "Offline passphrase");
  pass.placeholder = "Offline passphrase (at least 6 characters)";
  const unlock = element("button", "Unlock saved packages", "workspace-action");
  unlock.type = "button";
  const prepare = element(
    "button",
    "Prepare / refresh packages",
    "workspace-action",
  );
  prepare.type = "button";
  const explanation = element(
    "p",
    "Your offline passphrase protects saved business records on this device. Choose it when preparing packages, then use the same one to unlock them later. Unsynced work cannot be recovered without it.",
    "text-sm my-3",
  );
  const passLabel = element("label", "Offline passphrase");
  passLabel.htmlFor = pass.id;
  const packageHint = element("p", "", "text-sm my-3");
  packageHint.setAttribute("role", "note");
  const actions = element("div");
  actions.append(unlock, prepare);
  access.append(explanation, passLabel, pass, actions, packageHint, status);
  pass.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (recordId) unlock.click();
    else prepare.click();
  });
  prepare.onclick = async () => {
    try {
      if (pass.value.length < 6) throw Error("Use at least 6 characters.");
      await acquireLock();
      const response = await fetch("/retail/offline/snapshot/", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (
        !response.ok ||
        !response.headers.get("content-type")?.includes("application/json")
      )
        throw Error(
          "Sign in and obtain device approval before preparing packages.",
        );
      const snapshot = await response.json();
      let receipts = [];
      for (const owner of new Set([recordId, snapshot.owner].filter(Boolean))) {
        const previous = await storage("readonly", (store) => store.get(owner));
        if (!previous) continue;
        const oldKey = await derive(pass.value, new Uint8Array(previous.salt));
        const old = JSON.parse(
          decoder.decode(
            await crypto.subtle.decrypt(
              { name: "AES-GCM", iv: previous.iv },
              oldKey,
              previous.ciphertext,
            ),
          ),
        );
        if (old.pending.length) {
          throw Error(
            "Unlock and sync pending entries before replacing this package.",
          );
        }
        if (owner === snapshot.owner) receipts = old.receipts || [];
      }
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const nextKey = await derive(pass.value, salt);
      const nextState = { snapshot, pending: [], receipts };
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        nextKey,
        encoder.encode(JSON.stringify(nextState)),
      );
      await storage("readwrite", (store) =>
        store.put({ salt, iv, ciphertext }, snapshot.owner),
      );
      recordId = snapshot.owner;
      key = nextKey;
      state = nextState;
      localStorage.setItem("inprofic:offline:last", recordId);
      pass.value = "";
      connection();
      status.textContent =
        "Packages prepared. Open the offline workspace to continue. Entries remain pending until you confirm syncing.";
      if (toggle.checked && !shell) location.href = "/pwa/offline/";
    } catch (error) {
      status.textContent = error.message;
    }
  };
  unlock.onclick = async () => {
    try {
      if (state) {
        if (shell) {
          dialog.close();
          render();
        } else location.href = "/pwa/offline/";
        return;
      }
      await acquireLock();
      const saved = await storage("readonly", (store) => store.get(recordId));
      if (!saved) throw Error("Prepare packages while connected first.");
      key = await derive(pass.value, new Uint8Array(saved.salt));
      state = JSON.parse(
        decoder.decode(
          await crypto.subtle.decrypt(
            { name: "AES-GCM", iv: saved.iv },
            key,
            saved.ciphertext,
          ),
        ),
      );
      pass.value = "";
      dialog.close();
      await refreshSnapshot();
      render();
    } catch (error) {
      status.textContent =
        error.name === "OperationError"
          ? "Could not unlock. Check the passphrase."
          : error.message;
    }
  };
  toggle.onchange = () => {
    access.hidden = !toggle.checked;
    if (toggle.checked) {
      if (shell) {
        if (state) {
          dialog.close();
          render();
        }
      } else location.href = "/pwa/offline/";
    } else if (shell) location.href = "/";
  };
  addEventListener("offline", () => {
    reachable = false;
    connection();
    if (!shell || !state) open();
    const sync = document.querySelector("[data-offline-sync]");
    if (sync) sync.disabled = true;
  });
  addEventListener("online", async () => {
    await checkConnection(false);
    if (shell && state) {
      const sync = document.querySelector("[data-offline-sync]");
      if (sync) sync.disabled = !state.pending.length || !reachable;
    }
  }); // Never sends entries automatically.
  setInterval(() => {
    if (document.visibilityState === "visible") checkConnection();
  }, 30000);
  const units = (value) => {
    if (!/^\d+(?:\.\d{1,2})?$/.test(String(value)))
      throw Error("Use a quantity with at most two decimal places.");
    const [whole, fraction = ""] = String(value).split(".");
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
  };
  const decimal = (value) =>
    `${value / 100n}.${String(value % 100n).padStart(2, "0")}`;
  async function queue(kind, data, renderAfter = true) {
    const previous = structuredClone(state);
    try {
      const entry = {
        id: crypto.randomUUID(),
        kind,
        data,
        created_at: new Date().toISOString(),
        status: "Pending sync",
      };
      if (kind === "cash_sale") {
        for (const item of [...data.items, ...(data.reward_items || [])]) {
          const product = state.snapshot.products.find(
            (product) => product.id === item.id,
          );
          product.stock = decimal(units(product.stock) - units(item.quantity));
        }
        state.receipts ??= [];
        state.receipts.push({ ...entry });
      }
      state.pending.push(entry);
      await save();
      if (renderAfter) render();
      return entry;
    } catch (error) {
      state = previous;
      throw Error(
        "Could not save on this device. The operation was not completed. " +
          error.message,
      );
    }
  }
  async function refreshSnapshot() {
    if (!shell || !state || !navigator.onLine) return;
    try {
      const response = await fetch("/retail/offline/snapshot/", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const snapshot = await response.json();
      if (snapshot.owner !== state.snapshot.owner) return;
      for (const entry of state.pending.filter(
        (entry) => entry.kind === "cash_sale",
      ))
        for (const item of [
          ...entry.data.items,
          ...(entry.data.reward_items || []),
        ]) {
          const product = snapshot.products.find(
            (product) => product.id === item.id,
          );
          if (product) {
            const available = units(product.stock) - units(item.quantity);
            product.stock = decimal(available > 0n ? available : 0n);
          }
        }
      state.snapshot = snapshot;
      await save();
    } catch (_) {
      /* The last encrypted snapshot remains available when disconnected. */
    }
  }

  function showReceipt(entry) {
    const receipt = element("dialog");
    receipt.id = "offline-receipt";
    receipt.setAttribute("data-native-dialog", "");
    receipt.className = "p-4 rounded-xl w-full max-w-3xl";
    if (state.snapshot.ui?.receipt) {
      receipt.innerHTML = state.snapshot.ui.receipt;
      inlinePreparedImages(receipt);
      const status = receipt.querySelector("[data-document-status]");
      if (status) status.textContent = entry.status;
      const reference = receipt.querySelector("[data-document-reference]");
      if (reference) reference.textContent = entry.id;
      const items = receipt.querySelector("[data-document-items]");
      items.replaceChildren();
      let total = 0n;
      for (const item of entry.data.receipt_items || entry.data.items) {
        const amount =
          item.line_total != null
            ? units(item.line_total)
            : (units(item.price) * units(item.quantity) + 50n) / 100n;
        total += amount;
        const row = element(
          "div",
          undefined,
          "flex items-start justify-between gap-5 py-4",
        );
        const left = element("div");
        left.append(
          element("p", item.name || item.id, "font-semibold"),
          element(
            "p",
            item.quantity + " × " + (item.unit_price || item.price),
            "text-xs mt-1",
          ),
        );
        row.append(
          left,
          element("strong", state.snapshot.currency + decimal(amount)),
        );
        items.append(row);
      }
      receipt.querySelector("[data-document-total]").textContent =
        state.snapshot.currency + decimal(total);
      const offer = receipt.querySelector("[data-document-promotion]");
      if (offer) {
        offer.hidden = !(Number(entry.data.promotion?.saving) > 0);
        offer.textContent = entry.data.promotion?.saving
          ? `Order offer · ${entry.data.promotion.name} · Saved ${state.snapshot.currency}${entry.data.promotion.saving}`
          : "";
      }
      const customer = receipt.querySelector("[data-document-customer]");
      if (customer)
        customer.textContent = entry.data.customer || "Walk-in Customer";
      const print = receipt.querySelector("button");
      if (print) print.remove();
    } else
      receipt.append(
        element("h2", "Offline POS receipt", "text-xl"),
        element("p", entry.data.customer || "Walk-in Customer"),
      );
    receipt.append(
      element(
        "p",
        entry.status + " · " + new Date(entry.created_at).toLocaleString(),
        "text-sm mt-4",
      ),
    );
    const print = element("button", "Print receipt", "workspace-action");
    print.onclick = () => {
      document.body.classList.add("printing-offline-receipt");
      window.print();
      document.body.classList.remove("printing-offline-receipt");
    };
    const close = element("button", "Close", "workspace-action");
    close.onclick = () => receipt.close();
    receipt.append(print, close);
    document.body.append(receipt);
    receipt.addEventListener("close", () => receipt.remove(), { once: true });
    receipt.showModal();
  }

  let workspaceInstalled = false;
  let content, canvas;
  const packageActions = {};
  function installWorkspace() {
    if (workspaceInstalled) return;
    if (!state.snapshot.ui?.navigation) {
      workspaceInstalled = true;
      shell.className = "workspace-main flex-1 min-w-0 p-4 md:p-6";
      return; // Older encrypted queues remain reviewable and can still sync.
    }
    const tenant = state.snapshot.ui.tenant;
    const root = document.documentElement;
    root.dataset.tenant = tenant.id;
    root.dataset.tenantAccent = tenant.accent;
    root.dataset.tenantBackground = tenant.background;
    root.style.setProperty("--tenant-background", tenant.background);
    root.style.setProperty(
      "--tenant-background-contrast",
      tenant.background_contrast,
    );
    root.style.setProperty("--tenant-button", tenant.accent);
    root.style.setProperty("--tenant-button-contrast", tenant.accent_contrast);
    try {
      root.dataset.sidebar =
        localStorage.getItem("inprofic:sidebar") || "expanded";
    } catch (_) {}
    document.body.classList.add("flex");
    document.body.dataset.uiWorkspace = "";
    const wrapper = element("div");
    wrapper.innerHTML = state.snapshot.ui.navigation;
    shell.before(...wrapper.childNodes);
    shell.id = "main-content";
    shell.className =
      "workspace-main desktop-main flex-1 min-w-0 p-4 md:p-6 mt-14 md:mt-0";
    workspaceInstalled = true;
    const mobileToggle = document.getElementById("mobile-nav-button");
    const sidebarToggle = document.getElementById("sidebar-toggle");
    if (mobileToggle) mobileToggle.disabled = true;
    if (sidebarToggle) sidebarToggle.disabled = true;
    for (const source of [
      "workspace.js",
      "interactions.js",
      "brand-assets.js",
      "shell-tabs.js",
    ]) {
      const script = element("script");
      script.src = "/static/core/js/" + source;
      script.onload = () => {
        if (source === "shell-tabs.js" && mobileToggle)
          mobileToggle.disabled = false;
        if (source === "interactions.js" && sidebarToggle)
          sidebarToggle.disabled = false;
      };
      document.body.append(script);
    }
    document.addEventListener("click", (event) => {
      const link = event.target.closest("[data-offline-package]");
      if (!link) return;
      event.preventDefault();
      openPackage(link.dataset.offlinePackage);
      document
        .getElementById("mobile-nav-panel")
        ?.setAttribute("data-open", "false");
      document.getElementById("mobile-nav-panel")?.setAttribute("inert", "");
      document
        .getElementById("mobile-nav-button")
        ?.setAttribute("aria-expanded", "false");
    });
    // The brand link also stays within the prepared workspace.
    document
      .querySelectorAll(".sidebar-brand,#mobile-nav-shell a")
      .forEach((link) => {
        if (link.dataset.offlinePackage) return;
        link.addEventListener("click", (event) => {
          event.preventDefault();
          openPackage("dashboard");
        });
      });
  }
  function openPackage(name) {
    if (name === "settings") {
      open();
      return;
    }
    if (!packageActions[name]) return;
    activePackage = name;
    content.replaceChildren();
    packageActions[name]();
    document.querySelectorAll("[data-offline-package]").forEach((link) => {
      if (link.dataset.offlinePackage === name)
        link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }
  function savedPage(name) {
    const markup = state.snapshot.ui?.pages?.[name];
    if (!markup) {
      content.append(element("p", "This page is not in the prepared package."));
      return;
    }
    const parsed = new DOMParser().parseFromString(markup, "text/html");
    const body = parsed.querySelector(".workspace-content");
    inlinePreparedImages(parsed);
    if (!body) return;
    body
      .querySelectorAll("script,.workspace-utility-bar,[data-ui-root]")
      .forEach((node) => node.remove());
    body.querySelectorAll("*").forEach((node) => {
      [...node.attributes]
        .filter((attr) => attr.name.startsWith("on"))
        .forEach((attr) => node.removeAttribute(attr.name));
    });
    body.querySelectorAll("[x-cloak],[x-show]").forEach((node) => {
      if (node.matches('[role="tabpanel"]')) {
        node.removeAttribute("x-cloak");
        node.hidden = node.id !== "dashboard-panel-overview";
      } else node.remove();
    });
    body.querySelector('[data-tour-target="dashboard"] details')?.remove();
    body.querySelector('[aria-label="Dashboard view"]')?.remove();
    body.querySelector('input[type="search"]')?.closest("form")?.remove();
    // Keep the live screen, but remove operations that were not packaged.
    body.querySelectorAll("form").forEach((form) => {
      const assignment =
        name === "delivery" &&
        state.snapshot.deliveries.find((row) =>
          form.getAttribute("action")?.includes(row.public_id),
        );
      if (assignment && state.snapshot.packages.delivery_edit) {
        form.dataset.noDialog = "";
        const driver = form.querySelector('[name="driver_id"]');
        const submit = form.querySelector(
          'button:not([type]),button[type="submit"]',
        );
        if (!driver || !submit) {
          form.remove();
          return;
        }
        const label = driver.closest("label");
        driver.setAttribute(
          "aria-label",
          "Dispatch rider for " + assignment.order,
        );
        for (const option of [...driver.options]) {
          if (
            option.value &&
            !state.snapshot.drivers.some(
              (row) =>
                row.id === Number(option.value) &&
                row.provider === assignment.provider,
            )
          )
            option.remove();
        }
        const queued = state.pending.find(
          (row) =>
            row.kind === "dispatch_assignment" &&
            row.data.assignment === assignment.id,
        );
        if (queued) driver.value = queued.data.driver;
        form.replaceChildren(label, submit);
        form.closest("details")?.setAttribute("open", "");
        submit.textContent = "Save dispatch assignment";
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (!driver.value) return;
          const data = {
            assignment: assignment.id,
            driver: Number(driver.value),
            updated_at: assignment.updated_at,
          };
          if (queued) {
            queued.data = data;
            await save();
            render();
          } else await queue("dispatch_assignment", data);
        });
      } else form.remove();
    });
    body.querySelectorAll("a").forEach((link) => {
      const href = link.getAttribute("href") || "";
      const flag =
        name === "expiration" &&
        href.match(new RegExp("/retail/expiration/([0-9]+)/flag/"));
      if (flag && state.snapshot.packages.inventory_edit) {
        const button = element("button", link.textContent, "workspace-action");
        button.type = "button";
        button.onclick = () =>
          queue("expiry_flag", {
            lot: Number(flag[1]),
            note: "Flagged for expiry review offline",
          });
        link.replaceWith(button);
        return;
      }
      // Captured links cannot leave the packaged functionality.
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
      link.removeAttribute("data-form-dialog");
    });
    body.querySelectorAll("button").forEach((button) => {
      if (
        button.closest("form") ||
        (name === "expiration" &&
          button.textContent.includes("Flag for review"))
      )
        return;
      button.disabled = true;
    });
    body.querySelectorAll("input,select,textarea").forEach((field) => {
      if (!field.closest("form")) field.disabled = true;
    });
    body
      .querySelectorAll("[data-metric-url]")
      .forEach((node) => node.removeAttribute("data-metric-url"));
    for (const [metric, data] of Object.entries(
      state.snapshot.dashboard || {},
    )) {
      const card = body.querySelector(
        '[data-dashboard-metric="' + metric + '"]',
      );
      if (card?.querySelector("[data-metric-value]"))
        card.querySelector("[data-metric-value]").textContent =
          data.currency + data.value;
    }
    if (name === "dashboard") {
      body.querySelectorAll('[role="tab"]').forEach((button) => {
        const panelId = button.getAttribute("aria-controls");
        if (!panelId || !body.querySelector("#" + panelId)) return;
        button.disabled = false;
        button.addEventListener("click", () => {
          content
            .querySelectorAll('[role="tabpanel"]')
            .forEach((panel) => (panel.hidden = panel.id !== panelId));
          content
            .querySelectorAll('[role="tab"]')
            .forEach((tab) =>
              tab.setAttribute("aria-selected", String(tab === button)),
            );
        });
      });
      body.querySelectorAll("[data-dashboard-metric]").forEach((card) => {
        if (!state.snapshot.dashboard[card.dataset.dashboardMetric])
          card.hidden = true;
      });
    }
    body.querySelectorAll(".stock-ticker").forEach((ticker) => {
      ticker.classList.remove("is-loading");
      ticker.textContent = "Prepared balances are available in Inventory.";
    });
    content.append(...body.childNodes);
    if (name === "inventory") {
      content.querySelector("#radio-raw")?.parentElement?.remove();
      content.querySelector("#material-category-tabs")?.remove();
      content
        .querySelectorAll("#raw-panel, #finished-panel")
        .forEach((panel) => {
          panel.classList.remove(
            "hidden",
            "opacity-0",
            "translate-y-2",
            "pointer-events-none",
          );
          panel.hidden = false;
        });
      content.querySelectorAll("[data-material-category]").forEach((row) => {
        row.hidden = false;
        row.classList.remove("hidden");
        row.style.removeProperty("display");
      });
      content
        .querySelectorAll("a[aria-disabled]")
        .forEach((link) => link.remove());
      content
        .querySelectorAll("[data-stock-history-trigger]")
        .forEach((button) => {
          button.replaceWith(
            element("span", button.textContent, "font-medium"),
          );
        });
    }

    if (name === "inventory" && state.snapshot.packages.inventory_edit) {
      const form = element(
        "form",
        undefined,
        "workspace-card p-4 mt-5 space-y-3",
      );
      form.dataset.noDialog = "";
      const note = element("textarea");
      note.setAttribute("aria-label", "Stock review note");
      const submit = element(
        "button",
        "Save stock review note",
        "workspace-action",
      );
      form.append(
        element("h2", "Stock review note", "font-semibold"),
        note,
        submit,
      );
      form.onsubmit = (event) => {
        event.preventDefault();
        if (note.value.trim()) queue("stock_note", { note: note.value.trim() });
      };
      content.append(form);
    }
  }
  function offlineBasket(formData) {
    const items = [];
    for (const product of state.snapshot.products) {
      const quantity = formData.get("qty_" + product.id);
      if (!quantity || units(quantity) === 0n) continue;
      if (units(quantity) > units(product.stock))
        throw Error(
          product.name + ": quantity exceeds available stock on this device.",
        );
      items.push({
        id: product.id,
        name: product.name,
        quantity,
        price: product.price,
      });
    }
    if (!items.length) throw Error("Choose at least one product.");
    return items;
  }
  function offlinePromotion(items) {
    const roundEven = (value, denominator) => {
      const whole = value / denominator,
        rem = value % denominator;
      return (
        whole +
        (rem * 2n > denominator || (rem * 2n === denominator && whole % 2n)
          ? 1n
          : 0n)
      );
    };
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.snapshot.timezone || "Africa/Lagos",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const part = (name) => parts.find((p) => p.type === name).value;
    const today = `${part("year")}-${part("month")}-${part("day")}`;
    const offers = (state.snapshot.promotions || []).filter(
      (offer) => today >= offer.starts_on && today <= offer.ends_on,
    );
    const matches = (offer, row, reward = false) =>
      (reward ? offer.reward_product_ids : offer.product_ids).includes(
        row.id,
      ) &&
      ((reward ? offer.reward_portions : offer.product_portions)?.[row.id] ||
        "standard") === "standard";
    const original = items.map((row) =>
      roundEven(units(row.price) * units(row.quantity), 100n),
    );
    const originalTotal = original.reduce((a, b) => a + b, 0n);
    const applied = new Map();
    const prices = items.map((row) => {
      const configured = offers
        .filter(
          (offer) =>
            matches(offer, row) &&
            offer.unit_prices?.[row.id]?.physical_store != null,
        )
        .map((offer) => ({
          offer,
          price: offer.unit_prices[row.id].physical_store,
        }))
        .sort((a, b) =>
          units(a.price) < units(b.price)
            ? -1
            : units(a.price) > units(b.price)
              ? 1
              : Number(a.offer.id) - Number(b.offer.id),
        );
      if (!configured.length) return row.price;
      const chosen = configured[0];
      if (units(chosen.price) !== units(row.price))
        applied.set(chosen.offer.id, chosen.offer);
      return chosen.price;
    });
    const base = items.map((row, i) =>
      roundEven(units(prices[i]) * units(row.quantity), 100n),
    );
    const lines = [...base],
      occupied = new Set(),
      generated = [];
    let rewardValue = 0n;
    function evaluate(offer, blocked, previous) {
      const eligible = items
        .map((row, i) =>
          !blocked.has(i) &&
          matches(offer, row) &&
          units(row.quantity) > 0n &&
          units(row.quantity) % 100n === 0n
            ? i
            : -1,
        )
        .filter((i) => i >= 0);
      const quantity = eligible.reduce(
          (n, i) => n + units(items[i].quantity) / 100n,
          0n,
        ),
        qualifying = BigInt(offer.qualifying_quantity),
        groups = quantity / qualifying;
      if (!groups) return null;
      const targets = [...base];
      let rewards = [],
        value = 0n;
      if ((offer.mode || offer.kind) === "buy_get") {
        const count = groups * BigInt(offer.free_quantity);
        const available = (state.snapshot.products || [])
          .filter((product) => {
            const paid = items
              .filter((row) => row.id === product.id)
              .reduce((n, row) => n + units(row.quantity), 0n);
            const used = previous
              .filter((row) => row.product_id === product.id)
              .reduce((n, row) => n + units(row.quantity), 0n);
            return (
              matches(offer, product, true) &&
              units(product.stock) - paid - used >= count * 100n
            );
          })
          .sort((a, b) =>
            units(a.price) < units(b.price)
              ? -1
              : units(a.price) > units(b.price)
                ? 1
                : Number(a.promotion_order || 0) -
                    Number(b.promotion_order || 0) || a.id.localeCompare(b.id),
          );
        if (!available.length) return null;
        const product = available[0];
        value = count * units(product.price);
        rewards = [
          {
            id: product.id,
            product_id: product.id,
            name: product.name,
            quantity: decimal(count * 100n),
            unit: product.unit,
            unit_price: "0.00",
            line_total: "0.00",
            is_promotion_reward: true,
          },
        ];
      } else if (offer.bundle_price) {
        const amount = eligible.reduce((n, i) => n + base[i], 0n);
        if (!amount) return null;
        const numerator =
          amount * qualifying * groups -
          units(offer.bundle_price) * groups * quantity;
        const reduction = numerator > 0n ? roundEven(numerator, quantity) : 0n;
        if (!reduction) return null;
        let remaining = amount - reduction;
        eligible.forEach((i, index) => {
          targets[i] =
            index === eligible.length - 1
              ? remaining
              : (base[i] * (amount - reduction)) / amount;
          remaining -= targets[i];
        });
      } else return null;
      return {
        offer,
        eligible,
        targets,
        rewards,
        value,
        benefit:
          base.reduce((n, amount, i) => n + amount - targets[i], 0n) + value,
      };
    }
    const candidates = offers
      .map((offer) => evaluate(offer, new Set(), []))
      .filter(Boolean)
      .sort((a, b) =>
        a.benefit > b.benefit
          ? -1
          : a.benefit < b.benefit
            ? 1
            : Number(a.offer.id) - Number(b.offer.id),
      );
    for (const initial of candidates) {
      const candidate = evaluate(initial.offer, occupied, generated);
      if (!candidate) continue;
      candidate.eligible.forEach((i) => {
        occupied.add(i);
        lines[i] = candidate.targets[i];
      });
      generated.push(...candidate.rewards);
      rewardValue += candidate.value;
      applied.set(candidate.offer.id, candidate.offer);
    }
    const total = lines.reduce((a, b) => a + b, 0n),
      details = [...applied.values()].sort(
        (a, b) => Number(a.id) - Number(b.id),
      );
    if (!details.length)
      return {
        lines: original,
        prices: items.map((row) => row.price),
        total: originalTotal,
        promotion: {},
      };
    const first = details[0],
      saving = originalTotal - total + rewardValue;
    return {
      lines,
      prices,
      total,
      promotion: {
        id: first.id,
        name: details.length === 1 ? first.name : `${details.length} offers`,
        mode: first.mode || first.kind,
        kind: first.mode || first.kind,
        description: details.map((offer) => offer.description || "").join(" "),
        help_items: details.length === 1 ? first.help_items || [] : [],
        help_title:
          details.length === 1
            ? first.help_title || first.name
            : `${details.length} offers`,
        campaigns: details,
        rewards: generated,
        automatic_rewards: generated.length > 0,
        saving: decimal(saving > 0n ? saving : 0n),
      },
    };
  }
  if (shell)
    window.INPROFICOfflinePOS = {
      preview(formData) {
        const items = offlineBasket(formData);
        const priced = offlinePromotion(items);
        return {
          items: items
            .flatMap((row, i) => {
              const price = priced.prices[i];
              const numerator = units(price) * units(row.quantity);
              const whole = numerator / 100n,
                remainder = numerator % 100n;
              const uniform =
                whole +
                (remainder > 50n || (remainder === 50n && whole % 2n)
                  ? 1n
                  : 0n);
              if (priced.lines[i] === uniform)
                return [
                  {
                    name: row.name,
                    quantity: row.quantity,
                    unit_price: price,
                    line_total: decimal(priced.lines[i]),
                  },
                ];
              const quantity = units(row.quantity) / 100n,
                low = priced.lines[i] / quantity;
              const highCount = priced.lines[i] - low * quantity;
              return [
                [quantity - highCount, low],
                [highCount, low + 1n],
              ]
                .filter(([qty]) => qty > 0n)
                .map(([qty, unitPrice]) => ({
                  name: row.name,
                  quantity: decimal(qty * 100n),
                  unit_price: decimal(unitPrice),
                  line_total: decimal(qty * unitPrice),
                }));
            })
            .concat(priced.promotion.rewards || []),
          subtotal: decimal(priced.total),
          delivery_fee: "0.00",
          total: decimal(priced.total),
          promotion: priced.promotion,
        };
      },
      async complete(formData) {
        if (
          formData.get("method") !== "cash" ||
          formData.get("manual_payment_received") !== "on"
        )
          throw Error("Confirm that cash was physically received.");
        const preview = this.preview(formData),
          items = offlineBasket(formData);
        const entry = await queue(
          "cash_sale",
          {
            customer: formData.get("customer_name"),
            customer_phone: formData.get("customer_phone"),
            customer_email: formData.get("customer_email"),
            service_mode: formData.get("service_mode"),
            table_reference: formData.get("table_reference"),
            items,
            reward_items: preview.promotion.rewards || [],
            total: preview.total,
            receipt_items: preview.items,
            promotion: preview.promotion,
            cashier_session_id: state.snapshot.cashier_session_id || null,
          },
          false,
        );
        return {
          receipt_id: entry.id + " · Pending sync",
          customer: { name: entry.data.customer || "Walk-in Customer" },
          verified_at: entry.created_at,
          payment: {
            method: "cash",
            method_label: "Cash recorded offline",
            reference: entry.id,
            amount: preview.total,
          },
          items: preview.items,
          promotion: preview.promotion,
          subtotal: preview.total,
          total: preview.total,
          delivery_fee: "0",
        };
      },
      refresh() {
        render();
      },
    };
  function inlinePreparedImages(root) {
    root.querySelectorAll("img[src]").forEach((image) => {
      const saved =
        state.snapshot.ui?.images?.[image.getAttribute("src").split("?")[0]];
      if (saved) image.src = saved;
    });
  }
  function showPOS() {
    const frame = element("iframe");
    frame.title = "Offline POS";
    frame.className = "offline-pos-frame";
    const parsed = new DOMParser().parseFromString(
      state.snapshot.ui.pages.sales,
      "text/html",
    );
    inlinePreparedImages(parsed);
    // Only ordinary cash product quantities are packaged.
    parsed
      .querySelectorAll(
        '[name^="qty_option_"],[name^="bulk_pack_"],[name="split_cash"]',
      )
      .forEach((field) => field.closest("label")?.remove());
    parsed.querySelectorAll("a").forEach((link) => {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
    });
    // Update the exact live cards with this device's remaining availability.
    for (const product of state.snapshot.products) {
      const input = parsed.querySelector('[name="qty_' + product.id + '"]');
      if (input) {
        input.max = product.stock;
        const card = input.closest(".product-card");
        if (card) {
          card.dataset.stockAvailable = product.stock;
          card
            .querySelector("[data-pos-stock-label]")
            ?.replaceChildren(
              document.createTextNode(
                product.stock + " " + product.unit + " available",
              ),
            );
        }
      }
    }
    frame.srcdoc = "<!doctype html>" + parsed.documentElement.outerHTML;
    content.append(frame);
  }
  function render() {
    if (!shell || !state) return;
    installWorkspace();
    shell.hidden = false;
    shell.replaceChildren();
    canvas = element("div", undefined, "workspace-content");
    const snapshot = state.snapshot;
    for (const name of Object.keys(packageActions)) delete packageActions[name];
    canvas.append(
      element(
        "p",
        "Offline mode · " +
          snapshot.branch +
          " · Prepared " +
          new Date(snapshot.saved_at).toLocaleString(),
        "workspace-card p-3 mb-4 text-sm",
      ),
    );
    content = element("section");
    canvas.append(content);
    shell.append(canvas);
    for (const name of ["dashboard", "inventory", "expiration", "delivery"]) {
      if (snapshot.packages[name]) packageActions[name] = () => savedPage(name);
    }
    if (snapshot.packages.sales) packageActions.sales = showPOS;
    packageActions.receipts = () => {
      content.append(
        element("h1", "Offline receipts", "text-2xl font-semibold mb-4"),
      );
      for (const receipt of state.receipts || []) {
        const button = element(
          "button",
          (receipt.data.customer || "Walk-in Customer") +
            " · " +
            receipt.id.slice(0, 8),
          "workspace-action m-2",
        );
        button.onclick = () => showReceipt(receipt);
        content.append(button);
      }
    };
    const pending = element("section", undefined, "mt-6");
    pending.append(
      element(
        "h2",
        `Pending entries (${state.pending.length})`,
        "font-semibold",
      ),
    );
    state.pending.forEach((entry) =>
      pending.append(
        element(
          "p",
          `${entry.kind.replaceAll("_", " ")} · ${entry.status}`,
          "text-sm my-2",
        ),
      ),
    );
    const sync = element(
      "button",
      "Review & confirm sync",
      "workspace-action mt-3",
    );
    sync.dataset.offlineSync = "";
    sync.disabled = !navigator.onLine || !state.pending.length;
    sync.onclick = () => {
      connection();
      const review = element("div");
      review.append(
        element(
          "p",
          `Sync ${state.pending.length} entries to ${snapshot.business}, ${snapshot.branch}? Current stock, prices and permissions will be rechecked.`,
        ),
      );
      const confirm = element(
        "button",
        "Confirm sync",
        "workspace-action workspace-action-primary mt-3",
      );
      confirm.onclick = async () => {
        confirm.disabled = true;
        try {
          const fresh = await fetch("/retail/offline/snapshot/?interface=0", {
            cache: "no-store",
            headers: { Accept: "application/json" },
          });
          if (!fresh.ok)
            throw Error("Reconnect, sign in and check device access.");
          const current = await fresh.json();
          if (current.owner !== snapshot.owner)
            throw Error(
              "Switch back to the original user, business and branch.",
            );
          const response = await fetch("/retail/offline/sync/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": current.csrf,
              Accept: "application/json",
            },
            body: JSON.stringify({
              owner: snapshot.owner,
              confirmed: true,
              entries: state.pending,
            }),
          });
          const result = await response.json();
          if (!response.ok)
            throw Error(result.detail || "Sync was not accepted.");
          const accepted = new Set(
            result.results.filter((row) => row.ok).map((row) => row.id),
          );
          state.pending = state.pending.filter(
            (entry) => !accepted.has(entry.id),
          );
          for (const receipt of state.receipts || []) {
            const acknowledged = result.results.find(
              (row) => row.id === receipt.id && row.ok,
            );
            if (acknowledged)
              receipt.status =
                "Synced · Receipt " + acknowledged.result.receipt;
          }
          result.results
            .filter((row) => !row.ok)
            .forEach((row) => {
              const entry = state.pending.find((entry) => entry.id === row.id);
              if (entry) entry.status = "Needs review: " + row.error;
            });
          await save();
          await refreshSnapshot();
          review.remove();
          dialog.close();
          render();
        } catch (error) {
          status.textContent = error.message;
          confirm.disabled = false;
        }
      };
      review.append(confirm);
      access.append(review);
      open();
    };
    pending.append(sync);
    canvas.append(pending);
    const first =
      activePackage ||
      Object.keys(snapshot.packages).find(
        (name) => snapshot.packages[name] && snapshot.ui?.pages?.[name],
      );
    if (first) openPackage(first);
    else
      content.append(
        element(
          "p",
          "Reconnect to refresh this older package. Your unsynced entries are preserved below.",
          "workspace-card p-4",
        ),
      );
  }

  if (shell) {
    shell.hidden = true;
    toggle.checked = !preparing;
    access.hidden = false;
    open();
  } else if (!navigator.onLine) {
    reachable = false;
    open();
  }
  checkConnection(!shell);
})();
