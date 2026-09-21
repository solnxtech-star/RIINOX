from typing import Final

ALL: Final = "*"

# ---------------------------------------------------------------------------
# Locale applied to a brand-new organization
# (DEFAULT_PLAN_NAME lives in subscriptions.services; document numbering and the
#  custom-templates feature code live in invoice.services: each app owns its own.)
# ---------------------------------------------------------------------------
# The onboarding screens don't collect these. They are applied as defaults and
# become editable from Settings later (PRD §7 requires them on the profile).
DEFAULT_ORG_COUNTRY: Final = "NG"
DEFAULT_ORG_CURRENCY: Final = "NGN"
DEFAULT_ORG_TIMEZONE: Final = "Africa/Lagos"
# Calling code used to complete local numbers (0803... -> +234803...).
PHONE_COUNTRY_CODE: Final = "234"

# ---------------------------------------------------------------------------
# Default roles seeded into every organization (PRD §28)
# ---------------------------------------------------------------------------
OWNER_ROLE_SLUG: Final = "owner"
ADMINISTRATOR_ROLE_SLUG: Final = "administrator"
# Built-in roles allowed to manage members (invite, change role, remove).
MANAGER_ROLE_SLUGS: Final = (OWNER_ROLE_SLUG, ADMINISTRATOR_ROLE_SLUG)

DEFAULT_ROLES: Final = [
    ("owner", "Owner"),
    ("administrator", "Administrator"),
    ("manager", "Manager"),
    ("sales-manager", "Sales Manager"),
    ("sales-representative", "Sales Representative"),
    ("inventory-manager", "Inventory Manager"),
    ("inventory-staff", "Inventory Staff"),
    ("accountant", "Accountant"),
    ("finance-officer", "Finance Officer"),
    ("teacher-staff", "Teacher/Staff"),
    ("front-desk", "Front Desk"),
]

# role slug -> ALL | list of Permission.code.
# Owner and Administrator get the full catalog (PRD §55). Every other role
# starts with NO permissions (deny by default) until you fill it in from your
# permission catalog; the seeding code fails loudly on unknown codes.
DEFAULT_ROLE_PERMISSIONS: Final[dict[str, str | list[str]]] = {
    "owner": ALL,
    "administrator": ALL,
    # "manager": ["VIEW_PRODUCTS", "EDIT_SELLING_PRICE", ...],
}

# ---------------------------------------------------------------------------
# Business type catalog
# The first six are the onboarding cards from the Figma screen, in order.
# The rest come from the PRD list and are hidden from onboarding for now, but
# stay selectable on the later "edit business types" screen.
# ---------------------------------------------------------------------------
BUSINESS_TYPES: Final = [
    {
        "code": "retail",
        "name": "Retail / Shopping",
        "description": "Accelerate point-of-sale efficiency and stock rotation.",
        "icon": "shopping-basket",
        "highlights": ["Inventory Tracking", "Point Of Sale", "Employee Management"],
        "sort_order": 10,
        "show_in_onboarding": True,
    },
    {
        "code": "restaurant_food",
        "name": "Restaurant / Food",
        "description": "Scale your hospitality with menu and raw material management.",
        "icon": "utensils",
        "highlights": ["Recipe Management", "Table Booking", "Ingredient Alerts"],
        "sort_order": 20,
        "show_in_onboarding": True,
    },
    {
        "code": "pharmacy",
        "name": "Hospital / Pharmacy",
        "description": "Secure oversight of medical supplies and patient records.",
        "icon": "cross",
        "highlights": ["Expiry Tracking", "Batch Management", "Patient Records"],
        "sort_order": 30,
        "show_in_onboarding": True,
    },
    {
        "code": "hospitality",
        "name": "Hotel / Hospitality",
        "description": "Seamlessly manage bookings, guests, and facility logs.",
        "icon": "bed",
        "highlights": ["Booking Engine", "Housekeeping", "Facility Logs"],
        "sort_order": 40,
        "show_in_onboarding": True,
    },
    {
        "code": "manufacturing",
        "name": "Manufacturing",
        "description": "Control assembly lines and rigorous quality assurance.",
        "icon": "factory",
        "highlights": ["BOM Management", "Work Orders", "Quality Control"],
        "sort_order": 50,
        "show_in_onboarding": True,
    },
    {
        "code": "wholesale_distribution",
        "name": "Wholesale / Distribution",
        "description": "High-volume oversight for bulk sales and logistics.",
        "icon": "truck",
        "highlights": ["Bulk Pricing", "Logistics Tracking", "Credit Limits"],
        "sort_order": 60,
        "show_in_onboarding": True,
    },
    # --- PRD types without an onboarding card ------------------------------
    {"code": "fashion", "name": "Fashion", "sort_order": 70, "show_in_onboarding": False},
    {"code": "supermarket", "name": "Supermarket", "sort_order": 80, "show_in_onboarding": False},
    {"code": "school", "name": "School", "sort_order": 90, "show_in_onboarding": False},
    {"code": "general_trading", "name": "General Trading", "sort_order": 100, "show_in_onboarding": False},
    {"code": "services", "name": "Services", "sort_order": 110, "show_in_onboarding": False},
    {"code": "other", "name": "Other", "sort_order": 999, "show_in_onboarding": False},
]

# ---------------------------------------------------------------------------
# Nigeria: 36 states + FCT (backs the "Select state" dropdown)
# ---------------------------------------------------------------------------
NIGERIAN_STATES: Final = (
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
    "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu",
    "Federal Capital Territory", "Gombe", "Imo", "Jigawa", "Kaduna", "Kano",
    "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun",
    "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe",
    "Zamfara",
)
