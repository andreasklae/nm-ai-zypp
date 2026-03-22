import re
from dataclasses import dataclass


@dataclass(slots=True)
class LearnedRule:
    """A learned rule representing domain knowledge about specific Tripletex API endpoints."""

    path_pattern: str
    method_pattern: str
    rule_text: str

    def matches(self, method: str, path: str) -> bool:
        return bool(
            re.match(self.method_pattern, method, re.IGNORECASE) and re.match(self.path_pattern, path, re.IGNORECASE)
        )


# Map of compiled learned rules.
LEARNED_RULES: list[LearnedRule] = [
    # --- GLOBAL RULES ---
    LearnedRule(
        path_pattern=r".*",
        method_pattern=r".*",
        rule_text=(
            "Use Tripletex internal ids (integer), not your own external ids or numbers, "
            "when linking objects (e.g. customer_id, project_id). Nested DTOs often only need "
            '{"id": ...} when referring to an existing object.'
        ),
    ),
    # --- GET COLLECTION FILTERS ---
    LearnedRule(
        path_pattern=r"^/order/?$",
        method_pattern=r"GET",
        rule_text="Requires query params `orderDateFrom` and `orderDateTo`. The 'To' date is exclusive. If querying a single day, `orderDateTo` MUST be strictly later than `orderDateFrom` (e.g. From + 1 day). Do not probe the collection without a date range.",
    ),
    LearnedRule(
        path_pattern=r"^/invoice/?$",
        method_pattern=r"GET",
        rule_text=(
            "Requires query params `invoiceDateFrom` and `invoiceDateTo`. The 'To' date is exclusive. If querying a single day, `invoiceDateTo` MUST be strictly later than `invoiceDateFrom` (e.g. From + 1 day). "
            "Use POST /invoice for new invoices, or pass both date filters for historical reads. "
            "The `fields=` filter does NOT support `dueDate` — use `invoiceDueDate` instead."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/invoice/details/?$",
        method_pattern=r"GET",
        rule_text=(
            "Requires query params `invoiceDateFrom` and `invoiceDateTo`. The 'To' date is exclusive. If querying a single day, `invoiceDateTo` MUST be strictly later than `invoiceDateFrom` (e.g. From + 1 day). "
            "To get details for a specific invoice, use GET /invoice/details/{id} instead."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/ledger/voucher/?$",
        method_pattern=r"GET",
        rule_text="Requires query params `dateFrom` and `dateTo`. The 'To' date is exclusive. If querying a single day, `dateTo` MUST be strictly later than `dateFrom`. Do not probe without a date range.",
    ),
    LearnedRule(
        path_pattern=r"^/ledger/posting/?$",
        method_pattern=r"GET",
        rule_text=(
            "Requires query params `dateFrom` and `dateTo`. The 'To' date is exclusive. If querying a single day, `dateTo` MUST be strictly later than `dateFrom`. "
            "To view postings for a specific voucher, use GET /ledger/voucher/{id}?fields=postings(*) instead."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/supplier/?$",
        method_pattern=r"GET",
        rule_text=(
            "Do not filter by `name`. The API does not support partial text search on supplier names. "
            "Filter by `supplierNumber` or `organizationNumber`, or pull all active suppliers and filter in code."
        ),
    ),
    # --- CUSTOMERS ---
    LearnedRule(
        path_pattern=r"^/customer/?$",
        method_pattern=r"GET",
        rule_text="Filter by `customerName`, not `name`. Example: `?customerName=Test AS`.",
    ),
    LearnedRule(
        path_pattern=r"^/customer/?.*",
        method_pattern=r"(POST|PUT)",
        rule_text=(
            "organizationNumber validation depends on country. Send only 9 digits for Norway without spaces or MVA. "
            "If invoiceSendMethod=EHF, physical address fields might be strictly required."
        ),
    ),
    # --- SUPPLIERS ---
    LearnedRule(
        path_pattern=r"^/supplier/?.*",
        method_pattern=r"(POST|PUT)",
        rule_text="organizationNumber: Send only 9 digits with no spaces or MVA suffix.",
    ),
    LearnedRule(
        path_pattern=r"^/supplierInvoice/?.*",
        method_pattern=r"POST",
        rule_text=(
            "No POST /supplierInvoice exists. Book supplier invoices via POST /ledger/voucher "
            "(debit expense account, credit account 2400 with supplier_id)."
        ),
    ),
    # --- EMPLOYEES & SALARY ---
    LearnedRule(
        path_pattern=r"^/employee/?.*",
        method_pattern=r"(POST|PUT)",
        rule_text=(
            "Always set `dateOfBirth`. It becomes strictly required on any PUT operation, "
            "and is required before employment/salary can be created."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/employee/employment/?.*",
        method_pattern=r"POST",
        rule_text=(
            "Requires the employee to have a `dateOfBirth` set on their main profile first. "
            "Also requires a `division` ID."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/division/?",
        method_pattern=r"POST",
        rule_text=(
            "When creating a division (virksomhet), you MUST provide `startDate`, `name`, `municipality` (object with id), "
            "`municipalityDate` (same as startDate), and an `organizationNumber` that is DIFFERENT from the parent company's org number. "
            "You cannot use the parent's juridisk enhet number."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/employee/employment/details/?.*",
        method_pattern=r"POST",
        rule_text=(
            "Needs an `occupationCode` (STYRK 4-digit code). E.g., CEO->1120, Developer->2512. "
            "Set `percentageOfFullTimeEquivalent` to 100 for full time."
        ),
    ),
    # --- PROJECTS & BILLING ---
    LearnedRule(
        path_pattern=r"^/project/?$",
        method_pattern=r"POST",
        rule_text="Always set a `startDate`. Projects without a start date will not synchronize and creation may fail.",
    ),
    LearnedRule(
        path_pattern=r"^/project/.*",
        method_pattern=r"PUT",
        rule_text=(
            "PROJECT BUDGET vs FIXED PRICE: The 'budget' field means internal cost budget, NOT fixed-price billing. "
            "Do NOT use 'budget' to set a fixed price. Fixed price uses 'isFixedPrice' and 'fixedprice' fields."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/project/projectActivity/?",
        method_pattern=r"POST",
        rule_text=(
            "If this returns 409 Conflict, an activity with this name already exists for the project. "
            "Fetch the existing activity via GET /activity/>forTimeSheet and use its ID."
        ),
    ),
    # --- TIMESHEETS ---
    LearnedRule(
        path_pattern=r"^/timesheet/entry/?",
        method_pattern=r"POST",
        rule_text=(
            "If this returns 409 Conflict, a timesheet entry already exists for this employee/date/activity. "
            "You must fetch the existing entry (GET /timesheet/entry) and update it (PUT) instead of creating a new one."
        ),
    ),
    # --- INCOMING INVOICE (BETA — always 403) ---
    LearnedRule(
        path_pattern=r"^/incomingInvoice.*",
        method_pattern=r".*",
        rule_text=(
            "WARNING: /incomingInvoice is a BETA-restricted endpoint and will always return 403 Forbidden. "
            "DO NOT call any /incomingInvoice endpoint. "
            "To book a supplier invoice, use POST /ledger/voucher instead "
            "(debit expense account, credit accounts payable 2400 with supplierId on the posting row)."
        ),
    ),
    # --- LOGISTICS (module-gated) ---
    LearnedRule(
        path_pattern=r"^/purchaseOrder.*|^/inventory/location.*|^/product/group.*|^/product/groupRelation.*|^/product/inventoryLocation.*|^/product/productPrice.*",
        method_pattern=r".*",
        rule_text=(
            "This endpoint requires the Logistics Basic module. "
            "It will not be available in a standard Tripletex account. "
            "Do NOT attempt to call it unless the user explicitly mentions logistics or warehouse management."
        ),
    ),
    # --- ORDERS ---
    LearnedRule(
        path_pattern=r"^/order/?$",
        method_pattern=r"POST",
        rule_text=(
            "REQUIRED: Always include `deliveryDate` AND `orderDate` (both YYYY-MM-DD) when creating an order. "
            "Omitting `deliveryDate` will fail with 422 'deliveryDate Kan ikke være null'. "
            "Omitting `orderDate` will fail with 422. "
            "If the user did not specify dates, default both to today's date."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/order/orderline/?.*",
        method_pattern=r"(POST|PUT)",
        rule_text=(
            "Pricing mode rule: If the order has `isPrioritizeAmountsIncludingVat=true`, "
            "send `unitPriceIncludingVatCurrency`. If `false` (default), send `unitPriceExcludingVatCurrency`. "
            "Sending the wrong field will cause a validation error."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/order/\{id\}/:invoice",
        method_pattern=r"PUT",
        rule_text="To invoice an order, use PUT /order/{id}/:invoice with body parameter `sendToCustomer: false` if you don't want it emailed.",
    ),
    # --- VOUCHERS & LEDGER POSTINGS ---
    LearnedRule(
        path_pattern=r"^/ledger/voucher/?.*",
        method_pattern=r"POST",
        rule_text=(
            "REQUIRED: Every POST /ledger/voucher MUST include a `postings` array with at least one debit and one credit row. "
            "A voucher without postings will always fail with 422 ('Et bilag kan ikke registreres uten posteringer'). "
            "Do NOT send the voucher body first and add postings later — include them in the same request. "
            "Row 0 is system-generated; do not attempt to create it externally. "
            "VAT types: Output (Sales) = 3 (25%), Input (Purchases) = 1 (25%). "
            "Depreciation: monthly = acquisition_cost / (useful_life_years × 12). "
            "Debit expense (60XX), credit accumulated depreciation (e.g. 1209, 1219, 1259, NOT 1200). "
            "If posting to a Customer/Supplier/Employee subledger account, you MUST include the `customerId` / `supplierId` / `employeeId` on the posting row. "
            "When providing `postings`, `amountGross` MUST exactly equal `amountGrossCurrency`. (The tool will attempt to auto-fix this if you only provide `amountGross`, but you should provide both to be safe)."
        ),
    ),
    LearnedRule(
        path_pattern=r"^/ledger/account/?.*",
        method_pattern=r".*",
        rule_text="VAT-locked accounts: If an account is VAT-locked, you MUST use its exact required VAT code or pick a different account.",
    ),
    # --- PAYMENTS & FX ---
    LearnedRule(
        path_pattern=r"^/invoice/\{id\}/:payment",
        method_pattern=r"PUT",
        rule_text=(
            "ONLY use this endpoint to register customer invoice payments (zeros out amountOutstanding). "
            "Do not use manual vouchers for the primary invoice payment. "
            "FX agio/disagio: Pay invoice in full via this endpoint first. Calculate the FX difference by multiplying the FULL nominal invoice amount (INCLUDING VAT) by the difference in exchange rates. Book this difference as a separate manual voucher (8060 agio / 8160 disagio vs 1920 bank)."
        ),
    ),
]


def get_learned_rules_for_endpoint(method: str, path: str) -> list[str]:
    """Return all learned rules applicable to a specific endpoint."""
    return [rule.rule_text for rule in LEARNED_RULES if rule.matches(method, path)]
