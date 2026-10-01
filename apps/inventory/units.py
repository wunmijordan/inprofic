"""Shared raw-material unit catalogue.

The catalogue powers both the searchable unit inputs and the conversion helper.
Physical factors are expressed against one base unit per dimension. Packaging and
local measured units intentionally use the ``custom`` dimension because they do
not have a universal conversion ratio.
"""

UNIT_GROUPS = [
    (
        "Mass / weight",
        [
            ("mcg", "Microgram (µg)", "mass", 0.000000001),
            ("mg", "Milligram (mg)", "mass", 0.000001),
            ("g", "Gram (g)", "mass", 0.001),
            ("kg", "Kilogram (kg)", "mass", 1),
            ("t", "Metric tonne (t)", "mass", 1000),
            ("oz", "Ounce (oz)", "mass", 0.028349523125),
            ("lb", "Pound (lb)", "mass", 0.45359237),
            ("st", "Stone (st)", "mass", 6.35029318),
            ("short_ton", "US short ton", "mass", 907.18474),
            ("long_ton", "Imperial long ton", "mass", 1016.0469088),
        ],
    ),
    (
        "Volume / capacity",
        [
            ("ul", "Microlitre (µL)", "volume", 0.000001),
            ("ml", "Millilitre (mL)", "volume", 0.001),
            ("cl", "Centilitre (cL)", "volume", 0.01),
            ("dl", "Decilitre (dL)", "volume", 0.1),
            ("l", "Litre (L)", "volume", 1),
            ("m3", "Cubic metre (m³)", "volume", 1000),
            ("tsp_us", "Teaspoon (US)", "volume", 0.00492892159375),
            ("tbsp_us", "Tablespoon (US)", "volume", 0.01478676478125),
            ("floz_us", "Fluid ounce (US)", "volume", 0.0295735295625),
            ("cup_us", "Cup (US)", "volume", 0.2365882365),
            ("pt_us", "Pint (US)", "volume", 0.473176473),
            ("qt_us", "Quart (US)", "volume", 0.946352946),
            ("gal_us", "Gallon (US)", "volume", 3.785411784),
            ("tsp_imp", "Teaspoon (Imperial)", "volume", 0.005919388020833),
            ("tbsp_imp", "Tablespoon (Imperial)", "volume", 0.0177581640625),
            ("floz_imp", "Fluid ounce (Imperial)", "volume", 0.0284130625),
            ("pt_imp", "Pint (Imperial)", "volume", 0.56826125),
            ("qt_imp", "Quart (Imperial)", "volume", 1.1365225),
            ("gal_imp", "Gallon (Imperial)", "volume", 4.54609),
            ("cu_in", "Cubic inch", "volume", 0.016387064),
            ("cu_ft", "Cubic foot", "volume", 28.316846592),
        ],
    ),
    (
        "Count",
        [
            ("piece", "Piece / each", "count", 1),
            ("pair", "Pair", "count", 2),
            ("dozen", "Dozen", "count", 12),
            ("score", "Score", "count", 20),
            ("gross", "Gross", "count", 144),
            ("ream", "Ream (500)", "count", 500),
            ("thousand", "Thousand", "count", 1000),
        ],
    ),
    (
        "Length",
        [
            ("mm", "Millimetre (mm)", "length", 0.001),
            ("cm", "Centimetre (cm)", "length", 0.01),
            ("m", "Metre (m)", "length", 1),
            ("km", "Kilometre (km)", "length", 1000),
            ("in", "Inch (in)", "length", 0.0254),
            ("ft", "Foot (ft)", "length", 0.3048),
            ("yd", "Yard (yd)", "length", 0.9144),
            ("mi", "Mile (mi)", "length", 1609.344),
        ],
    ),
    (
        "Area",
        [
            ("mm2", "Square millimetre", "area", 0.000001),
            ("cm2", "Square centimetre", "area", 0.0001),
            ("m2", "Square metre", "area", 1),
            ("ha", "Hectare", "area", 10000),
            ("km2", "Square kilometre", "area", 1000000),
            ("in2", "Square inch", "area", 0.00064516),
            ("ft2", "Square foot", "area", 0.09290304),
            ("yd2", "Square yard", "area", 0.83612736),
            ("acre", "Acre", "area", 4046.8564224),
        ],
    ),
    (
        "Packaging / containers",
        [
            ("bag", "Bag", "custom", 1),
            ("sack", "Sack", "custom", 1),
            ("carton", "Carton", "custom", 1),
            ("box", "Box", "custom", 1),
            ("case", "Case", "custom", 1),
            ("crate", "Crate", "custom", 1),
            ("pack", "Pack", "custom", 1),
            ("packet", "Packet", "custom", 1),
            ("sachet", "Sachet", "custom", 1),
            ("pouch", "Pouch", "custom", 1),
            ("bottle", "Bottle", "custom", 1),
            ("jar", "Jar", "custom", 1),
            ("can", "Can", "custom", 1),
            ("tin", "Tin", "custom", 1),
            ("tube", "Tube", "custom", 1),
            ("vial", "Vial", "custom", 1),
            ("ampoule", "Ampoule", "custom", 1),
            ("drum", "Drum", "custom", 1),
            ("barrel", "Barrel", "custom", 1),
            ("keg", "Keg", "custom", 1),
            ("bucket", "Bucket", "custom", 1),
            ("pail", "Pail", "custom", 1),
            ("tank", "Tank", "custom", 1),
            ("tote", "Tote / IBC", "custom", 1),
            ("pallet", "Pallet", "custom", 1),
            ("tray", "Tray", "custom", 1),
            ("bowl", "Bowl", "custom", 1),
            ("tub", "Tub", "custom", 1),
            ("roll", "Roll", "custom", 1),
            ("coil", "Coil", "custom", 1),
            ("spool", "Spool", "custom", 1),
            ("bobbin", "Bobbin", "custom", 1),
            ("bundle", "Bundle", "custom", 1),
            ("bale", "Bale", "custom", 1),
            ("sheet", "Sheet", "custom", 1),
            ("strip", "Strip", "custom", 1),
            ("slab", "Slab", "custom", 1),
            ("block", "Block", "custom", 1),
            ("cylinder", "Cylinder", "custom", 1),
        ],
    ),
    (
        "Operational / local measured units",
        [
            ("spoon", "Spoon (measured locally)", "custom", 1),
            ("scoop", "Scoop", "custom", 1),
            ("cap", "Cap / capful", "custom", 1),
            ("ladle", "Ladle", "custom", 1),
            ("serving", "Serving", "custom", 1),
            ("portion", "Portion", "custom", 1),
            ("dose", "Dose", "custom", 1),
            ("tablet", "Tablet", "custom", 1),
            ("capsule", "Capsule", "custom", 1),
            ("drop", "Drop", "custom", 1),
            ("pump", "Pump", "custom", 1),
            ("shot", "Shot", "custom", 1),
            ("custom", "Custom measured unit", "custom", 1),
        ],
    ),
]

UNIT_ALIASES = {
    "µg": "mcg",
    "ug": "mcg",
    "microgram": "mcg",
    "micrograms": "mcg",
    "milligram": "mg",
    "milligrams": "mg",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kgs": "kg",
    "ton": "t",
    "tons": "t",
    "tonne": "t",
    "tonnes": "t",
    "ounce": "oz",
    "ounces": "oz",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
    "liter": "l",
    "litre": "l",
    "litres": "l",
    "liters": "l",
    "ltr": "l",
    "ltrs": "l",
    "milliliter": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "milliliters": "ml",
    "centilitre": "cl",
    "centiliter": "cl",
    "microlitre": "ul",
    "microliter": "ul",
    "gallon": "gal_us",
    "gallons": "gal_us",
    "us_gallon": "gal_us",
    "imperial_gallon": "gal_imp",
    "cup": "cup_us",
    "cups": "cup_us",
    "pint": "pt_us",
    "pints": "pt_us",
    "quart": "qt_us",
    "quarts": "qt_us",
    "teaspoon": "tsp_us",
    "teaspoons": "tsp_us",
    "tablespoon": "tbsp_us",
    "tablespoons": "tbsp_us",
    "pcs": "piece",
    "pc": "piece",
    "pieces": "piece",
    "each": "piece",
    "pairs": "pair",
    "dozens": "dozen",
    "meter": "m",
    "metre": "m",
    "meters": "m",
    "metres": "m",
    "feet": "ft",
    "foot": "ft",
    "inches": "in",
    "inch": "in",
    "yard": "yd",
    "yards": "yd",
}


def unit_datalist_options():
    """Return a de-duplicated flat list for native searchable datalists."""
    seen = set()
    options = []
    for group_label, rows in UNIT_GROUPS:
        for value, label, _dimension, _factor in rows:
            if value in seen:
                continue
            seen.add(value)
            options.append({"value": value, "label": label, "group": group_label})
    return options


def unit_catalog_payload():
    """JSON-serialisable catalogue consumed by the conversion modal."""
    return {
        "groups": [
            {
                "label": group_label,
                "units": [
                    {
                        "key": value,
                        "label": label,
                        "dimension": dimension,
                        "factor": factor,
                    }
                    for value, label, dimension, factor in rows
                ],
            }
            for group_label, rows in UNIT_GROUPS
        ],
        "aliases": UNIT_ALIASES,
    }
