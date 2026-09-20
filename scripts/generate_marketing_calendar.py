#!/usr/bin/env python3
"""Generate INPROFIC's Q4 2026 social/ad calendar from the feature catalogue.

The feature catalogue is the single source of truth. This command regenerates the
CSV execution calendar, Markdown summary, and the styled Excel workbook together
so those deliverables cannot silently drift apart.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docs" / "feature_marketing_catalog.json"
CSV_PATH = ROOT / "docs" / "MARKETING_CONTENT_CALENDAR_Q4_2026.csv"
MD_PATH = ROOT / "docs" / "MARKETING_CONTENT_CALENDAR_Q4_2026.md"
XLSX_PATH = ROOT / "docs" / "INPROFIC_Q4_2026_Content_Calendar.xlsx"

FORMATS = ["Reel / short demo", "Carousel", "Static graphic", "Screen-record demo", "Story / poll", "Founder post", "Paid ad creative"]
CHANNELS = ["Instagram, Facebook", "Instagram, LinkedIn", "LinkedIn, Facebook", "Instagram, TikTok", "Instagram Stories, Facebook Stories", "LinkedIn, Facebook", "Instagram, Facebook, LinkedIn"]
PROMOTION = ["Organic", "Organic + boost", "Organic", "Organic + paid", "Organic", "Organic", "Paid prospecting + retargeting"]

WEEKLY_LENS = {
    0: "Problem / pain point",
    1: "How it works",
    2: "Screen / workflow demo",
    3: "Connected-system handoff",
    4: "Control / verification",
    5: "Persona / use case",
    6: "Recap / CTA",
}

ANGLE_BY_MONTH = {
    10: ("Feature education", "Show exactly what the feature does and the workflow it replaces."),
    11: ("Problem → workflow", "Start from a familiar operational problem, then show the INPROFIC workflow that resolves it."),
    12: ("Control / ROI / readiness", "Frame the feature around cleaner controls, fewer manual gaps and readiness for the next operating period without inventing savings claims."),
}

LAUNCH = [
    ("INPROFIC launch", "Meet INPROFIC: one operating system for stock, production, sales, finance and commerce.", "See the full product tour", "Brand reveal + fast cuts across Inventory, Sales, Commerce, Delivery and Finance."),
    ("Who INPROFIC is for", "Bakery, restaurant, production, wholesale or retail: start with the workflow your business actually runs.", "Choose your operating profile", "Five business archetypes flowing into one INPROFIC workspace."),
    ("One source of operational truth", "Stop rebuilding the same order in separate stock, finance and fulfilment tools.", "Explore connected workflows", "Animated order moving through stock, payment and fulfilment."),
    ("Commerce to operations", "A customer checkout should become the right internal record only after verified payment.", "See payment-first checkout", "Storefront basket → payment → order → delivery timeline."),
    ("Production to finance", "Production, stock and money should agree without turning every manager into a spreadsheet reconciler.", "Watch the production flow", "Recipe release → output → sale/receivable → finance ledger."),
    ("Control without complexity", "Roles, audit trails and tenant boundaries can be strong without making the interface feel like engineering software.", "Book a guided walkthrough", "Role cards + audit evidence + clean dashboard."),
    ("Launch week recap", "INPROFIC is live: inventory, procurement, production, sales, finance, commerce, delivery, audit and more in one tenant-safe platform.", "Start your INPROFIC setup", "Launch-week montage with feature labels and CTA."),
]


def load_catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def week_number(start, current):
    return ((current - start).days // 7) + 1


def make_row(current, idx, feature, launch=False):
    if launch:
        theme, hook, cta, creative = LAUNCH[idx]
        return {
            "Date": current.isoformat(), "Day": current.strftime("%A"), "Campaign Week": "Launch week",
            "Feature Group": "launch", "Weekly Lens": "Launch story",
            "Phase": "Launch", "Funnel Goal": "Awareness / consideration", "Feature / Theme": theme,
            "Angle": "Launch story", "Format": FORMATS[idx % len(FORMATS)], "Promotion": PROMOTION[idx % len(PROMOTION)],
            "Channels": CHANNELS[idx % len(CHANNELS)], "Audience": "Owners and operating teams",
            "Hook": hook, "Core Message": hook, "CTA": cta, "Creative Brief": creative,
            "New Feature Highlight": "LAUNCH", "Feature ID": "launch", "Added On": "2026-10-01",
            "Status": "Planned", "Owner": "", "Asset / Draft Link": "",
        }
    angle, angle_note = ANGLE_BY_MONTH[current.month]
    new = feature.get("highlight") == "NEW"
    prefix = "✨ NEW — " if new else ""
    format_name = FORMATS[idx % len(FORMATS)]
    hook_templates = {
        10: f"{prefix}{feature['name']}: what changes when this is handled inside one connected system?",
        11: f"Still solving this manually? {prefix}{feature['name']} keeps the workflow connected instead of handing it off to another spreadsheet.",
        12: f"Year-end control check: {prefix}{feature['name']} gives the next operating period cleaner evidence and fewer disconnected handoffs.",
    }
    cta = "See how it works" if current.month == 10 else "Map this to your workflow" if current.month == 11 else "Prepare your 2027 workflow"
    creative = f"Use a real INPROFIC screen/demo for {feature['name']}. Show one before/after workflow, then one concrete proof point; avoid fabricated metrics."
    return {
        "Date": current.isoformat(), "Day": current.strftime("%A"), "Campaign Week": f"Week {week_number(date(2026,10,1), current)}",
        "Feature Group": feature.get("group", "product"), "Weekly Lens": WEEKLY_LENS[current.weekday()],
        "Phase": "Feature education" if current.month == 10 else "Workflow proof" if current.month == 11 else "Year-end readiness",
        "Funnel Goal": "Awareness" if idx % 3 == 0 else "Consideration" if idx % 3 == 1 else "Conversion support",
        "Feature / Theme": f"{prefix}{feature['name']}", "Angle": angle, "Format": format_name,
        "Promotion": PROMOTION[idx % len(PROMOTION)], "Channels": CHANNELS[idx % len(CHANNELS)],
        "Audience": feature["audience"], "Hook": hook_templates[current.month],
        "Core Message": f"{angle_note} Proof point: {feature['proof']}", "CTA": cta, "Creative Brief": creative,
        "New Feature Highlight": feature.get("highlight", ""), "Feature ID": feature["id"], "Added On": feature.get("added_on", ""),
        "Status": "Planned", "Owner": "", "Asset / Draft Link": "",
    }


def build_rows(features):
    start, end = date(2026, 10, 1), date(2026, 12, 31)
    rows = []
    current = start
    feature_index = 0
    while current <= end:
        offset = (current - start).days
        if offset < 7:
            rows.append(make_row(current, offset, None, launch=True))
        else:
            # Round-robin across the complete catalogue. This naturally repeats
            # each capability with October/November/December's different angle.
            feature = features[feature_index % len(features)]
            rows.append(make_row(current, feature_index, feature))
            feature_index += 1
        current += timedelta(days=1)
    return rows


def write_csv(rows):
    headers = list(rows[0])
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(features):
    new_features = [feature for feature in features if feature.get("highlight") == "NEW"]
    MD_PATH.write_text(
        "# INPROFIC Q4 2026 Social Media Ads & Content Calendar\n\n"
        "Generated from `docs/feature_marketing_catalog.json` by `scripts/generate_marketing_calendar.py`. "
        "The command keeps the detailed CSV, this Markdown summary and `docs/INPROFIC_Q4_2026_Content_Calendar.xlsx` synchronized.\n\n"
        "## Campaign structure\n\n"
        "- **1–7 October:** launch week — brand reveal, audience fit, connected workflow story and product tour.\n"
        "- **8–31 October:** feature education — what each capability does.\n"
        "- **November:** problem → workflow — revisit the same capabilities through real operating problems and connected workflows.\n"
        "- **December:** control / ROI / readiness — revisit them around controls, evidence and next-period readiness without fabricated savings claims.\n"
        "- Every normal week uses a seven-day lens: problem, how-it-works, screen/workflow demo, connected handoff, control/verification, persona/use case, and recap/CTA.\n"
        "- Paid/boosted placements are mixed into the daily plan; every row includes feature group, weekly lens, format, channels, audience, hook, CTA and creative brief.\n\n"
        "## Newly added capabilities highlighted in this release\n\n" +
        "\n".join(f"- ✨ **{f['name']}** — added {f['added_on']}: {f['proof']}" for f in new_features) +
        "\n\n## Maintenance rule\n\n"
        "Any user-visible feature addition or material behavior change must apply the repository `release-communications` skill, update the feature catalogue and regenerate this calendar. New additions remain visibly marked `✨ NEW` in generated calendar rows and in the Excel summary/catalogue.\n",
        encoding="utf-8",
    )


def _col_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def write_xlsx(rows, features):
    # Keep the spreadsheet-only dependency out of INPROFIC's production runtime.
    # Install it once with: pip install -r requirements-marketing.txt
    os.environ.setdefault("ARTIFACT_TOOL_RPC_DAEMON_STARTUP_TIMEOUT_S", "60")
    try:
        from artifact_tool import SpreadsheetFile, Workbook
    except ImportError as exc:
        raise SystemExit(
            "Excel generation requires the marketing tooling. Run "
            "`pip install -r requirements-marketing.txt`, then rerun "
            "`python scripts/generate_marketing_calendar.py`."
        ) from exc

    workbook = Workbook.create()

    # Campaign Summary -----------------------------------------------------
    summary = workbook.worksheets.add("Campaign Summary")
    summary.get_range("A1:H1").merge()
    summary.get_range("A1").values = [["INPROFIC · Q4 2026 Launch & Social Content Calendar"]]
    summary.get_range("A1:H1").format = {
        "fill": "#111827",
        "font": {"bold": True, "color": "#FFFFFF", "size": 18},
        "vertical_alignment": "center",
    }
    summary.get_range("A1:H1").format.row_height = 34

    summary.get_range("A3:B6").values = [
        ["Metric", "Value"],
        ["Scheduled posts", None],
        ["Catalogued capability groups", None],
        ["New capabilities highlighted", None],
    ]
    summary.get_range("A3:B3").format = {"fill": "#8F172D", "font": {"bold": True, "color": "#FFFFFF"}}
    summary.get_range("B4").formulas = [["=COUNTA(Calendar!A2:A200)"]]
    summary.get_range("B5").formulas = [["=COUNTA('Feature Catalog'!A2:A200)"]]
    summary.get_range("B6").formulas = [["=COUNTIF('Feature Catalog'!F2:F200,\"NEW\")"]]

    summary.get_range("D3:E7").values = [
        ["Campaign phase", "Intent"],
        ["1–7 October", "Launch week: reveal, audience fit, connected operating story"],
        ["8–31 October", "Feature education: what each capability does"],
        ["November", "Problem → workflow: show the operational handoff it replaces"],
        ["December", "Control / ROI / readiness: evidence, control and 2027 readiness"],
    ]
    summary.get_range("D3:E3").format = {"fill": "#8F172D", "font": {"bold": True, "color": "#FFFFFF"}}
    summary.get_range("D3:E7").format.wrap_text = True

    summary.get_range("A9:G9").merge()
    summary.get_range("A9").values = [["Seven-day recurring content lens"]]
    summary.get_range("A9:G9").format = {"fill": "#F3F4F6", "font": {"bold": True, "color": "#111827", "size": 13}}
    summary.get_range("A10:G11").values = [
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        ["Problem / pain point", "How it works", "Screen / workflow demo", "Connected-system handoff", "Control / verification", "Persona / use case", "Recap / CTA"],
    ]
    summary.get_range("A10:G10").format = {"fill": "#FEF3C7", "font": {"bold": True, "color": "#78350F"}, "horizontal_alignment": "center"}
    summary.get_range("A10:G11").format.wrap_text = True

    new_features = [feature for feature in features if feature.get("highlight") == "NEW"]
    summary.get_range("A13:H13").merge()
    summary.get_range("A13").values = [["✨ New additions highlighted in this release"]]
    summary.get_range("A13:H13").format = {"fill": "#ECFDF5", "font": {"bold": True, "color": "#065F46", "size": 13}}
    if new_features:
        new_rows = [
            [feature["name"], feature["proof"], datetime.strptime(feature["added_on"], "%Y-%m-%d") if feature.get("added_on") else None]
            for feature in new_features
        ]
        new_end = 13 + len(new_rows)
        summary.get_range(f"A14:C{new_end}").values = new_rows
        summary.get_range(f"A14:C{new_end}").format.wrap_text = True
        summary.get_range(f"A14:A{new_end}").format.font = {"bold": True}
        summary.get_range(f"C14:C{new_end}").format.number_format = "yyyy-mm-dd"

    summary.get_range("A20:H20").merge()
    summary.get_range("A20").values = [[
        "Maintenance rule: whenever a user-visible INPROFIC capability is added or materially changed, "
        "update docs/feature_marketing_catalog.json and run scripts/generate_marketing_calendar.py. "
        "The CSV, Markdown and Excel outputs are regenerated together; new additions remain visibly marked ✨ NEW."
    ]]
    summary.get_range("A20:H20").format = {"fill": "#FFF7ED", "font": {"color": "#9A3412"}, "wrap_text": True}
    summary.get_range("A20:H20").format.row_height = 48
    for column, width in {"A": 28, "B": 34, "C": 16, "D": 18, "E": 48, "F": 18, "G": 18, "H": 18}.items():
        summary.get_range(f"{column}:{column}").format.column_width = width
    summary.freeze_panes.freeze_rows(1)

    # Calendar -------------------------------------------------------------
    calendar = workbook.worksheets.add("Calendar")
    headers = list(rows[0])
    matrix = [headers]
    for row in rows:
        values = []
        for header in headers:
            value = row[header]
            if header in {"Date", "Added On"} and value:
                value = datetime.strptime(value, "%Y-%m-%d")
            values.append(value)
        matrix.append(values)
    end_col = _col_letter(len(headers))
    calendar.get_range(f"A1:{end_col}{len(matrix)}").values = matrix
    calendar.tables.add(f"A1:{end_col}{len(matrix)}", True, "Q4ContentCalendar")
    calendar.get_range(f"A1:{end_col}1").format = {"fill": "#111827", "font": {"bold": True, "color": "#FFFFFF"}}
    calendar.get_range(f"A1:{end_col}{len(matrix)}").format.wrap_text = True
    calendar.freeze_panes.freeze_rows(1)
    calendar.freeze_panes.freeze_columns(3)
    calendar.get_range(f"A2:A{len(matrix)}").format.number_format = "yyyy-mm-dd"
    added_col = _col_letter(headers.index("Added On") + 1)
    calendar.get_range(f"{added_col}2:{added_col}{len(matrix)}").format.number_format = "yyyy-mm-dd"
    widths = {
        "A": 13, "B": 12, "C": 15, "D": 16, "E": 24, "F": 19, "G": 20,
        "H": 38, "I": 22, "J": 20, "K": 20, "L": 28, "M": 28, "N": 45,
        "O": 54, "P": 26, "Q": 48, "R": 18, "S": 22, "T": 13, "U": 14,
        "V": 18, "W": 32,
    }
    for column, width in widths.items():
        calendar.get_range(f"{column}:{column}").format.column_width = width
    calendar.get_range("U2:U200").data_validation = {
        "rule": {"type": "list", "values": ["Planned", "Drafting", "Ready", "Scheduled", "Published", "Paused"]}
    }
    calendar.get_range(f"R2:R{len(matrix)}").conditional_formats.add_custom(
        '=OR(R2="NEW",R2="LAUNCH")',
        {"fill": "#FEF3C7", "font": {"bold": True, "color": "#92400E"}},
    )
    calendar.get_range(f"U2:U{len(matrix)}").conditional_formats.add_custom(
        '=U2="Published"', {"fill": "#DCFCE7", "font": {"color": "#166534"}}
    )
    calendar.get_range(f"U2:U{len(matrix)}").conditional_formats.add_custom(
        '=U2="Paused"', {"fill": "#FEE2E2", "font": {"color": "#991B1B"}}
    )

    # Feature Catalog ------------------------------------------------------
    catalog = workbook.worksheets.add("Feature Catalog")
    catalog_rows = [["Feature ID", "Capability", "Group", "Audience", "Proof point", "Highlight", "Added On"]]
    for feature in features:
        catalog_rows.append([
            feature["id"], feature["name"], feature["group"], feature["audience"], feature["proof"],
            feature.get("highlight", ""),
            datetime.strptime(feature["added_on"], "%Y-%m-%d") if feature.get("added_on") else None,
        ])
    catalog.get_range(f"A1:G{len(catalog_rows)}").values = catalog_rows
    catalog.tables.add(f"A1:G{len(catalog_rows)}", True, "FeatureCatalog")
    catalog.get_range("A1:G1").format = {"fill": "#111827", "font": {"bold": True, "color": "#FFFFFF"}}
    catalog.get_range(f"A1:G{len(catalog_rows)}").format.wrap_text = True
    catalog.freeze_panes.freeze_rows(1)
    for column, width in {"A": 24, "B": 42, "C": 18, "D": 34, "E": 65, "F": 12, "G": 13}.items():
        catalog.get_range(f"{column}:{column}").format.column_width = width
    catalog.get_range(f"G2:G{len(catalog_rows)}").format.number_format = "yyyy-mm-dd"
    catalog.get_range(f"F2:F{len(catalog_rows)}").conditional_formats.add_custom(
        '=F2="NEW"', {"fill": "#D1FAE5", "font": {"bold": True, "color": "#065F46"}}
    )

    # Execution Guide ------------------------------------------------------
    guide = workbook.worksheets.add("Execution Guide")
    guide.get_range("A1:F1").merge()
    guide.get_range("A1").values = [["INPROFIC content execution guide"]]
    guide.get_range("A1:F1").format = {"fill": "#8F172D", "font": {"bold": True, "color": "#FFFFFF", "size": 16}}
    guide.get_range("A3:B10").values = [
        ["Rule", "How to use it"],
        ["Product truth first", "Use real INPROFIC behavior/screens. Do not promote roadmap items as shipped."],
        ["Launch week", "1–7 October focuses on brand reveal, audience fit and connected operating workflows."],
        ["October", "Educate: explain what the feature is and how it works."],
        ["November", "Problem → workflow: lead with an operational problem, then show the INPROFIC flow."],
        ["December", "Control / readiness: emphasize evidence, fewer disconnected handoffs and 2027 preparedness; do not invent ROI numbers."],
        ["New additions", "Keep ✨ NEW visible on new capabilities until the release-highlight window is intentionally retired."],
        ["Regenerate", "Update docs/feature_marketing_catalog.json, then run: python scripts/generate_marketing_calendar.py"],
    ]
    guide.get_range("A3:B3").format = {"fill": "#111827", "font": {"bold": True, "color": "#FFFFFF"}}
    guide.get_range("A3:B10").format.wrap_text = True
    guide.get_range("A:A").format.column_width = 28
    guide.get_range("B:B").format.column_width = 90
    guide.freeze_panes.freeze_rows(1)

    # Compact verification before export. If the workbook ever develops an
    # invalid formula, fail the generator rather than silently shipping it.
    errors = workbook.inspect({
        "kind": "match",
        "search_term": "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
        "options": {"use_regex": True, "max_results": 100},
        "summary": "marketing workbook formula error scan",
    })
    if '"matched":true' in errors.ndjson.replace(" ", "").lower():
        raise RuntimeError(f"Generated workbook contains formula errors: {errors.ndjson}")

    SpreadsheetFile.export_xlsx(workbook).save(str(XLSX_PATH))


def main():
    # Import/check the spreadsheet dependency before writing any generated
    # artifact, so a missing marketing tool cannot leave outputs half-updated.
    os.environ.setdefault("ARTIFACT_TOOL_RPC_DAEMON_STARTUP_TIMEOUT_S", "60")
    try:
        import artifact_tool  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Marketing calendar generation requires the spreadsheet tooling. "
            "Run `pip install -r requirements-marketing.txt` first."
        ) from exc

    features = load_catalog()
    rows = build_rows(features)
    write_csv(rows)
    write_markdown(features)
    write_xlsx(rows, features)
    print(
        f"Wrote {len(rows)} calendar rows and refreshed "
        f"{CSV_PATH.relative_to(ROOT)}, {MD_PATH.relative_to(ROOT)} and {XLSX_PATH.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
