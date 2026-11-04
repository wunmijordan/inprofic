
function dashboardSearch() {
  return {
    query: '',
    results: [],
    showResults: false,
    loading: false,
    detail: null,
    detailTab: 'overview',
    profitWindow: 0,
    currency: document.querySelector('[data-dashboard-search]').dataset.currency,

    detailTabs() {
      const tabs = [{key:'overview',label:'Overview'},{key:'activity',label:'Activity'}];
      if (this.detail?.type === 'Finished good' && this.detail?.profitability) tabs.push({key:'profit',label:'Profitability'});
      tabs.push({key:'details',label:'Details'});
      return tabs;
    },
    // Where surplus production went, kept out of the table cell and shown in a help tip.
    excessLines(row) {
      return [
        `Planned offcut: ${this.fmt(row.planned_offcut_units || 0)}`,
        `Additional excess: ${this.fmt(row.additional_excess_units || 0)}`,
        `To physical store stock: ${this.fmt(row.excess_stock_units || 0)}`,
        `To market stock: ${this.fmt(row.excess_market_stock_units || 0)}`,
        `To non-stock purpose: ${this.fmt(row.excess_non_stock_units || 0)}`,
      ];
    },
    detailLists() {
      const d = this.detail;
      if (!d) return [];
      const n = (v) => this.fmt(v);
      const list = (key, title, rows, empty, note) => ({ key, title, rows: rows || [], empty, note: note || '' });
      const blocks = [];
      if (d.type === 'Raw material') {
        blocks.push(list('price', 'Procurement price movement', (d.price_history || []).map(r => ({ title: `${r.date} · ${r.supplier || 'Unknown vendor'}`, detail: `${this.money(r.unit_cost)} / ${r.purchase_unit || 'unit'}` })), 'No procurement price history yet.'));
        blocks.push(list('dispense', 'Operational supply dispensing', (d.operational_dispenses || []).map(r => ({ title: `${r.date} · ${r.reason || 'Dispensed'}`, detail: `${n(r.quantity)} ${r.unit || ''}${r.description ? ' · ' + r.description : ''}${r.location ? ' · ' + r.location : ''}` })), 'No operational dispensing recorded.'));
        if (d.uses_production) blocks.push(list('links', `${d.recipe_label} / production links`, (d.linked_goods || []).map(r => ({ title: r.name, detail: `${n(r.qty_per_batch)} ${r.usage_unit || ''} / batch${r.is_base ? ' · Base material' : (r.relation ? ' · ' + r.relation + (r.percent_text ? ' (' + r.percent_text + ')' : '') : '')}` })), 'Not used in any product yet.'));
        if (d.uses_production && (d.base_for || []).length) blocks.push(list('baseFor', 'Base material for', d.base_for.map(name => ({ title: name, detail: 'Other ingredients in this recipe are sized relative to this material.' })), ''));
      } else if (d.type === 'Finished good') {
        if (d.uses_production) blocks.push(list('recipe', `${d.recipe_label} / production cost`, (d.recipe || []).map(r => ({ title: r.name, detail: `${n(r.qty_per_batch)} ${r.unit || ''} / batch${r.is_base ? ' · Base material' : (r.relation ? ' · ' + r.relation + (r.percent_text ? ' (' + r.percent_text + ')' : '') : '')} · ${this.money(r.current_cost)} each` })), 'No recipe set up.', d.note));
        if (d.uses_production && d.base_material) blocks.unshift(list('baseMaterial', 'Base material', [{ title: d.base_material.name, detail: `${d.base_material.text}. Other ingredients are shown relative to this quantity.` }], ''));
        (d.recipe_warnings || []).forEach((warning, i) => blocks.push(list('recipeWarn' + i, 'Recipe note', [{ title: warning, detail: '' }], '')));
      } else if (d.type === 'Procurement vendor') {
        blocks.push(list('materials', 'Materials purchased from this vendor', (d.materials || []).map(r => ({ title: r.name, detail: this.money(r.spend) })), 'No materials purchased yet.'));
        blocks.push(list('purchases', 'Recent purchase history', (d.purchases || []).map(r => ({ title: `${r.date} · ${r.status}`, detail: `${this.money(r.total)}${(r.items || []).length ? ' · ' + r.items.map(i => i.name + ' × ' + n(i.qty)).join(', ') : ''}` })), 'No purchases yet.'));
      } else if (d.type === 'Customer') {
        blocks.push(list('orders', 'Order history', (d.orders || []).map(r => ({ title: `${r.date} · ${r.status}`, detail: `${this.money(r.total)} · ${n(r.units)} units${r.group ? ' · ' + r.group : ''}${r.region ? ' · ' + r.region : ''}` })), 'No orders yet.'));
        blocks.push(list('sales', 'Completed sales', (d.sales || []).map(r => ({ title: `${r.date}${r.payment ? ' · ' + r.payment : ''}`, detail: this.money(r.total) })), 'No completed sales yet.'));
      } else if (d.customers && d.customers.length) {
        blocks.push(list('customers', 'Customers in this segment', (d.customers || []).map(name => ({ title: name, detail: '' })), 'No customers in this segment.'));
      }
      return blocks;
    },
    currentProfitWindow() { return this.detail?.profitability?.windows?.[this.profitWindow] || null; },
    signedMoney(value) { const n = Number(value || 0); return (n < 0 ? '−' : '') + this.money(Math.abs(n)); },
    trendWidth(row) {
      const peak = Math.max(...(this.detail?.profitability?.trend || []).map(r => Math.abs(r.profit)), 1);
      return Math.round(Math.abs(row.profit) / peak * 1000) / 10;
    },

    async search() {
      const q = this.query.trim();
      this.showResults = !!q;
      if (q.length < 2) {
        this.results = [];
        return;
      }
      this.loading = true;
      try {
        const response = await fetch(`${document.querySelector('[data-dashboard-search]').dataset.searchUrl}?q=${encodeURIComponent(q)}`, {
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        const data = await response.json();
        this.results = data.results || [];
      } catch (error) {
        console.error('Dashboard search failed:', error);
        this.results = [];
      } finally {
        this.loading = false;
      }
    },

    async open(result) {
      this.showResults = false;
      this.loading = true;
      this.detailTab = 'overview';
      this.profitWindow = 0;
      try {
        const url = new URL(document.querySelector('[data-dashboard-search]').dataset.detailUrl, window.location.origin);
        url.searchParams.set('type', result.type);
        if (result.type === 'raw' || result.type === 'fg') url.searchParams.set('id', result.key.split(':')[1]);
        if (result.type === 'record') { url.searchParams.set('id', result.key.split(':').at(-1)); url.searchParams.set('model', result.model); }
        if (result.type === 'customer') {
          url.searchParams.set('channel', result.channel);
          url.searchParams.set('value', result.label);
        }
        if (['supplier', 'region', 'group'].includes(result.type)) url.searchParams.set('value', result.label);
        const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!response.ok) throw new Error('Detail request failed');
        this.detail = await response.json();
      } catch (error) {
        console.error('Dashboard search detail failed:', error);
      } finally {
        this.loading = false;
      }
    },

    openFirst() {
      if (this.results.length) this.open(this.results[0]);
    },

    closeResults() { this.showResults = false; },
    closeDetail() { this.detail = null; },
    clear() { this.query = ''; this.results = []; this.showResults = false; },

    fmt(value) {
      return Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },

    money(value) {
      return `${this.currency}${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    },

    summaryCards() {
      const s = this.detail?.summary || {};
      const skip = new Set(['regions', 'groups', 'customers_sample', 'customers', 'stock_unit', 'reorder_unit']);
      if (this.detail?.type === 'Finished good' && !this.detail?.uses_production) {
        ['total_produced', 'delivered_to_customers', 'market_stock', 'expired_market_stock', 'market_transfer_shelf_allowance', 'units_per_batch'].forEach(key => skip.add(key));
      }
      const labels = {
        category: 'Category', cost_per_purchase_unit: 'Cost / purchase unit', last_purchase: 'Last purchase', supplier_count: 'Suppliers',
        total_produced: 'Total produced', delivered_to_customers: 'Delivered', selling_price: 'Default selling price',
        market_stock: 'Distribution Market Stock', expired_market_stock: 'Expired Market Stock', market_transfer_shelf_allowance: 'Market-origin shelf allowance',
        estimated_recipe_cost_per_unit: this.detail?.uses_production ? `Current ${this.detail.recipe_label.toLowerCase()} cost / unit` : 'Latest purchase cost / unit', estimated_margin_per_unit: 'Current est. margin / unit', margin_pct: 'Current est. margin %', units_per_batch: 'Units / batch',
        total_purchase: 'Total purchased', purchase_orders: 'Purchase orders', materials_bought: 'Materials bought', average_purchase: 'Average purchase',
        channel: 'Channel', orders: 'Orders', completed_orders: 'Completed', sales: 'Sales', revenue: 'Revenue', units: 'Units', last_order: 'Last order', last_sale: 'Last sale',
        latest_historical_unit_cost: 'Latest historical cost / unit', unpaid_product_value: 'Unpaid product value', unpaid_product_retail_value: 'Unpaid product value', unpaid_product_cost_value: 'Unpaid product cost', gross_profit: 'Gross profit',
      };
      const cards = [];
      if (s.stock !== undefined && s.stock !== null && s.stock_unit) cards.push({ label: 'Current stock', value: this.fmt(s.stock) + ' ' + s.stock_unit });
      if (s.reorder_level !== undefined && s.reorder_level !== null && s.reorder_unit) cards.push({ label: 'Reorder level', value: this.fmt(s.reorder_level) + ' ' + s.reorder_unit });
      if (s.physical_store_reorder_level !== undefined && s.physical_store_reorder_level !== null && s.reorder_unit) cards.push({ label: 'Physical-store reorder', value: this.fmt(s.physical_store_reorder_level) + ' ' + s.reorder_unit });
      for (const [key, value] of Object.entries(s)) {
        if (skip.has(key) || key === 'stock' || key === 'reorder_level' || key === 'physical_store_reorder_level') continue;
        cards.push({
          label: labels[key] || key.replaceAll('_', ' '),
          value: typeof value === 'number' && ['cost_per_purchase_unit','selling_price','estimated_recipe_cost_per_unit','estimated_margin_per_unit','total_purchase','average_purchase','revenue','latest_historical_unit_cost','unpaid_product_value','unpaid_product_retail_value','unpaid_product_cost_value','gross_profit'].includes(key) ? this.money(value) : (Array.isArray(value) ? value.join(', ') || '—' : String(value ?? '—'))
        });
      }
      return cards.slice(0, 8);
    }
  };
}
