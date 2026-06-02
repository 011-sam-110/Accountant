"""SA103 (self-employment) mapping — the hidden fixed layer.

The user customises the *visible* `category.label`, but every category is
pinned to an SA103 box code here. Renaming never touches the mapping, so
exports stay correct however he customises (brief §4 / plan §5).

Box numbers follow SA103F and are CONFIG, not code (plan risk #11) — confirm
against the current-year form before first real export.
"""
from __future__ import annotations

# code -> (box_number, kind, label, ocr_selectable)
SA103_BOXES: list[dict] = [
    # ---- income ----
    {"code": "income_lesson", "box_number": "15", "kind": "income",
     "label": "Turnover (lesson / tuition fees)"},
    {"code": "income_other", "box_number": "16", "kind": "income",
     "label": "Any other business income"},
    # ---- expenses ----
    {"code": "car_van_travel", "box_number": "20", "kind": "expense",
     "label": "Car, van and travel expenses"},
    {"code": "vehicle_costs", "box_number": "20", "kind": "expense",
     "label": "Car, van and travel expenses (vehicle running costs)"},
    {"code": "phone_internet", "box_number": "25", "kind": "expense",
     "label": "Phone, fax, stationery and other office costs"},
    {"code": "advertising_marketing", "box_number": "24", "kind": "expense",
     "label": "Advertising and business entertainment costs"},
    {"code": "professional_franchise_fees", "box_number": "30", "kind": "expense",
     "label": "Accountancy, legal and other professional fees"},
    {"code": "accountancy_admin_bank", "box_number": "30", "kind": "expense",
     "label": "Accountancy, legal and other professional fees"},
    {"code": "training_cpd", "box_number": "31", "kind": "expense",
     "label": "Other allowable business expenses"},
    {"code": "other", "box_number": "31", "kind": "expense",
     "label": "Other allowable business expenses"},
]

SA103_BY_CODE: dict[str, dict] = {b["code"]: b for b in SA103_BOXES}

# Default per-user categories (the visible layer). Seeded at account creation.
# (label, sa103_code, kind, is_favourite)
DEFAULT_CATEGORIES: list[tuple[str, str, str, bool]] = [
    ("Lesson / tuition fees", "income_lesson", "income", True),
    ("Other income", "income_other", "income", False),
    ("Car, van & travel", "car_van_travel", "expense", True),
    ("Vehicle costs", "vehicle_costs", "expense", False),
    ("Phone & internet", "phone_internet", "expense", False),
    ("Advertising & marketing", "advertising_marketing", "expense", False),
    ("Professional / franchise fees", "professional_franchise_fees", "expense", False),
    ("Training & CPD", "training_cpd", "expense", False),
    ("Accountancy, admin & bank charges", "accountancy_admin_bank", "expense", False),
    ("Other allowable business expenses", "other", "expense", False),
]

# Fixed enum the vision-LLM must choose from (expenses only).
OCR_EXPENSE_CODES: list[str] = [
    b["code"] for b in SA103_BOXES if b["kind"] == "expense"
]

# Human guidance for the OCR system prompt (vendor cue -> category).
OCR_CATEGORY_GUIDANCE = (
    "fuel/petrol/diesel/parking/tolls -> car_van_travel; "
    "MOT/servicing/tyres/repairs/insurance/dual-controls -> vehicle_costs; "
    "mobile/broadband/SIM/phone -> phone_internet; "
    "ads/flyers/website/social -> advertising_marketing; "
    "DVSA/ADI/franchise/licence -> professional_franchise_fees; "
    "course/CPD/training -> training_cpd; "
    "accountant/bank charges/admin/stationery -> accountancy_admin_bank; "
    "anything else -> other"
)


def box_for_code(code: str) -> str:
    b = SA103_BY_CODE.get(code)
    return b["box_number"] if b else "31"
