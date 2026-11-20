"""Business-owned display labels; permission, URL and model keys never change."""

MENU_LABELS = {
    "dashboard": "Dashboard",
    "tour": "Tour INPROFIC",
    "assistant": "Assistant",
    "daily_brief": "Daily Brief",
    "operations": "Operations",
    "sales_finance": "Sales & Finance",
    "commerce_delivery": "Commerce & Delivery",
    "reports_administration": "Reports & Administration",
    "inventory": "Inventory",
    "market_stock": "Stock Distribution",
    "procurement_list": "Purchases & Expenses",
    "retail_workspace": "Branches & Cashiers",
    "retail_expiration": "Expiration Check",
    "retail_quotations": "Quotations",
    "retail_promotions": "Product Promotions",
    "retail_profile": "My Profile",
    "finance_dashboard": "Finance",
    "books_home": "Books",
    "tax_home": "Tax",
    "commerce_storefront_pos": "In-Premise POS",
    "commerce_dashboard": "Commerce",
    "delivery_dashboard": "Delivery",
    "delivery_rider_dashboard": "My Deliveries",
    "reports": "Reports",
    "audit_workspace": "Audit Workspace",
    "users_list": "Users & Access",
    "subscription_plans": "Plan & Billing",
    "platform_mailing_workspace": "Project Mailing",
    "ledger_workspace": "Ledger Cut-Over",
    "business_settings": "Business Settings",
    "expenses": "Expenses",
    "production": "Production",
    "procurement": "Procurement",
}
VERTICAL_LABELS = (
    "product_label",
    "recipe_label",
    "recipe_item_label",
    "production_inputs_label",
    "orders",
    "runs",
    "batches",
    "customers",
    "sales",
    "stock_location",
    "kitchen_label",
)
MENU_VERTICAL = {
    "orders_list": "orders",
    "production_runs": "runs",
    "production_batches": "batches",
    "customer_list": "customers",
    "sales_list": "sales",
    "kitchen": "kitchen_label",
}


def overrides(business):
    if not business:
        return {}
    values = {}
    if business.headquarters_id:
        values.update(
            (business.headquarters.label_overrides or {}).get(business.vertical, {})
        )
    values.update((business.label_overrides or {}).get(business.vertical, {}))
    return values


def labels(business):
    from .verticals import vertical_config

    config = vertical_config(business)
    result = dict(MENU_LABELS)
    result.update({key: config.get(key, "Production Entry") for key in VERTICAL_LABELS})
    if config["uses_production"]:
        result["market_stock"] = "Market Stock Distribution"
    result.update(
        {key: value for key, value in overrides(business).items() if key in result}
    )
    result.update({key: result[value] for key, value in MENU_VERTICAL.items()})
    return result


def label_text(text, business):
    """Resolve known UI labels, leaving customer content and unknown text intact."""
    from .verticals import VERTICAL_CONFIG

    aliases = {value.casefold(): key for key, value in MENU_LABELS.items()}
    aliases.update(
        {
            "customer quotations": "retail_quotations",
            "user management": "users_list",
            "market stock distribution": "market_stock",
            "production orders": "orders",
            "shared runs": "runs",
            "production batches": "batches",
        }
    )
    for config in VERTICAL_CONFIG.values():
        aliases.update(
            {
                str(config[key]).casefold(): key
                for key in VERTICAL_LABELS
                if key in config
            }
        )
    aliases.update(
        {
            "sales": "sales",
            "production": "production",
            "procurement": "procurement",
            "user management": "users_list",
            "pos": "commerce_storefront_pos",
        }
    )
    key = aliases.get(str(text).casefold())
    return labels(business).get(key, text) if key else text
