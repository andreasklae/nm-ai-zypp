# Common Mistakes and Business Rule Caveats

This reference highlights common ambiguities, caveats, and business rules when interacting with the Tripletex OpenAPI endpoints.

## 1. The "Missing Accounts" Problem
The sandbox environment may not contain all standard Norwegian accounts. When a required account number is absent:
1. **Try an exact lookup:** `GET /ledger/account?number=X`
2. **If empty:** You may need to create it using `POST /ledger/account`. Confirm the exact name and purpose with the prompt before creating it aggressively.
3. NEVER hardcode arbitrary account IDs (like 495413346). Always resolve an account by its human-readable account number (e.g. 1920) using `GET /ledger/account?number=1920` to find its internal ID before posting a voucher.
4. **Account Types:** 1000–1999 (ASSET), 2000–2999 (LIABILITY/EQUITY), 3000–3999 (OPERATING_REVENUES), 4000–7999 (OPERATING_EXPENSES), 8000–8999 (FINANCIAL_INCOME_EXPENSES).

## 2. VAT Rules
* **You are bad at math:** Never calculate VAT fractions manually. Use your `calculate_vat_split` tool for any amount that is *inclusive* of VAT.
* **Standard VAT Codes — do NOT call `GET /ledger/vatType` to look these up, use them directly:**
  * `3` — Output 25% (standard domestic sales)
  * `31` — Output 15% (food/beverages)
  * `33` — Output 12% (transport, cinema etc.)
  * `6` — Output 0%, outside VAT law (used for exempt domestic sales, e.g. financial services)
  * `52` — Output 0%, export exemption (sales of goods/services to foreign customers)
  * `1` — Input 25% (standard purchases)
* **When to look up VAT types:** Only call `GET /ledger/vatType` for unusual or ambiguous codes not listed above.
* **VAT-locked Accounts:** If a ledger account requires a specific `vatType` (is VAT-locked), you MUST use that exact VAT type ID on your posting row, or choose a different account.

## 3. Mandatory Date of Birth (DOB) for Employees
Tripletex strictly requires `dateOfBirth` before an employment record can be created or updated. If you ever `POST /employee` or `PUT /employee/{id}`, ALWAYS supply the `dateOfBirth` if it's available in your context. Failing to do so will cause 422 mapping errors on subsequent payroll/salary calls.

## 4. Duplicate Entry Conflicts (`code: 14000`)
If you receive a `409 Conflict` (e.g., `An entry already exist`), DO NOT keep trying to `POST` the same payload.
* **For Timesheets:** Fetch the existing entry via `GET /timesheet/entry` and update it (`PUT`).
* **For Project Activities:** Fetch the existing activity via `GET /activity/>forTimeSheet` and use its internal `id`.

## 5. Depreciation Formula
If you are asked to run a simplified depreciation for fixed assets:
`monthly_depreciation = acquisition_cost / (useful_life_years × 12)`
* Debit expense account (`6010` or `60XX`).
* Credit accumulated depreciation account (usually ends in 9, e.g., `1209`, `1219`, `1259`). DO NOT credit the main asset account (e.g., `1200`).

## 6. Query Parameters and Filters
* **Date Filters:** Collection reads for `/order`, `/invoice`, `/ledger/voucher`, and `/ledger/posting` generally require BOTH `dateFrom` and `dateTo` query parameters. Do not probe the collections broadly without a date range.
* **Fields:** Use `fields=` aggressively to reduce payload sizes (e.g., `fields=id,name,customer(displayName)`).
* **Exact ID Matches:** The Tripletex API relies heavily on internal numeric integer IDs. When linking objects (e.g. adding a `customerId` to an order), you must pass the integer ID, not the organization number string.

## 7. Business Documents vs. Accounting Truth
Do not assume `GET /invoice` tells you the final truth about a customer's payment status or accounts receivable. The *accounting truth* lives in the ledger (`/ledger/posting/openPost`). When in doubt about whether an item has been paid, defer to the ledger endpoints rather than the business document endpoints.
