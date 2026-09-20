"""Presentation and workflow vocabulary for supported business verticals.

Stored model keys remain stable across verticals. Only labels and small
workflow preferences vary, which keeps reports and existing data compatible.
"""

from .models import Business


VERTICAL_CONFIG = {
    Business.VERTICAL_BAKERY: {
        "uses_production": True,
        "procures_finished_goods": False,
        "direct_sale_channel": "physical_store",
        "inventory_primary": "raw",
        "product_label": "Finished Goods",
        "recipe_label": "Recipe",
        "recipe_item_label": "Ingredient",
        "production_inputs_label": "Production Inputs",
        "process_label": "Bake / Prepare",
        "platform_label": "Bakery operations",
        "orders": "Orders",
        "runs": "Shared Runs",
        "batches": "Production Batches",
        "customers": "Customers",
        "sales": "Sales",
        "stock_location": "Physical Store",
        "sale_intro": "Physical Store Stock only — immediate, from what's already on the Shelf.",
        "order_types": [
            ("distribution", "Distribution Order"),
            ("online", "Online Order"),
            ("physical_store", "Physical Store Order"),
        ],
        "commerce_channels": {
            "physical_store": "Physical Store / Pickup",
            "online": "Online Order",
            "distribution": "Distribution Order",
        },
        "storefront": {
            "eyebrow": "Freshly baked · Easy ordering",
            "headline": "Baked fresh for every moment.",
            "intro": "Choose a favourite, select the ordering option that suits you, and we will take it from there.",
            "range_label": "Fresh from our bakery",
            "range_heading": "Something delicious awaits",
            "all_label": "All bakes",
            "immediate_order": "Ready for pickup",
            "prepared_order": "Baked for your order",
        },
    },
    Business.VERTICAL_RESTAURANT: {
        "uses_production": True,
        "procures_finished_goods": False,
        "direct_sale_channel": "physical_store",
        "inventory_primary": "raw",
        "product_label": "Menu Items",
        "recipe_label": "Recipe",
        "recipe_item_label": "Ingredient",
        "production_inputs_label": "Kitchen Inputs",
        "process_label": "Prepare / Cook",
        "platform_label": "Restaurant operations",
        "orders": "Kitchen Orders",
        "runs": "Prep Runs",
        "batches": "Prep Batches",
        "customers": "Guests & Customers",
        "sales": "POS Sales",
        "stock_location": "Kitchen / Counter",
        "sale_intro": "Record dine-in, takeaway, or delivery service from available prepared stock.",
        "order_types": [
            ("distribution", "Catering / Bulk Order"),
            ("online", "Delivery / Online Order"),
            ("physical_store", "Kitchen / Counter Replenishment"),
        ],
        "commerce_channels": {
            "physical_store": "Counter / Pickup",
            "online": "Delivery / Online Order",
            "distribution": "Catering / Bulk Order",
        },
        "storefront": {
            "eyebrow": "Made with care · Easy ordering",
            "headline": "Good food, made for your moment.",
            "intro": "Explore the menu and choose the pickup, delivery, or catering option that suits your occasion.",
            "range_label": "Explore the menu",
            "range_heading": "What are you craving?",
            "all_label": "Full menu",
            "immediate_order": "Ready menu order",
            "prepared_order": "Prepared for your order",
        },
    },
    Business.VERTICAL_GENERAL: {
        "uses_production": True,
        "procures_finished_goods": False,
        "direct_sale_channel": "physical_store",
        "inventory_primary": "raw",
        "product_label": "Finished Goods",
        "recipe_label": "Formula / BOM",
        "recipe_item_label": "Component",
        "production_inputs_label": "Production Inputs",
        "process_label": "Manufacture / Assemble",
        "platform_label": "Production operations",
        "orders": "Production Orders",
        "runs": "Production Runs",
        "batches": "Production Batches",
        "customers": "Customers",
        "sales": "Sales",
        "stock_location": "Finished Goods Store",
        "sale_intro": "Record an immediate sale from available finished-goods stock.",
        "order_types": [
            ("distribution", "Wholesale / Customer Order"),
            ("online", "Online Order"),
            ("physical_store", "Stock Replenishment Order"),
        ],
        "commerce_channels": {
            "physical_store": "Direct / Store Order",
            "online": "Online Order",
            "distribution": "Wholesale / Customer Order",
        },
        "storefront": {
            "eyebrow": "Made with purpose · Easy ordering",
            "headline": "Made well. Made for you.",
            "intro": "Explore our products and choose the ordering option that best fits what you need.",
            "range_label": "Our product range",
            "range_heading": "Find the right fit",
            "all_label": "All products",
            "immediate_order": "Order from current stock",
            "prepared_order": "Made for your order",
        },
    },
    Business.VERTICAL_WHOLESALE: {
        "uses_production": False,
        "procures_finished_goods": True,
        "direct_sale_channel": "distribution",
        "inventory_primary": "finished",
        "product_label": "Stock Products",
        "recipe_label": "Product Specification",
        "recipe_item_label": "Purchased Product",
        "production_inputs_label": "Supplier Inputs",
        "process_label": "Receive / Put Away",
        "platform_label": "Wholesale operations",
        "orders": "Stock Orders",
        "runs": "Stock Runs",
        "batches": "Stock Batches",
        "customers": "Trade Customers",
        "sales": "Wholesale Sales",
        "stock_location": "Warehouse Stock",
        "sale_intro": "Release available warehouse stock to a trade customer, with agreed pricing and credit terms when applicable.",
        "order_types": [
            ("distribution", "Wholesale Order"),
            ("online", "Online Trade Order"),
            ("physical_store", "Warehouse Replenishment"),
        ],
        "commerce_channels": {
            "physical_store": "Direct Warehouse Order",
            "online": "Online Trade Order",
            "distribution": "Wholesale Order",
        },
        "storefront": {
            "eyebrow": "Reliable supply · Straightforward ordering",
            "headline": "Keep your shelves moving.",
            "intro": "Browse dependable stock, compare trade options, and place the order your business needs.",
            "range_label": "Wholesale selection",
            "range_heading": "Built for better buying",
            "all_label": "All stock",
            "immediate_order": "Order from warehouse stock",
            "prepared_order": "Order from warehouse stock",
        },
    },
    Business.VERTICAL_RETAIL: {
        "uses_production": False,
        "procures_finished_goods": True,
        "direct_sale_channel": "physical_store",
        "inventory_primary": "finished",
        "product_label": "Stock Products",
        "recipe_label": "Product Specification",
        "recipe_item_label": "Purchased Product",
        "production_inputs_label": "Supplier Inputs",
        "process_label": "Receive / Shelve",
        "platform_label": "Retail operations",
        "orders": "Stock Orders",
        "runs": "Stock Runs",
        "batches": "Stock Batches",
        "customers": "Customers",
        "sales": "Retail Sales",
        "stock_location": "Shop Stock",
        "sale_intro": "Record an immediate retail sale from stock currently available in the shop.",
        "order_types": [
            ("distribution", "Bulk Customer Order"),
            ("online", "Online Order"),
            ("physical_store", "Shop Replenishment"),
        ],
        "commerce_channels": {
            "physical_store": "Retail / Pickup Order",
            "online": "Online Order",
            "distribution": "Bulk Customer Order",
        },
        "storefront": {
            "eyebrow": "Easy shopping · Flexible ordering",
            "headline": "Find your next favourite.",
            "intro": "Browse our range, choose how you would like to receive it, and send your order with ease.",
            "range_label": "Shop the collection",
            "range_heading": "Picked for you",
            "all_label": "All products",
            "immediate_order": "Order from shop stock",
            "prepared_order": "Order from shop stock",
        },
    },
}


POS_CONFIG = {
    Business.VERTICAL_RESTAURANT: {
        "eyebrow": "In-premise service",
        "title": "Restaurant POS",
        "intro": "Build dine-in, takeaway and delivery orders from the live menu, then complete payment at the counter.",
        "catalog_label": "Menu",
        "basket_label": "Current order",
        "search_placeholder": "Search menu items…",
        "customer_label": "Guest / customer",
        "customer_default": "Walk-in Guest",
        "checkout_label": "Complete order",
        "empty_label": "No published counter menu items are available.",
        "service_modes": [("dine_in", "Dine-in"), ("takeaway", "Takeaway / pickup"), ("delivery", "Delivery")],
        "service_mode_label": "Service mode",
        "show_service_mode": True,
        "show_reference": True,
        "reference_label": "Table / service reference",
        "reference_placeholder": "e.g. Table 8 or Patio A",
    },
    Business.VERTICAL_WHOLESALE: {
        "eyebrow": "Trade counter",
        "title": "Wholesale Counter",
        "intro": "Build trade orders from available warehouse stock using wholesale pricing and configured minimum quantities.",
        "catalog_label": "Trade products",
        "basket_label": "Trade order",
        "search_placeholder": "Search stock products…",
        "customer_label": "Trade customer",
        "customer_default": "Counter Trade Customer",
        "checkout_label": "Complete trade sale",
        "empty_label": "No published wholesale products are available for counter sale.",
        "service_modes": [],
        "service_mode_label": "",
        "show_service_mode": False,
        "show_reference": False,
        "reference_label": "",
        "reference_placeholder": "",
    },
    Business.VERTICAL_RETAIL: {
        "eyebrow": "In-store selling",
        "title": "Retail POS",
        "intro": "Ring up walk-in purchases from live shop stock with fast product search, basket controls and configured payment methods.",
        "catalog_label": "Products",
        "basket_label": "Basket",
        "search_placeholder": "Search products…",
        "customer_label": "Customer",
        "customer_default": "Walk-in Customer",
        "checkout_label": "Complete sale",
        "empty_label": "No published retail products are available for in-store sale.",
        "service_modes": [],
        "service_mode_label": "",
        "show_service_mode": False,
        "show_reference": False,
        "reference_label": "",
        "reference_placeholder": "",
    },
    Business.VERTICAL_BAKERY: {
        "eyebrow": "Counter selling",
        "title": "Bakery Counter",
        "intro": "Sell ready bakery items from current counter stock with a fast basket and payment workflow.",
        "catalog_label": "Bakes",
        "basket_label": "Current sale",
        "search_placeholder": "Search bakes…",
        "customer_label": "Customer",
        "customer_default": "Walk-in Customer",
        "checkout_label": "Complete sale",
        "empty_label": "No published counter products are available.",
        "service_modes": [],
        "service_mode_label": "",
        "show_service_mode": False,
        "show_reference": False,
        "reference_label": "",
        "reference_placeholder": "",
    },
    Business.VERTICAL_GENERAL: {
        "eyebrow": "Direct product sales",
        "title": "Product Sales POS",
        "intro": "Sell available finished goods directly from stock without leaving the operational workspace.",
        "catalog_label": "Sellable products",
        "basket_label": "Current sale",
        "search_placeholder": "Search finished goods…",
        "customer_label": "Customer",
        "customer_default": "Walk-in Customer",
        "checkout_label": "Complete sale",
        "empty_label": "No published finished goods are available for direct sale.",
        "service_modes": [],
        "service_mode_label": "",
        "show_service_mode": False,
        "show_reference": False,
        "reference_label": "",
        "reference_placeholder": "",
    },
}


PRODUCT_SOURCE_LABELS = {
    Business.VERTICAL_RESTAURANT: {
        "made_in_house": "Prepared in-house",
        "purchased_for_resale": "Purchased for resale",
        "resale_group": "Purchased drinks / packaged resale items",
    },
    Business.VERTICAL_BAKERY: {
        "made_in_house": "Baked / made in-house",
        "purchased_for_resale": "Purchased for resale",
        "resale_group": "Purchased resale products",
    },
    Business.VERTICAL_GENERAL: {
        "made_in_house": "Made / assembled in-house",
        "purchased_for_resale": "Purchased for resale",
        "resale_group": "Purchased resale products",
    },
    Business.VERTICAL_WHOLESALE: {
        "made_in_house": "Not used for this vertical",
        "purchased_for_resale": "Purchased stock",
        "resale_group": "Stock products",
    },
    Business.VERTICAL_RETAIL: {
        "made_in_house": "Not used for this vertical",
        "purchased_for_resale": "Purchased stock",
        "resale_group": "Stock products",
    },
}


def vertical_config(business):
    key = getattr(business, "vertical", Business.VERTICAL_BAKERY)
    config = dict(VERTICAL_CONFIG.get(key, VERTICAL_CONFIG[Business.VERTICAL_GENERAL]))
    config["pos"] = POS_CONFIG.get(key, POS_CONFIG[Business.VERTICAL_GENERAL])
    config["product_sources"] = PRODUCT_SOURCE_LABELS.get(key, PRODUCT_SOURCE_LABELS[Business.VERTICAL_GENERAL])
    return config


def bulk_package_type_choices(business):
    """Customer-facing bulk container/pack vocabulary by business vertical.

    Stored values remain simple stable strings so changing labels later does
    not rewrite historical orders.  A custom option remains available for
    businesses whose packaging is not represented by the common list.
    """
    common = [
        ("pack", "Pack"), ("box", "Box"), ("carton", "Carton"),
        ("crate", "Crate"), ("tray", "Tray"), ("bundle", "Bundle"),
        ("bag", "Bag"), ("set", "Set"), ("container", "Container"),
        ("custom", "Other / custom"),
    ]
    vertical = getattr(business, "vertical", None)
    if vertical == Business.VERTICAL_RESTAURANT:
        return [
            ("bowl", "Bowl"), ("tray", "Tray"), ("platter", "Platter"),
            ("pan", "Pan"), ("bucket", "Bucket"), ("food_pack", "Food pack"),
            ("box", "Box"), ("cooler", "Cooler"), ("bottle", "Bottle"),
            ("jug", "Jug"), ("container", "Container"), ("custom", "Other / custom"),
        ]
    if vertical == Business.VERTICAL_BAKERY:
        return [
            ("box", "Box"), ("tray", "Tray"), ("pack", "Pack"),
            ("carton", "Carton"), ("dozen", "Dozen"), ("crate", "Crate"),
            ("bag", "Bag"), ("basket", "Basket"), ("bundle", "Bundle"),
            ("custom", "Other / custom"),
        ]
    if vertical == Business.VERTICAL_WHOLESALE:
        return [
            ("carton", "Carton"), ("case", "Case"), ("crate", "Crate"),
            ("pallet", "Pallet"), ("sack", "Sack"), ("bag", "Bag"),
            ("bundle", "Bundle"), ("drum", "Drum"), ("box", "Box"),
            ("pack", "Pack"), ("custom", "Other / custom"),
        ]
    if vertical == Business.VERTICAL_RETAIL:
        return [
            ("pack", "Pack"), ("box", "Box"), ("carton", "Carton"),
            ("case", "Case"), ("bundle", "Bundle"), ("bag", "Bag"),
            ("set", "Set"), ("crate", "Crate"), ("custom", "Other / custom"),
        ]
    return common
