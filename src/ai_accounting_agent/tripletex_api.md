# Tripletex API Reference for the AI Accounting Agent

This document is a practical, agent-oriented guide to the Tripletex API. Everything documented here has been verified through live API calls against the sandbox on `2026-03-20`.

The Tripletex API is version `2.74.00` with `838` operations across `546` paths. This file covers all endpoint families from the OpenAPI spec, with the most detailed coverage on the accounting-critical endpoints and exact working payloads.

The goal: a later agent can read this file, identify the correct endpoint, and construct a working API call on the first attempt.

## Sources

- Live spec: `GET /v2/openapi.json` (3.6 MB JSON)
- OpenAPI docs UI: `https://kkpqfuj-amager.tripletex.dev/v2-docs/`
- Developer docs: `https://developer.tripletex.no/`
- Ledger/voucher FAQ: `https://developer.tripletex.no/docs/documentation/faq/ledger-voucher/`

## Base URL and Auth

All Tripletex API calls use HTTP Basic Auth with username `0` (own company) and the session token as password.

```python
import requests
response = requests.get(f"{base_url}/employee", auth=("0", session_token))
```

```text
Authorization: Basic base64("0:<sessionToken>")
```

### Two contexts: sandbox vs competition

| | Sandbox (local dev) | Competition (via `/solve`) |
|---|---------------------|---------------------------|
| Base URL | `https://kkpqfuj-amager.tripletex.dev/v2` (from `.env`) | `body["tripletex_credentials"]["base_url"]` |
| Session token | `TRIPLETEX_SESSION_TOKEN` from `.env` | `body["tripletex_credentials"]["session_token"]` |
| API access | Direct to Tripletex | Via authenticated proxy |
| Data | Persistent sandbox, accumulates | Fresh empty account per submission |

**Sandbox token**: The `.env` token (`eyJ0b2tlbklk...`) is a base64-encoded JSON containing `{"tokenId":..., "token":"<uuid>"}`. Use the full string as the password. This sandbox token **expires 2026-03-31**.

**Competition token**: The `session_token` from the `/solve` request body is used directly as the password. Always use the provided `base_url` — never call the sandbox URL during competition submissions.

### Web UI access

The sandbox has a full Tripletex web interface at `https://kkpqfuj-amager.tripletex.dev`. Log in with the email from your sandbox card (use "Forgot password" to set up Visma Connect on first login). The same Visma Connect credentials work for competition submission accounts too.

Accountant-client access uses a different company id as username. Use `GET /company/%3EwithLoginAccess` to discover accessible client companies.

## URL Path Conventions

Tripletex uses special prefixes in paths:

| Prefix | Meaning | URL encoding | Example |
|--------|---------|-------------|---------|
| `:` | Action/command | Keep as-is (no encoding needed) | `PUT /order/{id}/:invoice` |
| `>` | Summary/aggregation | **Must encode as `%3E`** | `GET /token/session/%3EwhoAmI` |

This is critical. Using `>whoAmI` without encoding gives `400 / 4000 HTTP 405 Method Not Allowed`.

## Response Envelopes

**List response:**

```json
{
  "fullResultSize": 101,
  "from": 0,
  "count": 100,
  "versionDigest": "string or null",
  "values": [...]
}
```

**Single response:**

```json
{
  "value": {...}
}
```

**Pagination**: use `from` and `count` query params. Default count varies by endpoint. Max count depends on the endpoint (some cap at 1000, some at 10000).

## Curated Tool → API Endpoint Quick Reference

This table maps each curated agent tool to the underlying Tripletex endpoint and key constraints.

| Curated Tool | API Endpoint | Key Notes |
|---|---|---|
| create_employee | POST /employee | Needs firstName, lastName, userType, department.id |
| grant_employee_privileges | PUT /employee/entitlement/:grantEntitlementsByTemplate | Query params: employeeId, template |
| create_customer | POST /customer | orgNumber: exactly 9 digits. Auto-fills customerNumber, currency |
| create_supplier | POST /supplier | orgNumber: exactly 9 digits. Auto-fills supplierNumber |
| create_product | POST /product | priceExcludingVatCurrency required. Non-VAT: only vatType.id=6 |
| create_project | POST /project | Needs name, projectManager.id, startDate |
| configure_project_billing | GET then PUT /project/{id} | Handles version automatically. Sets customer, isFixedPrice, fixedprice |
| create_department | POST /department | Minimal: just name. Auto-fills departmentNumber |
| create_voucher | POST /ledger/voucher?sendToLedger=true | Postings must sum to 0. VENDOR accounts need supplier.id. Supports freeAccountingDimension1/2/3 |
| create_accounting_dimension | POST /ledger/accountingDimensionName + POST /ledger/accountingDimensionValue | Link on voucher posting via `freeAccountingDimension1/2/3` |
| create_order | POST /order | deliveryDate is REQUIRED. Supports project.id for project billing |
| create_invoice | POST /invoice?sendToCustomer=false | Needs order_ids. Auto-ensures bank account on 1920 |
| register_invoice_payment | PUT /invoice/{id}/:payment | Query params: paymentDate, paymentTypeId, paidAmount |
| create_credit_note | PUT /invoice/{id}/:createCreditNote | Query params: date, comment. For crediting invoices ONLY — do NOT use for payment reversals |
| reverse_voucher | PUT /ledger/voucher/{id}/:reverse | Query param: date. Use this for reversing payments returned by bank |
| calculate_vat_split | (pure calculation) | Splits amount incl. VAT into net + VAT components. Use for supplier invoices |
| create_employment | POST /employee/employment | Sets employee start date. Auto-creates division if needed |
| create_travel_expense | POST /travelExpense | travelDetails MUST be nested, not at root level |
| add_travel_expense_cost | POST /travelExpense/cost | Needs costCategory.id, paymentType.id, currency.id |
| add_travel_mileage_allowance | POST/PUT /travelExpense/mileageAllowance | Look up `rateType.id` from `GET /travelExpense/rate?type=MILEAGE_ALLOWANCE` |
| add_travel_per_diem | POST/PUT /travelExpense/perDiemCompensation | Look up `rateType.id` from `GET /travelExpense/rate?type=PER_DIEM` |
| transition_travel_expense | PUT /travelExpense/:{action} | Query param: id. Actions: deliver, approve, unapprove, undeliver, createVouchers |
| get_timesheet_activities | GET /activity/%3EforTimeSheet | Query params: projectId, employeeId, date |
| create_timesheet_entry | POST /timesheet/entry | activity.id MUST come from get_timesheet_activities |
| create_contact | POST /contact | Minimal tested body: `firstName`, `lastName`, `customer.id` |
| update_employee | PUT /employee/{id} | `dateOfBirth` becomes required on update |
| run_salary_transaction | POST /division + POST /employee/employment + POST /salary/transaction | Division create needs `organizationNumber`, `municipality`, `municipalityDate` |
| upload_attachment | POST /ledger/voucher/{id}/attachment or /travelExpense/{id}/attachment | Multipart field name is `file` |
| create_bank_reconciliation | POST /bank/reconciliation | Manual reconciliation works with `account`, `accountingPeriod`, `type`, `bankAccountClosingBalanceCurrency` |
| create_webhook_subscription | POST /event/subscription | `targetUrl` must be absolute HTTPS, `event` comes from `GET /event` |
| get_reference_data | Various GET endpoints | See tool docstring for full reference list and filters |
| tripletex_get | Any GET endpoint | Generic GET for endpoints without curated tools (open posts, bank statements, SAF-T, etc.) |
| tripletex_post | Any POST endpoint | Generic POST for endpoints without curated tools |
| tripletex_put | Any PUT endpoint | Generic PUT for actions like `:sendToLedger`, `:sendToInbox`, etc. |
| tripletex_delete | Any DELETE endpoint | Generic DELETE |

## Rate Limiting

Response headers on every call:

- `X-Rate-Limit-Limit` - allowed requests per period (100 in this sandbox)
- `X-Rate-Limit-Remaining` - remaining requests
- `X-Rate-Limit-Reset` - seconds until period resets

After hitting the limit, all requests return `429` until reset.

## First Calls an Agent Should Make

### 1. `GET /token/session/%3EwhoAmI`

Confirms auth works. Returns `employeeId`, `companyId`, `language`.

Live result: `employeeId=18442131`, `companyId=108114483`, `language="no"`.

### 2. `GET /ledger/vatSettings`

**Critical**: determines VAT behavior for the entire company.

Live result: `vatRegistrationStatus: "VAT_NOT_REGISTERED"`.

This means:
- Products can only use `vatType.id=6` (0% - outside VAT law)
- Accounts locked to outgoing VAT codes (id=3, 31, 32) will reject postings
- Travel expense costs with VAT will fail delivery/approval
- The sandbox operates as a non-VAT-registered company

In a VAT-registered company, you'd also have access to vatType id=3 (25%), id=31 (15%), id=32 (12%), etc.

### 3. `GET /company/{companyId}?fields=*`

Returns company name, org number, currency, type.

Live: `name="NM i AI Zypp a2b6f991"`, `organizationNumber="369691287"`, `type="AS"`, `currency.id=1` (NOK).

### 4. `GET /company/salesmodules`

Shows enabled modules: `WAGE`, `ELECTRONIC_VOUCHERS`, `TIME_TRACKING`, `API_V2`, `KOMPLETT`, `UP_TO_500_VOUCHERS`.

Important: `KOMPLETT` does NOT guarantee asset access. Asset endpoints returned `403` despite this module.

### 5. `GET /employee/entitlement?fields=id,name`

Shows employee permissions. This sandbox has 21 entitlements including `ROLE_ADMINISTRATOR`, `AUTH_ALL_VOUCHERS`, `AUTH_INVOICING`.

Field pitfall: `displayName` is not valid on this DTO; use `name`.

## Core API Conventions

### PUT for partial updates (not PATCH)

Send only changed fields + `id`. Include `version` for optimistic locking.

Live-tested: PUT with correct `version` succeeds. PUT with stale `version` fails with `409 / 8000 RevisionException`.

Versions do NOT always increment by 1. A project create returned `version=0`, first update returned `version=2`.

### The `fields` parameter

Controls which fields are returned. Supports nested selection:

```text
fields=id,date,description,postings(id,account(number,name),amount,description)
```

This was live-tested and returned deeply nested filtered data correctly.

**Critical pitfall**: field names must match the exact DTO property names. Common mistakes:

| Resource | Correct | Wrong |
|----------|---------|-------|
| `department` | `departmentNumber` | `number` |
| `product` | `priceExcludingVatCurrency` | `salesPrice` |
| `project` | `number` | `projectNumber` |
| `travelExpense` | `title`, `state` | `description`, `status` |
| `voucherType` | `name`, `displayName` | `number` |
| `vatType` | `percentage` | `rate` |
| `employee/entitlement` | `name` | `displayName` |
| `travelExpense/costCategory` | `description` | `name` |

When a field fails: `400 / 11000 Illegal field in fields filter`. Don't guess again - check the spec.

### Search parameter pitfalls

Filter parameter names often differ from field names:

- `/customer` filters by `customerName`, NOT `name`
- `/supplier` has NO `name` filter at all - use `supplierNumber`, `organizationNumber`, or `/supplierCustomer/search`
- `/invoice` requires `invoiceDateFrom` + `invoiceDateTo`
- `/supplierInvoice` requires `invoiceDateFrom` + `invoiceDateTo`
- `/ledger/voucher` requires `dateFrom` + `dateTo`
- `/ledger/posting` requires `dateFrom` + `dateTo`
- `/order` collection reads should be treated as requiring `orderDateFrom` + `orderDateTo` in competition-style proxy traffic. A bare `GET /order?count=1` returned `422` in the live evaluator environment.
- Do NOT use bare collection reads such as `GET /order?count=1` to inspect schema. Use verified notes or a specific entity read instead.

## Error Pattern Quick Reference

| HTTP | Code | Meaning | Common cause |
|------|------|---------|-------------|
| `400` | `4000` | Bad request | Wrong HTTP method, or `>` not URL-encoded |
| `400` | `11000` | Illegal field | Wrong `fields=` parameter name |
| `400` | `12000` | Path param error | Invalid path parameter (wrong type, missing) |
| `400` | `24000` | Cryptography error | Token/encryption issue |
| `401` | `3000` | Unauthorized | Expired/invalid session token |
| `403` | `9000` | Forbidden | Feature/module not available |
| `404` | `6000` | Not found | Wrong ID or endpoint doesn't exist |
| `409` | `7000` | Object exists | Trying to create a resource that already exists |
| `409` | `8000` | Revision conflict | Stale `version` on PUT |
| `409` | `10000` | Locked | Resource is locked by another process |
| `409` | `14000` | Duplicate entry | Duplicate unique constraint violation |
| `422` | `15000` | Validation failed | Missing required query params |
| `422` | `16000` | Mapping failed | Field doesn't exist in request body |
| `422` | `17000` | Sorting error | Invalid `sorting` parameter |
| `422` | `18000` | Business validation | Valid JSON but business rules violated |
| `422` | `21000` | Param error | Invalid query parameter value |
| `422` | `22000` | Invalid JSON | Malformed JSON in request body |
| `422` | `23000` | Result set too large | Reduce `count` or add filters |
| `429` | - | Rate limited | Wait for `X-Rate-Limit-Reset` seconds |
| `500` | `1000` | Internal error | Unexpected server-side failure |

The `422 / 16000` error is especially sneaky: it means a field name in your POST/PUT body doesn't exist on the schema. Example: sending `departureDate` at root level of travel expense instead of nested inside `travelDetails`.

## Norwegian Chart of Accounts (Norsk Standard Kontoplan)

Account numbers follow this structure:

| Range | Category | Examples |
|-------|----------|---------|
| 1000-1999 | Assets (Eiendeler) | 1500 Kundefordringer, 1920 Bankinnskudd |
| 2000-2999 | Equity & Liabilities | 2000 Aksjekapital, 2400 Leverandørgjeld |
| 3000-3999 | Revenue (Inntekter) | 3000 Salgsinntekt, 3200 Salgsinntekt utenfor avgiftsomr. |
| 4000-4999 | Cost of goods | 4000 Innkjøp av råvarer |
| 5000-5999 | Salary expenses | 5000 Lønn (not in default sandbox) |
| 6000-6999 | Other operating expenses | 6300 Leiekostnad, 6900 Telefon |
| 7000-7999 | Depreciation & write-offs | 7000 Avskrivninger |
| 8000-8999 | Financial items | 8000 Finansinntekter |

Key accounts for common operations:

| Account | Name | Typical use |
|---------|------|-------------|
| 1500 | Kundefordringer | Customer receivables (ledgerType=CUSTOMER) |
| 1920 | Bankinnskudd | Bank deposits (isBankAccount=true) |
| 2400 | Leverandørgjeld | Supplier payables (ledgerType=VENDOR) |
| 3000 | Salgsinntekt, avgiftspliktig | Sales revenue with VAT |
| 3200 | Salgsinntekt, utenfor avgiftsomr. | Sales revenue outside VAT |
| 4000 | Innkjøp av råvarer | Purchases |

This sandbox has 201+ accounts. Use `GET /ledger/account?count=200&fields=id,number,name,type` to get the full list.

## VAT Types Quick Reference

| id | number | name | rate | Use for |
|----|--------|------|------|---------|
| 0 | (none) | No VAT treatment | 0% | Bank accounts, equity |
| 1 | 1 | Fradrag inngående, høy sats | 25% | Purchase deduction high rate |
| 3 | 3 | Utgående avgift, høy sats | 25% | Sales VAT high rate (requires VAT registration) |
| 5 | 5 | Ingen utgående avgift (innenfor mva) | 0% | Sales within VAT law, zero rate |
| 6 | 6 | Ingen utgående avgift (utenfor mva) | 0% | Sales outside VAT law |
| 11 | 11 | Fradrag inngående, middels sats | 15% | Purchase deduction medium |
| 12 | 13 | Fradrag inngående, lav sats | 12% | Purchase deduction low |
| 31 | 31 | Utgående avgift, middels sats | 15% | Sales VAT medium rate |
| 32 | 33 | Utgående avgift, lav sats | 12% | Sales VAT low rate |

Total: 51 VAT types. Use `GET /ledger/vatType?count=60&fields=id,name,number,percentage` for the full list.

**Important**: Some accounts are "VAT-locked" - they require a specific vatType on every posting. Example: account 3200 is locked to vatType 6. Omitting vatType on a posting to a locked account produces `422 / 18000`.

## Voucher Types

| Name | Typical use |
|------|-------------|
| Utgående faktura | Outgoing customer invoice |
| Leverandørfaktura | Supplier/vendor invoice |
| Betaling | Payment |
| Lønnsbilag | Salary voucher |
| Reiseregning | Travel expense |
| Ansattutlegg | Employee expense |
| Terminoppgave | Tax period report |

Total: 17 types. Fetch with `GET /ledger/voucherType?count=20&fields=id,name,displayName`.

## Currencies

| id | code | description |
|----|------|-------------|
| 1 | NOK | Norge |
| 2 | SEK | Sverige |
| 3 | DKK | Danmark |
| 4 | USD | USA |
| 5 | EUR | EU |
| 6 | GBP | Storbritannia |

Total: 21 currencies. Fetch with `GET /currency?count=25&fields=id,code,description`.

---

## Resource Families: Verified Working Examples

## 1. Customer

### Read

```text
GET /customer?count=10&fields=id,name,customerNumber,organizationNumber,email,invoiceSendMethod,version
```

Filter by `customerName` (not `name`), `organizationNumber`, `email`, `customerAccountNumber`.

### Create

Working body (minimal):

```json
{ "name": "Test Kunde AS" }
```

Full example with common fields:

```json
{
  "name": "Test Kunde AS",
  "organizationNumber": "987654321",
  "email": "test@testkunde.no",
  "invoiceSendMethod": "EMAIL",
  "invoicesDueIn": 14,
  "invoicesDueInType": "DAYS"
}
```

Full example with address (used when task provides street, postal code, city):

```json
{
  "name": "Fjordkraft AS",
  "organizationNumber": "843216285",
  "email": "post@fjordkraft.no",
  "invoiceSendMethod": "EMAIL",
  "postalAddress": {
    "addressLine1": "Fjordveien 129",
    "postalCode": "2317",
    "city": "Hamar"
  }
}
```

Address can be set at creation time via the nested `postalAddress` object — no need to create the customer first and then update with a PUT.

Auto-filled by Tripletex: `customerNumber`, `language` ("NO"), `currency` (NOK), `ledgerAccount` (1500), postal/physical addresses (if not provided).

### Update / Delete

PUT with `id` + `version`. DELETE returns `204`.

## 2. Supplier

### Read

```text
GET /supplier?count=10&fields=id,name,supplierNumber,organizationNumber,email,version
```

No `name` filter exists. Use `supplierNumber`, `organizationNumber`, or `GET /supplierCustomer/search?query=searchterm` (searches across both suppliers and customers by name).

### Create

```json
{
  "name": "Test Leverandør AS",
  "organizationNumber": "123456789",
  "email": "faktura@leverandor.no"
}
```

With separate invoice email (used when the task specifies a dedicated invoice email like `faktura@...`):

```json
{
  "name": "Dalheim AS",
  "organizationNumber": "892196753",
  "email": "post@dalheim.no",
  "invoiceEmail": "faktura@dalheim.no"
}
```

`invoiceEmail` is the email address where invoices should be sent. It's separate from the general `email` field. If the task only provides one email and it looks like an invoice email (e.g., starts with `faktura@`), set it as both `email` and `invoiceEmail`.

Auto-filled: `supplierNumber`, `currency`, `ledgerAccount` (2400), addresses.

## 3. Product

### Read

```text
GET /product?count=10&fields=id,name,number,priceExcludingVatCurrency,priceIncludingVatCurrency,vatType(id,percentage),version
```

### Create

```json
{
  "name": "Konsulenttjeneste",
  "number": "KONS-001",
  "priceExcludingVatCurrency": 1000.00
}
```

Auto-filled: `vatType` (id=6, 0% in non-VAT company), `currency` (NOK).

**vatType restriction**: In a non-VAT-registered company, only `vatType.id=6` is accepted for products. Attempting id=3 (25%) returns `422 / 18000 "Ugyldig mva-kode"`.

## 4. Department

### Read

```text
GET /department?count=20&fields=id,name,departmentNumber,version
```

### Create

```json
{ "name": "Research-1fa659 Dept" }
```

Minimal: just `name` is enough. Live-tested on `2026-03-20` and returned:

```json
{
  "id": 904510,
  "version": 0,
  "name": "Research-1fa659 Dept",
  "departmentNumber": "",
  "isInactive": false
}
```

Auto-filled by Tripletex: `departmentNumber` becomes an empty string if omitted, `displayName` mirrors `name`, and `businessActivityTypeId` defaults to `0`.

### Update

```text
PUT /department/{id}
```

Use standard Tripletex optimistic locking: `GET` first, then send `id` + `version` + changed fields.

### Competition workflow

1. `POST /department` with `{name}` -> department id
2. Reuse that `department.id` in later `POST /employee`, `POST /project`, or parent-entity updates

## 5. Project

### Create

Required fields: `name`, `projectManager`, `startDate`.

```json
{
  "name": "Test Prosjekt",
  "number": "P-001",
  "projectManager": { "id": 18442131 },
  "startDate": "2026-01-01",
  "endDate": "2026-12-31",
  "isInternal": true
}
```

Auto-filled: `number` (if not provided), `currency`, `vatType`.

### Project billing / fixed price

Live-validated on `2026-03-20`:

- `GET /project/{id}?fields=*` returns billing-related fields including `customer`, `projectManager`, `isFixedPrice`, `fixedprice`, and `invoicingPlan`.
- `PUT /project/{id}` accepts a partial update with `id`, `version`, and changed billing fields.

Working update example:

```json
PUT /project/401958515
{
  "id": 401958515,
  "version": 0,
  "customer": { "id": 108245909 },
  "projectManager": { "id": 18442131 },
  "isFixedPrice": true,
  "fixedprice": 122800
}
```

Verified result fields after update:

```json
{
  "id": 401958515,
  "version": 2,
  "customer": { "id": 108245909, "name": "Billing Test Customer 251fe4e6" },
  "projectManager": { "id": 18442131 },
  "isFixedPrice": true,
  "fixedprice": 122800.0,
  "invoicingPlan": []
}
```

Practical guidance:

- Use project `PUT` for customer linkage and fixed-price configuration.
- `invoicingPlan` exists on the DTO, but direct writes to milestone plan structures were not validated here.
- For stage or milestone billing, the safest tested path is: configure the project, then create a project-linked order with a freeform line for the milestone amount, then create the invoice.

### Activity lookup for timesheet

Before creating timesheet entries for a project, fetch applicable activities:

```text
GET /activity/%3EforTimeSheet?projectId={projectId}&employeeId={employeeId}&date=2026-03-20
```

Returns activities like "Fakturerbart arbeid" (id=5588996), "Prosjektadministrasjon" (id=5588995).

## 6. Ledger Voucher (Core Accounting)

This is the most important endpoint family for accounting tasks.

### Read

```text
GET /ledger/voucher?dateFrom=2026-01-01&dateTo=2026-12-31&count=10&fields=id,number,date,description,voucherType(id,name),version
```

**Required**: `dateFrom` and `dateTo`.

### Create

**Critical rules:**

1. Use `amountGross` and `amountGrossCurrency` (not `amount`)
2. Set `row` on each posting (row 0 is reserved for system-generated postings)
3. Include `vatType` when the account is VAT-locked
4. Postings must balance (sum of amountGross = 0)
5. Each posting needs its own `date`

Working example (revenue received into bank):

```json
{
  "date": "2026-03-20",
  "description": "Mottatt betaling for tjenester",
  "postings": [
    {
      "row": 1,
      "date": "2026-03-20",
      "description": "Mottatt betaling",
      "account": { "id": 424227363 },
      "amountGross": 1000.00,
      "amountGrossCurrency": 1000.00
    },
    {
      "row": 2,
      "date": "2026-03-20",
      "description": "Salgsinntekt",
      "account": { "id": 424227497 },
      "vatType": { "id": 6 },
      "amountGross": -1000.00,
      "amountGrossCurrency": -1000.00
    }
  ]
}
```

Query param: `POST /ledger/voucher?sendToLedger=true` sends directly to ledger. Without it, voucher goes to non-posted state.

**Common failure modes:**

| Error | Cause | Fix |
|-------|-------|-----|
| "Et bilag kan ikke registreres uten posteringer" + "rad 0 er systemgenererte" | Missing `row` field (defaults to 0) | Set `row: 1`, `row: 2`, etc. |
| "Kontoen X er låst til mva-kode Y" | Account has locked vatType | Include `vatType: {"id": Y}` on the posting |
| Postings don't balance | Sum of amountGross != 0 | Ensure debit + credit sum to zero |

### Supplier invoice as voucher

Since `POST /supplierInvoice` does not exist, create supplier invoices as vouchers:

```json
{
  "date": "2026-03-15",
  "description": "Leverandørfaktura - Kontorrekvisita",
  "vendorInvoiceNumber": "INV-2026-001",
  "postings": [
    {
      "row": 1,
      "date": "2026-03-15",
      "description": "Kontorrekvisita innkjøp",
      "account": { "id": 424227524 },
      "amountGross": 5000.00,
      "amountGrossCurrency": 5000.00
    },
    {
      "row": 2,
      "date": "2026-03-15",
      "description": "Leverandørgjeld",
      "account": { "id": 424227422 },
      "supplier": { "id": 108240565 },
      "amountGross": -5000.00,
      "amountGrossCurrency": -5000.00
    }
  ]
}
```

Key: account 2400 (Leverandørgjeld) is a VENDOR ledger account, so you MUST include `supplier.id` on that posting.

### Ledger type rules

If `ledgerType` is not `GENERAL`, attach the matching entity:

| ledgerType | Required field on posting |
|-----------|-------------------------|
| `CUSTOMER` | `customer: {"id": ...}` |
| `VENDOR` | `supplier: {"id": ...}` |
| `EMPLOYEE` | `employee: {"id": ...}` |
| `ASSET` | `asset: {"id": ...}` |

### Reverse a voucher

```text
PUT /ledger/voucher/{id}/:reverse?date=2026-03-20
```

Date is a **query parameter**, not body. Returns the new reverse voucher with description "Reversering av bilag X-YYYY".

### Voucher actions (change state)

```text
PUT /ledger/voucher/{id}/:sendToLedger         → send a non-posted voucher to ledger
PUT /ledger/voucher/{id}/:sendToInbox           → send voucher back to inbox
```

### Import document as voucher

Upload a PDF/PNG/JPEG/TIFF to create one or more vouchers automatically:

```text
POST /ledger/voucher/importDocument?split=false
multipart/form-data:
  file = <PDF/PNG/JPEG/TIFF>
  description = "Optional description"
```

- `split=true` creates one voucher per page for multi-page documents
- Returns a list of created vouchers
- EHF/XML import requires agreement with Tripletex

### Import GBAT10

```text
POST /ledger/voucher/importGbat10
multipart/form-data:
  generateVatPostings = true/false
  file = <GBAT10 file>
  encoding = "utf-8"
```

Returns a list of imported voucher IDs.

### Non-posted and reception

```text
GET /ledger/voucher/%3EnonPosted?dateFrom=...&dateTo=...
GET /ledger/voucher/%3EvoucherReception?dateFrom=...&dateTo=...
GET /ledger/voucher/%3EexternalVoucherNumber?externalVoucherNumber=...
```

### Open posts (unpaid items)

Two endpoints for finding open (unpaid/unsettled) postings:

```text
GET /ledger/openPost?date=2026-03-31
GET /ledger/posting/openPost?date=2026-03-31
```

Both require `date` (format `yyyy-MM-dd`, to and excluding). Optional filters:

| Filter | Description |
|--------|-------------|
| `accountId` | Ledger account |
| `supplierId` | Supplier |
| `customerId` | Customer |
| `employeeId` | Employee |
| `departmentId` | Department |
| `projectId` | Project |
| `productId` | Product |
| `accountNumberFrom` / `accountNumberTo` | Account range (posting endpoint only) |

Use these to find outstanding supplier invoices, unpaid customer invoices, or unsettled employee expenses. The `openPost` endpoints return postings that have not been matched/settled against each other.

## 7. Order and Invoice (Full Flow)

### Step 1: Create order

```json
POST /order
{
  "customer": { "id": 108240560 },
  "orderDate": "2026-03-20",
  "deliveryDate": "2026-03-31",
  "orderLines": [
    {
      "product": { "id": 84384169 },
      "description": "Konsulenttjeneste mars 2026",
      "count": 10,
      "unitPriceExcludingVatCurrency": 1000.00
    }
  ]
}
```

**Required**: `deliveryDate` (not just `orderDate`). Missing it gives `422 / 18000 "deliveryDate: Kan ikke være null"`.

Order lines can reference a product or be freeform. If referencing a product, the product's price and vatType are used as defaults.

Project-linked order shape was live-tested:

```json
POST /order
{
  "customer": { "id": 108245913 },
  "project": { "id": 401958522 },
  "orderDate": "2026-03-20",
  "deliveryDate": "2026-03-31",
  "orderLines": [
    {
      "description": "Pagamento por etapa",
      "count": 1,
      "unitPriceExcludingVatCurrency": 92100
    }
  ]
}
```

This returned `201`, and a subsequent `GET /order/{id}` confirmed both the `project` link and created `orderLines`.

### Step 2: Create invoice from order

```json
POST /invoice?sendToCustomer=false
{
  "invoiceDate": "2026-03-31",
  "invoiceDueDate": "2026-04-14",
  "customer": { "id": 108240560 },
  "orders": [{ "id": 401954281 }]
}
```

**Prerequisites:**
- The company MUST have a bank account number set on its bank account (account 1920). Without it: `422 "Faktura kan ikke opprettes før selskapet har registrert et bankkontonummer"`.
- Set bank account: `PUT /ledger/account/{id}` with `bankAccountNumber` (must be a valid Norwegian bank account number passing MOD11 check).

**Required fields**: `invoiceDate`, `invoiceDueDate`, `orders` (non-empty).

Query params:
- `sendToCustomer=false` - don't email
- `sendToCustomer=true` - send via customer's `invoiceSendMethod`
- `paymentTypeId` - prepaid invoice payment type
- `paidAmount` - prepaid amount

### Step 3 (optional): Create credit note

```text
PUT /invoice/{id}/:createCreditNote?date=2026-03-31&comment=Kreditnota
```

Date and comment are **query parameters**. Returns new credit note invoice with `creditedInvoice` pointing to original.

### Cancel/reverse a payment (returned by bank)

When a payment has been returned by the bank and you need to restore the invoice to unpaid, reverse the **payment voucher** (not the invoice itself). A credit note would zero out the entire invoice, which is wrong — the customer still owes the money.

**Do NOT use `create_credit_note` for payment reversals** — that cancels the invoice entirely.

Flow:
1. Find customer: `GET /customer?organizationNumber=...`
2. Find invoice: `GET /invoice?customerId={id}&invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31&fields=id,invoiceNumber,amount,amountOutstanding,isCreditNote`
3. Find payment voucher: `GET /ledger/posting?dateFrom=2020-01-01&dateTo=2030-12-31` filtered by the invoice — look for postings on account 1920 (bank). The voucher ID is on the posting's `voucher.id` field.
4. Reverse the payment voucher: `PUT /ledger/voucher/{paymentVoucherId}/:reverse?date=2026-03-20`

This restores the original invoice's `amountOutstanding`.

**Important**: `/invoice` collection reads REQUIRE `invoiceDateFrom` + `invoiceDateTo`. Without them you get `400`.

### Alternative: Invoice via order action

```text
PUT /order/{id}/:invoice
```

Creates an invoice directly from the order.

### Register payment on invoice

All parameters are **query parameters**, not body:

```text
PUT /invoice/{id}/:payment?paymentDate=2026-04-01&paymentTypeId=32817129&paidAmount=10000
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `paymentDate` | Yes | Date of payment (YYYY-MM-DD) |
| `paymentTypeId` | Yes | Payment type ID. Use `GET /invoice/paymentType` to find valid IDs. |
| `paidAmount` | Yes | Amount paid in company currency |
| `paidAmountCurrency` | No | Amount in invoice currency (for foreign currency invoices) |

Common payment type IDs: "Kontant" (id=32817128), "Betalt til bank" (id=32817129). These vary per sandbox - always look them up first.

### Send invoice

```text
PUT /invoice/{id}/:send?sendType=EMAIL
PUT /invoice/{id}/:send?sendType=EMAIL&overrideEmailAddress=kunde@example.no
```

Send types: `EMAIL`, `EHF`, `AVTALEGIRO`, `EFAKTURA`, `VIPPS`, `PAPER`, `MANUAL`.

### Read invoices

```text
GET /invoice?invoiceDateFrom=2026-01-01&invoiceDateTo=2026-12-31&count=10&fields=id,invoiceNumber,invoiceDate,invoiceDueDate,customer(id,name),amount,amountOutstanding,isCreditNote,version
```

Filter parameters: `id`, `invoiceDateFrom` (required), `invoiceDateTo` (required), `customerId`, `invoiceNumber`, `kid`, `voucherId`, `isNotSent`.

**Common search patterns:**

Find invoices for a specific customer:
```text
GET /invoice?customerId={id}&invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31&fields=id,invoiceNumber,amount,amountOutstanding,isCreditNote
```

Find original (non-credit) invoices only — critical when issuing credit notes or reversing payments:
```text
GET /invoice?customerId={id}&invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31&fields=id,invoiceNumber,amount,amountOutstanding,isCreditNote
```
Then filter results in code for `isCreditNote=false` (there is no query-level filter for this).

**Important**: `invoiceDateFrom` + `invoiceDateTo` are always required. Without them the API returns `400`.

## 8. Supplier Invoice

### Read

```text
GET /supplierInvoice?invoiceDateFrom=2026-01-01&invoiceDateTo=2026-12-31&count=10
```

### Create

There is NO `POST /supplierInvoice`. Options for creating supplier invoices:

1. **Via voucher** (tested, works): Create a voucher with expense debit + supplier credit posting (see section 6 above)
2. **Via incoming invoice BETA** (`POST /incomingInvoice`): Requires special permissions. In this sandbox it returned `403`.
3. **Via document upload**: Upload to voucher inbox, then process

### Incoming invoice BETA schema (for reference)

```json
POST /incomingInvoice
{
  "invoiceHeader": {
    "vendorId": 108240565,
    "invoiceDate": "2026-03-15",
    "dueDate": "2026-04-15",
    "invoiceNumber": "INV-2026-001",
    "invoiceAmount": 5000.00,
    "currencyId": 1,
    "description": "Kontorrekvisita"
  },
  "orderLines": [
    {
      "externalId": "line-1",
      "row": 1,
      "description": "Kontorrekvisita",
      "accountId": 424227524,
      "amountInclVat": 5000.00,
      "vatTypeId": 0
    }
  ]
}
```

Note: `externalId` is required on each order line. This returned `403` in our sandbox.

### Supplier invoice actions

```text
PUT /supplierInvoice/{id}/:approve
PUT /supplierInvoice/{id}/:reject
PUT /supplierInvoice/:approve (batch, with ?id=...)
PUT /supplierInvoice/{invoiceId}/:changeDimension
POST /supplierInvoice/{invoiceId}/:addPayment
PUT /supplierInvoice/voucher/{id}/postings (update postings)
```

## 9. Travel Expense

### Read

```text
GET /travelExpense?count=10&fields=id,title,date,state,employee(id,firstName,lastName),amount,version
```

Optional filters: `employeeId`, `departmentId`, `projectId`, `departureDateFrom`, `returnDateTo`, `state`.

### Delete

```text
DELETE /travelExpense/{id}   → returns 204
```

### Create

```json
POST /travelExpense
{
  "employee": { "id": 18442131 },
  "title": "Reise til kundemøte Oslo",
  "project": { "id": 401954298 },
  "department": { "id": 900557 },
  "travelDetails": {
    "isForeignTravel": false,
    "isDayTrip": true,
    "departureDate": "2026-03-18",
    "returnDate": "2026-03-18",
    "departureFrom": "Bergen",
    "destination": "Oslo",
    "departureTime": "07:00",
    "returnTime": "20:00",
    "purpose": "Kundemøte"
  }
}
```

**Critical**: `departureDate` and `returnDate` go inside `travelDetails`, NOT at root level. Putting them at root gives `422 / 16000 "Feltet eksisterer ikke i objektet"`.

### Add cost to travel expense

First, look up cost categories and payment types:

```text
GET /travelExpense/costCategory?count=25   (use "description" field, not "name")
GET /travelExpense/paymentType?count=10
```

Common cost categories:
- Fly (id=32817103), Hotell (id=32817106), Buss (id=32817097)
- Drivstoff (id=32817099), Bomavgift (id=32817096)
- Kontorrekvisita (id=32817089), Telefon (id=32817094)

Payment types:
- Privat utlegg (id=32817087) - employee paid out of pocket

```json
POST /travelExpense/cost
{
  "travelExpense": { "id": 11142790 },
  "costCategory": { "id": 32817103 },
  "paymentType": { "id": 32817087 },
  "date": "2026-03-18",
  "comments": "Flybillett Bergen-Oslo tur-retur",
  "amountCurrencyIncVat": 2500.00,
  "currency": { "id": 1 }
}
```

**Required**: `paymentType` is mandatory. Missing it gives `422 / 18000 "paymentType: Kan ikke være null"`.

**VAT warning**: If the company is not VAT-registered, costs with VAT-carrying cost categories will fail on delivery/approval. Ensure cost categories use `vatType.id=0` or `6` for non-VAT companies.

### Mileage allowance

Look up valid rate rows with:

```text
GET /travelExpense/rate?type=MILEAGE_ALLOWANCE&dateFrom=2026-03-01&dateTo=2026-04-01&count=5&fields=id,rate,zone,rateCategory(id,name,type)
```

Live result in this sandbox:

- `rateType.id=25891`, `rateCategory.id=743` ("Bil"), `rate=5.3`

```json
POST /travelExpense/mileageAllowance
{
  "travelExpense": { "id": 11144436 },
  "rateType": { "id": 25891 },
  "rateCategory": { "id": 743 },
  "date": "2026-03-18",
  "departureLocation": "Oslo sentrum",
  "destination": "Drammen sentrum",
  "km": 50
}
```

Update shape (also live-tested):

```json
PUT /travelExpense/mileageAllowance/6871431
{
  "id": 6871431,
  "version": 1,
  "travelExpense": { "id": 11144436 },
  "rateType": { "id": 25891 },
  "rateCategory": { "id": 743 },
  "date": "2026-03-18",
  "departureLocation": "Oslo sentrum",
  "destination": "Drammen sentrum",
  "km": 60
}
```

Returned amount after update: `318.0` (`60 km * 5.3`).

Use `rateType` and `rateCategory` objects, not `rateTypeId` / `rateCategoryId` in the body. Updates require a fresh `version`.

### Per-diem compensation

Look up valid rate rows with:

```text
GET /travelExpense/rate?type=PER_DIEM&dateFrom=2026-03-01&dateTo=2026-04-01&count=10&fields=id,rate,zone,rateCategory(id,name,type),breakfastDeductionRate,lunchDeductionRate,dinnerDeductionRate
```

Live result in this sandbox:

- `rateType.id=25886`, `rateCategory.id=738` ("Dagsreise 6-12 timer - innland"), `rate=397.0`

Working create body:

```json
POST /travelExpense/perDiemCompensation
{
  "travelExpense": { "id": 11144436 },
  "rateType": { "id": 25886 },
  "rateCategory": { "id": 738 },
  "location": "Drammen",
  "count": 1,
  "isDeductionForBreakfast": false,
  "isDeductionForLunch": false,
  "isDeductionForDinner": false
}
```

Working update body:

```json
PUT /travelExpense/perDiemCompensation/1590702
{
  "id": 1590702,
  "version": 1,
  "travelExpense": { "id": 11144436 },
  "rateType": { "id": 25886 },
  "rateCategory": { "id": 738 },
  "location": "Drammen",
  "count": 2,
  "isDeductionForBreakfast": false,
  "isDeductionForLunch": false,
  "isDeductionForDinner": false
}
```

Returned amount after update: `794.0` (`2 * 397.0`).

This differs from regular cost lines:

- Per-diem uses `rateType` + `rateCategory` + `count`
- It does not use `paymentType`, `costCategory`, or `currency`
- Amount is derived from the rate table, not sent directly

### Selecting the correct per-diem rate category

The `GET /travelExpense/rate?type=PER_DIEM` endpoint returns multiple rate categories. Select based on trip type:

| Trip type | Rate category name pattern | Typical use |
|-----------|---------------------------|-------------|
| Day trip 6-12 hours | "Dagsreise 6-12 timer - innland" | Single-day domestic travel |
| Day trip >12 hours | "Dagsreise over 12 timer - innland" | Long single-day domestic travel |
| Overnight domestic | "Overnatting - innland" | Multi-day domestic trips (the most common for 2+ day trips) |
| Foreign travel | "Utland" categories | International trips |

For multi-day trips (e.g., "Reisen varte 4 dager"), use the **overnight** rate category, NOT the day-trip category. Set `count` to the number of overnight stays (typically `days - 1` for return trips, or `days` if the prompt says "X days with per-diem").

When the task specifies a daily rate (e.g., "dagssats 800 NOK"), this is for reference — the actual per-diem amount comes from Tripletex's rate table. If the task's daily rate differs from the system rate, the closest matching rate category should still be used.

### Travel expense lifecycle

```text
PUT /travelExpense/:deliver?id={id}      → submit for approval
PUT /travelExpense/:approve?id={id}      → approve
PUT /travelExpense/:unapprove?id={id}    → unapprove
PUT /travelExpense/:undeliver?id={id}    → return to draft
PUT /travelExpense/:createVouchers?id={id} → create accounting voucher
```

## 10. Timesheet

### Create entry

First, look up activities for the project:

```text
GET /activity/%3EforTimeSheet?projectId={projectId}&employeeId={employeeId}&date=2026-03-20
```

Then create the entry:

```json
POST /timesheet/entry
{
  "employee": { "id": 18442131 },
  "project": { "id": 401954298 },
  "activity": { "id": 5588996 },
  "date": "2026-03-20",
  "hours": 7.5,
  "comment": "Arbeid på prosjektet"
}
```

**Important**: The `activity.id` must be valid for the project. Using a non-existent activity ID gives `404 / 6000`.

### Timesheet summary and approval

```text
GET /timesheet/entry/%3EtotalHours?employeeId=...&startDate=...&endDate=...
PUT /timesheet/month/:approve?id=...
PUT /timesheet/month/:complete?id=...
PUT /timesheet/week/:approve?id=...
```

## 11. Balance Sheet and Ledger Summary

### Balance sheet

```text
GET /balanceSheet?dateFrom=2026-01-01&dateTo=2026-03-31&accountNumberFrom=1000&accountNumberTo=9999
```

Returns per-account: `balanceIn`, `balanceChange`, `balanceOut`, `startDate`, `endDate`.

### General ledger

```text
GET /ledger?dateFrom=2026-01-01&dateTo=2026-03-31
```

Returns per-account: `sumAmount`, `openingBalance`, `closingBalance`, with links to individual postings.

### Postings

```text
GET /ledger/posting?dateFrom=2026-01-01&dateTo=2026-12-31&fields=id,date,description,account(number,name),amount,supplier(id,name),customer(id,name)
```

Filter by: `accountId`, `supplierId`, `customerId`, `employeeId`, `departmentId`, `projectId`, `openPostings`, `accountNumberFrom`, `accountNumberTo`.

### Accounting periods

```text
GET /ledger/accountingPeriod?count=20&fields=id,start,end,isClosed
```

Returns 12 monthly periods for the fiscal year. Each has `start`, `end`, `isClosed`.

## 12. Employee

### Read

```text
GET /employee?count=10&fields=id,firstName,lastName,email,employeeNumber,displayName,userType,phoneNumberMobile,phoneNumberWork,department(id,name),version
```

Filter parameters: `id`, `firstName`, `lastName`, `email`, `employeeNumber`, `departmentId`, `allowInformationRegistration`, `includeContacts`, `hasSystemAccess`.

Most common search: `GET /employee?email=ola@example.org&fields=id,firstName,lastName,email` — used when tasks reference employees by email.

### Create

**Required fields**: `firstName`, `lastName`, `userType`, `department`.

```json
{
  "firstName": "Ola",
  "lastName": "Nordmann",
  "email": "ola@example.org",
  "userType": "STANDARD",
  "department": { "id": 837916 }
}
```

`userType` values: `STANDARD` (reduced access), `EXTENDED` (full access), `NO_ACCESS`.

**Common failure modes:**

| Error | Cause | Fix |
|-------|-------|-----|
| "Brukertype kan ikke være '0' eller tom" | Missing `userType` | Set `userType: "STANDARD"` or another value |
| "department.id: Feltet må fylles ut" | Missing department | Include `department: {"id": ...}`. Use `GET /department` to find one. Every sandbox has a default "Avdeling" department. |

The default "Avdeling" department exists in every fresh sandbox. Fetch it with `GET /department?count=1`.

### Update

PUT requires `id`, `version`, and **`dateOfBirth`** (becomes required on update even if not set at creation).

```json
PUT /employee/{id}
{
  "id": 18562517,
  "version": 2,
  "firstName": "Ola",
  "lastName": "Nordmann",
  "email": "ola@example.org",
  "dateOfBirth": "1990-01-15",
  "userType": "STANDARD",
  "department": { "id": 837916 },
  "phoneNumberMobile": "91234567",
  "phoneNumberWork": "22334455"
}
```

Live failure on `2026-03-20`:

- `PUT /employee/{id}` without `dateOfBirth` returned `422 / 18000`
- Validation message: `dateOfBirth: Feltet må fylles ut.`

Live-tested working body:

```json
PUT /employee/18567149
{
  "id": 18567149,
  "version": 1,
  "firstName": "Emp",
  "lastName": "Research-1fa659 WithDOB",
  "email": "emp-research-1fa659@example.org",
  "userType": "STANDARD",
  "department": { "id": 904510 },
  "dateOfBirth": "1991-02-03"
}
```

Gotcha: after employee creation, `GET /employee/{id}` may return `userType: null` even when the create payload used `"STANDARD"`. Sending `"STANDARD"` again on update still works.

### Competition workflow (create employee with start date)

Tasks often ask to create an employee with a start date (e.g., "opprett vedkommende som ansatt med startdato 13. January 2026"). The `startDate` is NOT on the employee entity — it lives on the **employment** record.

Full flow:
1. `GET /department?count=1` → get default department id
2. `POST /employee` with `firstName`, `lastName`, `email`, `userType: "STANDARD"`, `department.id`
3. `PUT /employee/{id}` with `dateOfBirth` (required for salary operations later)
4. `POST /division` (if none exists) → division id (see Section 15)
5. `POST /employee/employment` with `employee.id`, `startDate`, `division.id`, `isMainEmployer: true`
6. `PUT /employee/entitlement/:grantEntitlementsByTemplate?employeeId={id}&template=ALL_PRIVILEGES` (optional)

Steps 4-5 are needed if salary operations will follow. For simple employee creation without salary, steps 1-3 suffice.

### Grant entitlements (set admin role)

Use the template-based endpoint to grant permission sets:

```text
PUT /employee/entitlement/:grantEntitlementsByTemplate?employeeId={id}&template=ALL_PRIVILEGES
```

Available templates:

| Template | Description |
|----------|-------------|
| `ALL_PRIVILEGES` | Full administrator access (51 entitlements including `ROLE_ADMINISTRATOR`) |
| `NONE_PRIVILEGES` | Remove all privileges |
| `INVOICING_MANAGER` | Invoice management access |
| `PERSONELL_MANAGER` | Personnel/HR access |
| `ACCOUNTANT` | Accountant access |
| `AUDITOR` | Auditor access |
| `DEPARTMENT_LEADER` | Department leader access |

Returns `204 No Content` on success. Verify with `GET /employee/entitlement?employeeId={id}&fields=id,name`.

Typical "create employee as admin" flow:
1. `GET /department?count=1` → get default department id
2. `POST /employee` with `userType: "STANDARD"` and `department`
3. `PUT /employee/entitlement/:grantEntitlementsByTemplate?employeeId={id}&template=ALL_PRIVILEGES`

### Employment

Create employment record for an employee:

```json
POST /employee/employment
{
  "employee": { "id": 18562517 },
  "startDate": "2026-03-01",
  "endDate": "2027-03-01"
}
```

Fetch: `GET /employee/employment?employeeId={id}`.

## 13. Contact

Contacts are external people linked to a customer.

### Read

```text
GET /contact?count=10&fields=id,version,firstName,lastName,email,phoneNumberMobile,customer(id,name)
```

Filters: `customerId`, `firstName`, `lastName`, `email`.

### Create

```json
POST /contact
{
  "firstName": "Rita",
  "lastName": "Research-1fa659",
  "email": "research-1fa659@example.org",
  "customer": { "id": 108250089 }
}
```

Minimal tested body: `firstName`, `lastName`, and `customer.id`. `email` is optional but worked.

### Update

Working update body:

```json
PUT /contact/18567146
{
  "id": 18567146,
  "version": 0,
  "firstName": "Rita",
  "lastName": "Research-1fa659 Updated",
  "email": "research-1fa659@example.org",
  "customer": { "id": 108250089 },
  "phoneNumberMobile": "90000000"
}
```

### Delete

There is no single-item `DELETE /contact/{id}` in this spec. Contact deletion is exposed through the batch endpoint:

```text
DELETE /contact/list
```

### Competition workflow

1. `POST /customer` or find the customer -> customer id
2. `POST /contact` with `firstName`, `lastName`, `customer.id`
3. `GET /contact` or `GET /contact/{id}` -> fetch `version`
4. `PUT /contact/{id}` with `id`, `version`, and changed fields

## 14. Address

Addresses are embedded in customers, suppliers, and employees. Update them via the parent entity:

```json
PUT /customer/{id}
{
  "id": 108250089,
  "version": 1,
  "name": "Research-1fa659 Customer AS",
  "postalAddress": {
    "addressLine1": "Researchgata 1",
    "postalCode": "0150",
    "city": "Oslo"
  }
}
```

Address fields: `addressLine1`, `addressLine2`, `postalCode`, `city`, `country: {"id": ...}`.

Live-tested supplier example:

```json
PUT /supplier/108250091
{
  "id": 108250091,
  "version": 1,
  "name": "Research-1fa659 Supplier AS",
  "postalAddress": {
    "addressLine1": "Leverandørveien 2",
    "postalCode": "5003",
    "city": "Bergen",
    "country": { "id": 129 }
  }
}
```

Live-tested employee example:

```json
PUT /employee/18567149
{
  "id": 18567149,
  "version": 2,
  "firstName": "Emp",
  "lastName": "Research-1fa659 WithDOB",
  "email": "emp-research-1fa659@example.org",
  "userType": null,
  "department": { "id": 904510 },
  "dateOfBirth": "1991-02-03",
  "address": {
    "addressLine1": "Ansattveien 3",
    "postalCode": "7010",
    "city": "Trondheim",
    "country": { "id": 129 }
  }
}
```

Important live behavior:

- You do **not** need to send the nested address `id`
- Updating the parent creates a new address row with a new address id
- Look up the `country.id` first; hard-coding ids is risky. The payload above worked because this sandbox accepted `country.id=129`

## 15. Salary

Salary in Tripletex is handled through salary transactions containing payslips with salary specifications.

### Prerequisites

The minimal tested prerequisite chain was:

1. Create or find a division (`virksomhet`)
2. Create an employment linked to that division
3. Post the salary transaction

The employee used in the live test also had `dateOfBirth` set already. The salary transaction succeeded **before** any `employment/details` row existed.

### Division bootstrap

Working create body (live-tested):

```json
POST /division
{
  "name": "Research Division 9913",
  "startDate": "2026-01-01",
  "organizationNumber": "973949913",
  "municipalityDate": "2026-01-01",
  "municipality": { "id": 262 }
}
```

Required fields discovered by validation:

- `municipality`
- `organizationNumber`
- `municipalityDate`

### Employment bootstrap

Working create body (live-tested):

```json
POST /employee/employment
{
  "employee": { "id": 18567149 },
  "startDate": "2026-01-01",
  "division": { "id": 108249253 },
  "isMainEmployer": true
}
```

Returned defaults:

- `taxDeductionCode` auto-filled to `loennFraHovedarbeidsgiver`
- `employmentDetails` started as an empty array

Error-driven gotcha:

- Missing `division.id` returned `422 / 18000` with `Arbeidsforholdet må knyttes til en virksomhet/underenhet.`

### Optional employment details

Not required for the minimal salary transaction, but this payload also worked:

```json
POST /employee/employment/details
{
  "employment": { "id": 2796849 },
  "date": "2026-01-01",
  "employmentType": "ORDINARY",
  "employmentForm": "PERMANENT",
  "remunerationType": "MONTHLY_WAGE",
  "workingHoursScheme": "NOT_SHIFT",
  "occupationCode": { "id": 3 },
  "percentageOfFullTimeEquivalent": 100,
  "annualSalary": 12000,
  "payrollTaxMunicipalityId": { "id": 262 }
}
```

### Salary types

```text
GET /salary/type?count=50&fields=id,number,name,description
```

Key salary types (IDs vary per sandbox — always look them up):

| Number | Name | Use for |
|--------|------|---------|
| 2000 | Fastlønn | Monthly salary / base pay |
| 2001 | Timelønn | Hourly wage |
| 2002 | Bonus | One-time bonus |
| 2003 | Faste tillegg | Fixed allowances |
| 2005 | Overtidsgodtgjørelse | Overtime pay |
| 6000 | Skattetrekk | Tax deduction (auto-generated if generateTaxDeduction=true) |

### Create salary transaction (run payroll)

```json
POST /salary/transaction?generateTaxDeduction=true
{
  "date": "2026-03-20",
  "month": 3,
  "year": 2026,
  "payslips": [
    {
      "employee": { "id": 18567149 },
      "date": "2026-03-20",
      "month": 3,
      "year": 2026,
      "specifications": [
        {
          "salaryType": { "id": 69041874 },
          "rate": 1000,
          "count": 1,
          "amount": 1000
        }
      ]
    }
  ]
}
```

**Key rules:**
- `generateTaxDeduction=true` as query parameter to auto-calculate tax
- Each specification needs `salaryType.id`, `rate`, `count`, and `amount`
- For monthly salary: `rate` = monthly amount, `count` = 1, `amount` = rate × count
- For bonus: use salary type "Bonus" (number 2002), same pattern
- `month` and `year` on both the transaction and each payslip
- Look up salary type IDs first: `GET /salary/type?count=50&fields=id,number,name`
- Find the employee by email: `GET /employee?email=...&fields=id,firstName,lastName`

**Common failure modes:**

| Error | Cause | Fix |
|-------|-------|-----|
| "Ansatt nr.  er ikke registrert med et arbeidsforhold i perioden." | Employee has no employment in that period | Create `POST /employee/employment` first |
| "Arbeidsforholdet er ikke knyttet mot en virksomhet" | Employee missing division on employment | Create division + link to employment |
| "Feltet må fylles ut" on employee.dateOfBirth | Employee update requires dateOfBirth | Set dateOfBirth on the employee |

### Read salary data

```text
GET /salary/payslip?count=10&fields=id,date,employee(id,firstName,lastName),grossAmount,specifications(salaryType(id,name),amount,rate,count)
GET /salary/compilation?employeeId={id}&year=2026
```

### Competition workflow

1. `GET /company/divisions` or `POST /division` -> division id
2. Ensure employee has `dateOfBirth`
3. `POST /employee/employment` with `employee.id`, `startDate`, `division.id`
4. `GET /salary/type?count=50&fields=id,number,name` -> find salary type ids
5. `POST /salary/transaction?generateTaxDeduction=true`

## 16. Payment Types

### Outgoing payments

```text
GET /ledger/paymentTypeOut?count=20
```

Returns: "Manuelt betalt nettbank", "Betalingsbilag (på papir)", "Bankkort", "Kontant".

### Invoice payment types

```text
GET /invoice/paymentType?count=20
```

Returns: "Kontant" (id=32817128), "Betalt til bank" (id=32817129).

## 17. Asset (Permission-gated)

All asset endpoints returned `403 / 9000` in this sandbox:

```text
GET /asset
GET /asset/assetsExist
GET /asset/balanceAccountsSum
```

Probe `GET /asset` first. If `403`, skip asset logic entirely.

## 18. Custom Accounting Dimensions

Free accounting dimensions are exposed as `ledger/accountingDimensionName` + `ledger/accountingDimensionValue`, and voucher postings link them through `freeAccountingDimension1`, `freeAccountingDimension2`, or `freeAccountingDimension3`.

### Read

```text
GET /ledger/accountingDimensionName
GET /ledger/accountingDimensionName/{id}
GET /ledger/accountingDimensionValue/search
GET /ledger/accountingDimensionValue/{id}
```

### Create dimension definition

Working body (tested `2026-03-20`):

```json
POST /ledger/accountingDimensionName
{
  "dimensionName": "ResearchDim-8d6b9a",
  "description": "Research custom dimension",
  "active": true
}
```

Returned: `dimensionIndex: 1`.

### Update dimension definition

Working body:

```json
PUT /ledger/accountingDimensionName/1091
{
  "id": 1091,
  "version": 0,
  "dimensionName": "Dim8d6b9a",
  "description": "Updated description",
  "active": true
}
```

Gotcha: `dimensionName` has a max length of `20`. A longer update returned `422`.

### Create dimension value

Working body:

```json
POST /ledger/accountingDimensionValue
{
  "dimensionIndex": 1,
  "displayName": "ResearchDim-8d6b9a Value",
  "number": "6b9a",
  "showInVoucherRegistration": true,
  "active": true
}
```

### Update dimension value

There is no single-item `PUT /ledger/accountingDimensionValue/{id}`. Updates go through the bulk endpoint:

```json
PUT /ledger/accountingDimensionValue/list
[
  {
    "id": 15711,
    "version": 0,
    "displayName": "Dim value updated",
    "number": "6b9a",
    "dimensionIndex": 1,
    "showInVoucherRegistration": false,
    "active": true,
    "position": 1
  }
]
```

### Link dimension value to voucher posting

Working voucher body:

```json
POST /ledger/voucher?sendToLedger=true
{
  "date": "2026-03-20",
  "description": "ResearchDim-8d6b9a voucher",
  "postings": [
    {
      "row": 1,
      "date": "2026-03-20",
      "description": "Debit bank",
      "account": { "id": 424227363 },
      "amountGross": 100.0,
      "amountGrossCurrency": 100.0
    },
    {
      "row": 2,
      "date": "2026-03-20",
      "description": "Credit sales with dimension",
      "account": { "id": 424227497 },
      "vatType": { "id": 6 },
      "freeAccountingDimension1": { "id": 15711 },
      "amountGross": -100.0,
      "amountGrossCurrency": -100.0
    }
  ]
}
```

Verified with:

```text
GET /ledger/voucher/{id}?fields=postings(id,row,freeAccountingDimension1(id,displayName,dimensionIndex))
```

### Competition workflow

1. `POST /ledger/accountingDimensionName` -> get `dimensionIndex`
2. `POST /ledger/accountingDimensionValue` with that `dimensionIndex` -> value id
3. `POST /ledger/voucher?sendToLedger=true` and set `freeAccountingDimension1/2/3`
4. `GET /ledger/voucher/{id}` to confirm the posting carries the dimension

## 19. Bank Reconciliation

### Read

```text
GET /bank/reconciliation?count=5&fields=id,account(id,number),accountingPeriod(id,name,number),type,isClosed,bankAccountClosingBalanceCurrency
GET /bank/reconciliation/settings
GET /ledger/accountingPeriod?count=5&fields=id,name,number,start,end,isClosed
```

### Create settings

Working body:

```json
POST /bank/reconciliation/settings
{
  "numberOfMatchesPerPage": "ITEMS_50"
}
```

### Create manual reconciliation

Working body:

```json
POST /bank/reconciliation
{
  "account": { "id": 424227363 },
  "accountingPeriod": { "id": 23753398 },
  "type": "MANUAL",
  "bankAccountClosingBalanceCurrency": 0
}
```

### Update manual reconciliation

Working body:

```json
PUT /bank/reconciliation/12705344
{
  "id": 12705344,
  "version": 0,
  "account": { "id": 424227363 },
  "accountingPeriod": { "id": 23753398 },
  "type": "MANUAL",
  "bankAccountClosingBalanceCurrency": 25
}
```

Prerequisites:

- A bank account ledger account (`1920` worked)
- An accounting period id from `/ledger/accountingPeriod`

### Competition workflow

1. `GET /ledger/account?number=1920` -> bank account id
2. `GET /ledger/accountingPeriod` -> period id
3. `POST /bank/reconciliation`
4. `PUT /bank/reconciliation/{id}` as balances change

## 20. Document and Attachment Upload

### Relevant endpoints

```text
POST /ledger/voucher/{voucherId}/attachment
DELETE /ledger/voucher/{voucherId}/attachment
POST /travelExpense/{travelExpenseId}/attachment
GET /travelExpense/{travelExpenseId}/attachment
DELETE /travelExpense/{travelExpenseId}/attachment
POST /salary/transaction/{id}/attachment
POST /purchaseOrder/{id}/attachment
POST /documentArchive/customer/{id}
POST /documentArchive/supplier/{id}
POST /documentArchive/project/{id}
POST /documentArchive/reception
```

The upload endpoints use `multipart/form-data` with a single required field named `file`.

### Voucher attachment

Working request shape:

```text
POST /ledger/voucher/608830659/attachment
multipart/form-data:
  file = <PDF/PNG/JPEG/TIFF>
```

The live test uploaded [`Lønnsslipp-3-2026.pdf`](/Users/andreasklaeboe/repos/nm-ai-zypp/src/ai_accounting_agent/Lønnsslipp-3-2026.pdf) and returned `attachment.id=1024155649`.

### Travel expense attachment

Working request shape:

```text
POST /travelExpense/11144436/attachment
multipart/form-data:
  file = <PDF/PNG/JPEG/TIFF>
```

The same PDF upload returned `attachment.id=1024155653`.

Gotchas:

- The body is multipart, not JSON
- Tripletex appends pages if the voucher already has a PDF attachment
- Non-PDF image formats are converted to PDF

## 21. Webhook Subscriptions

### Read

```text
GET /event
GET /event/subscription
GET /event/subscription/{id}
GET /event/{eventType}
```

`GET /event` returned keys such as `customer.create`, `customer.update`, `voucher.create`, and `contact.update`.

### Create

Working body:

```json
POST /event/subscription
{
  "event": "customer.create",
  "targetUrl": "https://example.com/tripletex-research",
  "fields": "id,version,name",
  "authHeaderName": "Authorization",
  "authHeaderValue": "Bearer research-token"
}
```

### Update

Working body:

```json
PUT /event/subscription/1
{
  "id": 1,
  "version": 0,
  "event": "customer.create",
  "targetUrl": "https://example.com/tripletex-research-updated",
  "fields": "id,name,customerNumber",
  "authHeaderName": "Authorization",
  "authHeaderValue": "Bearer research-token-2"
}
```

Gotchas:

- `targetUrl` must be absolute HTTPS
- `authHeaderValue` is write-only and is not echoed back
- Event names must come from `GET /event`

## 22. Purchase Orders

### Endpoints

```text
GET /purchaseOrder
POST /purchaseOrder
GET /purchaseOrder/{id}
PUT /purchaseOrder/{id}
DELETE /purchaseOrder/{id}
POST /purchaseOrder/orderline
GET /purchaseOrder/goodsReceipt
POST /purchaseOrder/goodsReceipt
PUT /purchaseOrder/{id}/:send
PUT /purchaseOrder/{id}/:sendByEmail
POST /purchaseOrder/{id}/attachment
```

### Sandbox status

Collection reads work:

```text
GET /purchaseOrder?deliveryDateFrom=2026-03-01&deliveryDateTo=2026-03-31&count=5
```

returned `200` with an empty list.

Create did **not** work in this sandbox. The closest live probe was:

```json
POST /purchaseOrder
{
  "supplier": { "id": 108250091 },
  "deliveryDate": "2026-03-31",
  "creationDate": "2026-03-20",
  "comments": "Research purchase order",
  "ourContact": { "id": 18442131 }
}
```

This returned `422` with "Oppdatering av dette feltet er ikke tillatt", which strongly suggests the relevant Logistics module/write permission is not enabled in this sandbox.

## 23. Reminder and Recurring Billing Notes

### Reminder endpoints

```text
PUT /invoice/{id}/:createReminder
GET /reminder?dateFrom=...&dateTo=...
GET /reminder/{id}
GET /reminder/{reminderId}/pdf
```

### Sandbox findings

- `GET /reminder` requires `dateFrom` and `dateTo`
- `ReminderDTO` uses `reminderDate`, not `date`
- `PUT /invoice/{id}/:createReminder` was **not** successful in this sandbox: every spec-listed `type` enum (`SOFT_REMINDER`, `REMINDER`, `NOTICE_OF_DEBT_COLLECTION`, `DEBT_COLLECTION`) returned `422` `"type: Ugyldig verdi."`

Recurring invoice automation is exposed on the `order` DTO via subscription fields such as `isSubscription`, `subscriptionDuration`, `subscriptionPeriodsOnInvoice`, and related approval actions, but that flow was not live-validated here.

## 24. Currency and Exchange Rates

### Read endpoints

```text
GET /currency?count=25&fields=id,code,displayName
GET /currency/{id}
GET /currency/{id}/rate
GET /currency/{fromCurrencyID}/exchangeRate
GET /currency/{fromCurrencyID}/{toCurrencyID}/exchangeRate
```

There is no currency-rate create/update endpoint in this v2 spec. Foreign-currency behavior shows up instead on payment and invoice actions through fields such as `paidAmountCurrency`.

## 25. Supplier Invoice Approval Workflow

### Relevant endpoints

```text
GET /supplierInvoice?invoiceDateFrom=...&invoiceDateTo=...
GET /supplierInvoice/forApproval
GET /supplierInvoice/{id}
PUT /supplierInvoice/{id}/:approve
PUT /supplierInvoice/{id}/:reject
PUT /supplierInvoice/{invoiceId}/:changeDimension
POST /supplierInvoice/{invoiceId}/:addPayment
PUT /supplierInvoice/voucher/{id}/postings
```

Sandbox status on `2026-03-20`:

- `GET /supplierInvoice?...` returned `200` with no rows
- `GET /supplierInvoice/forApproval` returned `200` with no rows
- There is still no `POST /supplierInvoice`
- The alternative `POST /incomingInvoice` remained permission-blocked earlier in this sandbox

Operationally, the competition-safe create path is still supplier invoice as voucher unless a sandbox explicitly exposes incoming invoices.

## 26. Inventory / Stock

### Endpoints

```text
GET /inventory
POST /inventory
GET /inventory/{id}
PUT /inventory/{id}
DELETE /inventory/{id}
GET /inventory/location
POST /inventory/location
GET /inventory/stocktaking
POST /inventory/stocktaking
GET /product/inventoryLocation
POST /product/inventoryLocation
GET /purchaseOrder/goodsReceipt
POST /purchaseOrder/goodsReceipt
```

### Sandbox status

`GET /inventory?count=5` returned `200` and showed one default inventory: `Hovedlager`.

Create did not work in this sandbox:

```json
POST /inventory
{
  "name": "Research Inventory",
  "number": "INV-RES-1",
  "description": "Research stock location"
}
```

This returned `422` saying updates to those fields were not allowed, so treat inventory write access as module/permission-gated unless a fresh competition sandbox proves otherwise.

## 27. Year-End and Period Closing

### Relevant endpoints

```text
GET /ledger/accountingPeriod
GET /ledger/closeGroup
PUT /ledger/posting/:closePostings
PUT /ledger/voucher/historical/:closePostings
GET /yearEnd/penneo/casefiles
POST /yearEnd/penneo/casefiles
POST /yearEnd/penneo/documents
PUT /yearEnd/researchAndDevelopment2024
```

Only the read side was live-tested here. `GET /ledger/accountingPeriod` worked with fields `id,name,number,start,end,isClosed`. No closing action was executed in the shared sandbox because period-closing is destructive and competition sandboxes are fresh anyway.

## 28. Country Lookup

```text
GET /country?count=300&fields=id,code,name
GET /country/{id}
```

Filters: `id`, `code`, `isDisabled`, `supportedInZtl`.

Use when setting addresses with `country: {"id": ...}`. Norway is typically `id=129` but always look up to be safe.

## 29. Municipality Lookup

```text
GET /municipality?count=500&fields=id,name,number
GET /municipality/query?query=Oslo
```

Optional: `includePayrollTaxZones=true` (default).

Needed when creating divisions for salary (the `municipality` field on `POST /division`). Also useful for employment details with `payrollTaxMunicipalityId`.

## 30. Bank Statements

### Read

```text
GET /bank/statement?accountId={accountId}&count=10
GET /bank/statement/{id}
```

Filter by `id`, `accountId`, `fileFormats`.

### Transactions

```text
GET /bank/statement/transaction?count=50
GET /bank/statement/transaction/{id}
GET /bank/statement/transaction/{id}/details
```

### Import

```text
POST /bank/statement/import?bankId={bankId}&accountId={accountId}&fromDate=2026-01-01&toDate=2026-03-31
multipart/form-data:
  file = <bank statement file>
  fileFormat = TELEPAY / DNB_CSV / NORDEA_XLSX / FOKUS_CSV / SPAREBANK1_CSV / DANSKE_CSV / ...
  externalId = "optional external id"
```

Supported file formats from the spec: `TELEPAY`, `EXTGML`, `AGROSGML`, `FOKUS_CSV`, `FOKUS_XLSX`, `DNB_CSV`, `SPAREBANK1_CSV`, `HANDELSBANKEN_CSV`, `NORDEA_CSV`, `NORDEA_XLSX`, `DANSKE_CSV`, `DANSKE_XLSX`, `PARETO`.

### Delete

```text
DELETE /bank/statement/{id}
```

## 31. Supplier/Customer Combined Search

```text
GET /supplierCustomer/search?query=searchterm
```

Searches across both active suppliers and customers simultaneously. Useful when you need to find a business partner but don't know if they're registered as supplier, customer, or both.

## 32. Travel Expense Sub-resources

### Accommodation allowance

```text
GET /travelExpense/accommodationAllowance/{id}
POST /travelExpense/accommodationAllowance
PUT /travelExpense/accommodationAllowance/{id}
DELETE /travelExpense/accommodationAllowance/{id}
```

### Driving stops (for mileage routes)

```text
GET /travelExpense/drivingStop/{id}
POST /travelExpense/drivingStop
PUT /travelExpense/drivingStop/{id}
DELETE /travelExpense/drivingStop/{id}
```

### Passengers (mileage allowance passengers)

```text
GET /travelExpense/passenger?mileageAllowance={mileageAllowanceId}
GET /travelExpense/passenger/{id}
POST /travelExpense/passenger
PUT /travelExpense/passenger/{id}
DELETE /travelExpense/passenger/{id}
```

### Cost participants (entertainment expenses)

```text
GET /travelExpense/costParticipant/{costId}/costParticipants
GET /travelExpense/costParticipant/{id}
POST /travelExpense/costParticipant
POST /travelExpense/costParticipant/createCostParticipantAdvanced
PUT /travelExpense/costParticipant/{id}
DELETE /travelExpense/costParticipant/{id}
```

### Rate categories and zones

```text
GET /travelExpense/rateCategory?count=50
GET /travelExpense/rateCategory/{id}
GET /travelExpense/rateCategoryGroup?count=50
GET /travelExpense/rateCategoryGroup/{id}
GET /travelExpense/zone?count=50
GET /travelExpense/zone/{id}
GET /travelExpense/settings
```

### Copy travel expense

```text
PUT /travelExpense/:copy?id={id}
```

## 33. Timesheet Sub-resources

### Allocated time

```text
GET /timesheet/allocated?employeeId=...&startDate=...&endDate=...
GET /timesheet/allocated/{id}
PUT /timesheet/allocated/{id}
PUT /timesheet/allocated/{id}/:approve
PUT /timesheet/allocated/{id}/:unapprove
PUT /timesheet/allocated/:approveList
PUT /timesheet/allocated/:unapproveList
```

### Time clock

```text
GET /timesheet/timeClock?employeeId=...
GET /timesheet/timeClock/{id}
GET /timesheet/timeClock/present?employeeId=...
PUT /timesheet/timeClock/:start?activityId=...&projectId=...&employeeId=...
PUT /timesheet/timeClock/{id}/:stop
```

### Company holidays

```text
GET /timesheet/companyHoliday?from=...&count=...
GET /timesheet/companyHoliday/{id}
POST /timesheet/companyHoliday
PUT /timesheet/companyHoliday/{id}
DELETE /timesheet/companyHoliday/{id}
```

### Recent items

```text
GET /timesheet/entry/>recentActivities?projectId=...&employeeId=...
GET /timesheet/entry/>recentProjects?employeeId=...
```

### Timesheet settings

```text
GET /timesheet/settings
```

### Monthly/weekly timesheet approval

```text
GET /timesheet/month/{id}
GET /timesheet/month/byMonthNumber?employeeIds=...&monthYear=2026-03
PUT /timesheet/month/:approve?id=...&employeeId=...&monthYear=...
PUT /timesheet/month/:unapprove?id=...
PUT /timesheet/month/:complete?id=...
PUT /timesheet/month/:reopen?id=...
GET /timesheet/week?employeeIds=...&yearFrom=...&yearTo=...
PUT /timesheet/week/:approve?id=...
PUT /timesheet/week/:unapprove?id=...
PUT /timesheet/week/:complete?id=...
PUT /timesheet/week/:reopen?id=...
```

### Salary type specifications

```text
GET /timesheet/salaryTypeSpecification/{id}
GET /timesheet/salaryProjectTypeSpecification/{id}
```

## 34. Employee Sub-resources

### Hourly cost and rate

```text
GET /employee/hourlyCostAndRate/{id}
PUT /employee/hourlyCostAndRate/{id}
```

### Next of kin

```text
GET /employee/nextOfKin/{id}
POST /employee/nextOfKin
PUT /employee/nextOfKin/{id}
```

### Employee categories

```text
GET /employee/category?count=50
GET /employee/category/{id}
POST /employee/category
PUT /employee/category/{id}
DELETE /employee/category/{id}
```

### Employee preferences

```text
GET /employee/preferences/>loggedInEmployeePreferences
GET /employee/preferences/{id}
PUT /employee/preferences/{id}
PUT /employee/preferences/:changeLanguage?languageCode=...
```

### Standard time

```text
GET /employee/standardTime/{id}
GET /employee/standardTime/byDate?employeeId=...&date=...
POST /employee/standardTime
PUT /employee/standardTime/{id}
```

### Search employees and contacts

```text
GET /employee/searchForEmployeesAndContacts?searchString=...
```

### Leave of absence

```text
GET /employee/employment/leaveOfAbsence/{id}
POST /employee/employment/leaveOfAbsence
PUT /employee/employment/leaveOfAbsence/{id}
GET /employee/employment/leaveOfAbsenceType
```

### Employment type enums

```text
GET /employee/employment/employmentType
GET /employee/employment/employmentType/employmentEndReasonType
GET /employee/employment/employmentType/employmentFormType
GET /employee/employment/employmentType/maritimeEmploymentType
GET /employee/employment/employmentType/salaryType
GET /employee/employment/employmentType/scheduleType
GET /employee/employment/remunerationType
GET /employee/employment/workingHoursScheme
GET /employee/employment/occupationCode?count=500
```

## 35. Customer Sub-resources

### Customer categories

```text
GET /customer/category?count=50
GET /customer/category/{id}
POST /customer/category
PUT /customer/category/{id}
DELETE /customer/category/{id}
```

### Delivery addresses

```text
GET /deliveryAddress?count=50
GET /deliveryAddress/{id}
POST /deliveryAddress
PUT /deliveryAddress/{id}
```

## 36. Product Sub-resources

### Product groups

```text
GET /product/group?count=50
GET /product/group/{id}
POST /product/group
PUT /product/group/{id}
DELETE /product/group/{id}
GET /product/groupRelation?count=50
GET /product/groupRelation/{id}
POST /product/groupRelation
DELETE /product/groupRelation/{id}
```

### Product units

```text
GET /product/unit?count=50
GET /product/unit/{id}
POST /product/unit
PUT /product/unit/{id}
DELETE /product/unit/{id}
GET /product/unit/master?count=50
GET /product/unit/master/{id}
```

### Supplier products

```text
GET /product/supplierProduct?count=50&productId=...
GET /product/supplierProduct/{id}
POST /product/supplierProduct
PUT /product/supplierProduct/{id}
DELETE /product/supplierProduct/{id}
```

### Discount groups

```text
GET /product/discountGroup?count=50
GET /product/discountGroup/{id}
POST /product/discountGroup
PUT /product/discountGroup/{id}
DELETE /product/discountGroup/{id}
```

### Product images

```text
POST /product/{id}/image   (multipart/form-data, field: file)
DELETE /product/{id}/image
```

### Product inventory locations

```text
GET /product/inventoryLocation?count=50
GET /product/inventoryLocation/{id}
POST /product/inventoryLocation
PUT /product/inventoryLocation/{id}
DELETE /product/inventoryLocation/{id}
```

### Logistics settings

```text
GET /product/logisticsSettings
PUT /product/logisticsSettings
```

### Product prices

```text
GET /product/productPrice?productId=...
```

## 37. Project Sub-resources

### Hourly rates

```text
GET /project/hourlyRates/{id}
GET /project/hourlyRates/projectSpecificRates?projectId=...
GET /project/hourlyRates/projectSpecificRates/{id}
POST /project/hourlyRates/projectSpecificRates
PUT /project/hourlyRates/projectSpecificRates/{id}
PUT /project/hourlyRates/updateOrAddHourRates
DELETE /project/hourlyRates/deleteByProjectIds?ids=...
```

### Project participants

```text
GET /project/participant?count=50&projectId=...
GET /project/participant/{id}
POST /project/participant
PUT /project/participant/{id}
DELETE /project/participant/{id}
```

### Project tasks

```text
GET /project/task?projectId=...
POST /project/task
```

### Project activities

```text
GET /project/projectActivity?count=50&projectId=...
GET /project/projectActivity/{id}
POST /project/projectActivity
DELETE /project/projectActivity/{id}
```

### Project categories

```text
GET /project/category?count=50
GET /project/category/{id}
POST /project/category
PUT /project/category/{id}
```

### Project order lines

```text
GET /project/orderline?count=50&projectId=...
GET /project/orderline/{id}
POST /project/orderline
PUT /project/orderline/{id}
DELETE /project/orderline/{id}
```

### Project settings and import

```text
GET /project/settings
POST /project/import
GET /project/template/{id}
```

### Project control forms

```text
GET /project/controlForm?count=50&projectId=...
GET /project/controlForm/{id}
GET /project/controlFormType?count=50
GET /project/controlFormType/{id}
```

### Project period reports

```text
GET /project/{id}/period/budgetStatus?dateFrom=...&dateTo=...
GET /project/{id}/period/hourlistReport?dateFrom=...&dateTo=...
GET /project/{id}/period/invoiced?dateFrom=...&dateTo=...
GET /project/{id}/period/invoicingReserve?dateFrom=...&dateTo=...
GET /project/{id}/period/monthlyStatus?dateFrom=...&dateTo=...
GET /project/{id}/period/overallStatus?dateFrom=...&dateTo=...
GET /project/>forTimeSheet?employeeId=...&date=...
```

### Resource plan and subcontracts

```text
GET /project/resourcePlanBudget
GET /project/subcontract?count=50&projectId=...
GET /project/subcontract/{id}
POST /project/subcontract
PUT /project/subcontract/{id}
```

## 38. Order Sub-resources

### Order lines

```text
GET /order/orderline?count=50&orderId=...
GET /order/orderline/{id}
POST /order/orderline
PUT /order/orderline/{id}
DELETE /order/orderline/{id}
GET /order/orderline/orderLineTemplate?orderId=...
PUT /order/orderline/{id}/:pickLine
PUT /order/orderline/{id}/:unpickLine
```

### Invoice multiple orders

```text
PUT /order/:invoiceMultipleOrders?id=orderId1,orderId2,...
```

[BETA] Creates a single customer invoice from multiple orders. All orders must share the same customer, currency, due date, receiver email, attn., and SMS notification number.

Query params: `id` (comma-separated order IDs), `invoiceDate`, `sendToCustomer`.

### Order groups

```text
GET /order/orderGroup/{id}
PUT /order/orderGroup/{id}
DELETE /order/orderGroup/{id}
```

### PDF downloads

```text
GET /order/orderConfirmation/{orderId}/pdf
GET /order/packingNote/{orderId}/pdf
```

### Order actions

```text
PUT /order/{id}/:attach                          → attach document
PUT /order/{id}/:approveSubscriptionInvoice      → approve subscription
PUT /order/{id}/:unApproveSubscriptionInvoice    → unapprove subscription
PUT /order/sendInvoicePreview/{orderId}           → send invoice preview
PUT /order/sendOrderConfirmation/{orderId}        → send order confirmation
PUT /order/sendPackingNote/{orderId}              → send packing note
```

## 39. Invoice Sub-resources

### Invoice details

```text
GET /invoice/details?invoiceDateFrom=...&invoiceDateTo=...
GET /invoice/details/{id}
```

### Invoice PDF

```text
GET /invoice/{invoiceId}/pdf
```

### Invoice remarks

```text
GET /invoiceRemark/{id}
```

## 40. Salary Sub-resources

### Payslip PDF

```text
GET /salary/payslip/{id}/pdf
```

### Salary settings

```text
GET /salary/settings
```

### Holiday settings

```text
GET /salary/settings/holiday?count=50
GET /salary/settings/holiday/{id}
POST /salary/settings/holiday
PUT /salary/settings/holiday/{id}
```

### Pension schemes

```text
GET /salary/settings/pensionScheme?count=50
GET /salary/settings/pensionScheme/{id}
POST /salary/settings/pensionScheme
PUT /salary/settings/pensionScheme/{id}
DELETE /salary/settings/pensionScheme/{id}
```

### Salary compilation (annual summary)

```text
GET /salary/compilation?employeeId=...&year=2026
GET /salary/compilation/pdf?employeeId=...&year=2026
```

### Salary reconciliation endpoints

```text
GET /salary/payrollTax/reconciliation/context
GET /salary/payrollTax/reconciliation/{reconciliationId}/overview
GET /salary/payrollTax/reconciliation/{reconciliationId}/paymentsOverview
GET /salary/taxDeduction/reconciliation/context
GET /salary/taxDeduction/reconciliation/{reconciliationId}/overview
GET /salary/taxDeduction/reconciliation/{reconciliationId}/balanceAndOwedAmount
GET /salary/taxDeduction/reconciliation/{reconciliationId}/paymentsOverview
GET /salary/financeTax/reconciliation/context
GET /salary/financeTax/reconciliation/{reconciliationId}/overview
GET /salary/financeTax/reconciliation/{reconciliationId}/paymentsOverview
GET /salary/holidayAllowance/reconciliation/context
GET /salary/holidayAllowance/reconciliation/{reconciliationId}/holidayAllowanceDetails
GET /salary/holidayAllowance/reconciliation/{reconciliationId}/holidayAllowanceSummary
GET /salary/mandatoryDeduction/reconciliation/context
GET /salary/mandatoryDeduction/reconciliation/{reconciliationId}/overview
GET /salary/mandatoryDeduction/reconciliation/{reconciliationId}/paymentsOverview
```

### Salary transaction attachments

```text
POST /salary/transaction/{id}/attachment         (multipart/form-data, field: file)
DELETE /salary/transaction/{id}/deleteAttachment
GET /salary/transaction/{id}/attachment/list
```

## 41. SAF-T Export and Import

### Export

```text
GET /saft/exportSAFT?year=2026
```

[BETA] Creates a SAF-T (Standard Audit File - Tax) XML export for the account. This is the standardized format for exchanging accounting data in Norway.

### Import

```text
POST /saft/importSAFT
multipart/form-data:
  file = <SAF-T XML file>
```

[BETA] Imports accounting data from a SAF-T XML file.

## 42. Result Budget

```text
GET /resultbudget?periodDateFrom=...&periodDateTo=...
GET /resultbudget/company?periodDateFrom=...&periodDateTo=...
GET /resultbudget/department/{id}?periodDateFrom=...&periodDateTo=...
GET /resultbudget/employee/{id}?periodDateFrom=...&periodDateTo=...
GET /resultbudget/product/{id}?periodDateFrom=...&periodDateTo=...
GET /resultbudget/project/{id}?periodDateFrom=...&periodDateTo=...
```

Returns budget vs actual comparison data by entity. Useful for project budget tracking and financial reporting.

## 43. Division (Virksomhet)

Divisions are organizational sub-units required for salary/payroll processing.

### Read

```text
GET /company/divisions?count=50
GET /division/{id}
```

### Create

```json
POST /division
{
  "name": "Division Name",
  "startDate": "2026-01-01",
  "organizationNumber": "973949913",
  "municipalityDate": "2026-01-01",
  "municipality": { "id": 262 }
}
```

Required: `municipality`, `organizationNumber`, `municipalityDate`.

### Update / Delete

```text
PUT /division/{id}
DELETE /division/{id}
```

## 44. Pension

```text
GET /pension?count=50
```

Read-only pension data.

## 45. Company Settings

### Altinn settings

```text
GET /company/settings/altinn
PUT /company/settings/altinn
```

## 46. Document Archive

### Upload to archive

```text
POST /documentArchive/customer/{customerId}       (multipart/form-data)
POST /documentArchive/supplier/{supplierId}       (multipart/form-data)
POST /documentArchive/project/{projectId}         (multipart/form-data)
POST /documentArchive/employee/{employeeId}       (multipart/form-data)
POST /documentArchive/account/{accountId}         (multipart/form-data)
POST /documentArchive/reception                   (multipart/form-data)
```

### Read / Delete

```text
GET /documentArchive/{id}
DELETE /documentArchive/{id}
GET /document/{id}
GET /document/{id}/content
```

## 47. Voucher Inbox, Status, Messages, and Approval

### Voucher inbox

```text
GET /voucherInbox/inboxCount
```

Returns the number of vouchers in the inbox. Useful for monitoring unprocessed items.

### Voucher status

```text
GET /voucherStatus?voucherId=...&from=...&count=...
GET /voucherStatus/{id}
```

Used to coordinate integration processes. Requires setup by Tripletex; currently supports debt collection.

### Voucher messages

```text
GET /voucherMessage?voucherId=...
POST /voucherMessage
```

### Voucher approval

```text
GET /voucherApprovalListElement/{id}
```

## 48. Attestation / Approval Workflow

```text
POST /attestation/:addApprover
GET /attestation/addApproverPermission
GET /attestation/companyModules
```

## 49. Ledger Sub-resources

### Annual accounts

```text
GET /ledger/annualAccount?count=50
GET /ledger/annualAccount/{id}
```

### Close groups

```text
GET /ledger/closeGroup?count=50&dateFrom=...&dateTo=...
GET /ledger/closeGroup/{id}
```

### Posting rules

```text
GET /ledger/postingRules?count=50
```

### Posting by date

```text
GET /ledger/postingByDate?dateFrom=...&dateTo=...
```

### Opening balance

```text
GET /ledger/voucher/openingBalance
GET /ledger/voucher/openingBalance/>correctionVoucher
POST /ledger/voucher/openingBalance
```

[BETA] Add an opening balance on the given date. All movements before this date will be "zeroed out" in a separate correction voucher.

### Voucher PDF

```text
GET /ledger/voucher/{voucherId}/pdf
GET /ledger/voucher/{voucherId}/pdf/{fileName}
```

### Voucher options

```text
GET /ledger/voucher/{id}/options
```

### Historical vouchers

```text
GET /ledger/voucher/historical/historical
GET /ledger/voucher/historical/employee
POST /ledger/voucher/historical/{voucherId}/attachment
PUT /ledger/voucher/historical/:closePostings
PUT /ledger/voucher/historical/:reverseHistoricalVouchers
```

## 50. VAT Returns

```text
GET /vatReturns/comment?from=...&count=...
GET /vatReturns/comment/>all
POST /vatReturns/comment
```

## 51. VAT Term Size Settings

```text
GET /vatTermSizeSettings?count=50
GET /vatTermSizeSettings/{id}
```

## 52. Subscription Management

```text
GET /subscription/packages
PUT /subscription/cancel
PUT /subscription/reactivate
```

## 53. Balance Reconciliation (Annual)

```text
GET /balance/reconciliation/annual/context
GET /balance/reconciliation/attachment/{attachmentId}/pdf
GET /balance/reconciliation/{reconciliationId}/account/{accountId}/vouchers
```

## 54. Accountant Dashboard and Office

### News

```text
GET /accountantDashboard/news?count=50
GET /accountantDashboard/news/tags
```

### Reconciliation controls (accountant office)

```text
GET /accountingOffice/reconciliations/{reconciliationId}/control
PUT /accountingOffice/reconciliations/{reconciliationId}/control/:controlReconciliation
PUT /accountingOffice/reconciliations/{reconciliationId}/control/:reconcile
PUT /accountingOffice/reconciliations/{reconciliationId}/control/:requestControl
```

## 55. Token and Session Management

### Session

```text
POST /token/session/:create?consumerToken=...&employeeToken=...&expirationDate=...
GET /token/session/>whoAmI
DELETE /token/session/{token}
```

### Employee token

```text
POST /token/employee/:create?tokenName=...&expirationDate=...
```

### Consumer token

```text
GET /token/consumer/byToken?token=...
```

## 56. CRM Prospects

```text
GET /crm/prospect?count=50
GET /crm/prospect/{id}
POST /crm/prospect
PUT /crm/prospect/{id}
DELETE /crm/prospect/{id}
```

## 57. Pickup Points and Transport Types

### Pickup points

```text
GET /pickupPoint?count=50
GET /pickupPoint/{id}
```

### Transport types

```text
GET /transportType?count=50
GET /transportType/{id}
```

## 58. Incoming Invoice (BETA)

```text
GET /incomingInvoice/search?invoiceDateFrom=...&invoiceDateTo=...
POST /incomingInvoice
GET /incomingInvoice/{voucherId}
POST /incomingInvoice/{voucherId}/addPayment
```

Note: This is BETA and was permission-blocked (403) in the sandbox. See section 8 for the working alternative (supplier invoice as voucher).

## 59. Purchase Order Sub-resources

### Deviations

```text
GET /purchaseOrder/deviation?count=50&purchaseOrderId=...
GET /purchaseOrder/deviation/{id}
POST /purchaseOrder/deviation
PUT /purchaseOrder/deviation/{id}
DELETE /purchaseOrder/deviation/{id}
PUT /purchaseOrder/deviation/{id}/:approve
PUT /purchaseOrder/deviation/{id}/:deliver
PUT /purchaseOrder/deviation/{id}/:undeliver
```

### Goods receipt

```text
GET /purchaseOrder/goodsReceipt?count=50&purchaseOrderId=...
GET /purchaseOrder/goodsReceipt/{id}
POST /purchaseOrder/goodsReceipt
PUT /purchaseOrder/goodsReceipt/{id}/:confirm
PUT /purchaseOrder/goodsReceipt/{id}/:receiveAndConfirm
PUT /purchaseOrder/goodsReceipt/{id}/:registerGoodsReceipt
```

### Goods receipt lines

```text
GET /purchaseOrder/goodsReceiptLine?count=50&purchaseOrderGoodsReceiptId=...
GET /purchaseOrder/goodsReceiptLine/{id}
POST /purchaseOrder/goodsReceiptLine
PUT /purchaseOrder/goodsReceiptLine/{id}
DELETE /purchaseOrder/goodsReceiptLine/{id}
```

### Order lines

```text
GET /purchaseOrder/orderline?count=50&purchaseOrderId=...
GET /purchaseOrder/orderline/{id}
POST /purchaseOrder/orderline
PUT /purchaseOrder/orderline/{id}
DELETE /purchaseOrder/orderline/{id}
```

### Incoming invoice relation

```text
GET /purchaseOrder/purchaseOrderIncomingInvoiceRelation?count=50&purchaseOrderId=...
GET /purchaseOrder/purchaseOrderIncomingInvoiceRelation/{id}
POST /purchaseOrder/purchaseOrderIncomingInvoiceRelation
DELETE /purchaseOrder/purchaseOrderIncomingInvoiceRelation/{id}
```

### Attachment

```text
POST /purchaseOrder/{id}/attachment              (multipart/form-data)
GET /purchaseOrder/{id}/attachment/list
```

## 60. Bank Sub-resources

### Bank accounts

```text
GET /bank/{id}
```

### Reconciliation match and suggestions

```text
GET /bank/reconciliation/match?count=50&bankReconciliationId=...
GET /bank/reconciliation/match/{id}
POST /bank/reconciliation/match
PUT /bank/reconciliation/match/:suggest?bankReconciliationId=...
GET /bank/reconciliation/match/count?bankReconciliationId=...
POST /bank/reconciliation/match/query
DELETE /bank/reconciliation/match/{id}
GET /bank/reconciliation/matches/counter
```

### Reconciliation payment types

```text
GET /bank/reconciliation/paymentType?count=50
GET /bank/reconciliation/paymentType/{id}
```

### Last reconciliation

```text
GET /bank/reconciliation/>last?accountId=...
GET /bank/reconciliation/>lastClosed?accountId=...&after=...
GET /bank/reconciliation/closedWithUnmatchedTransactions?accountId=...
```

### Adjustment

```text
POST /bank/reconciliation/{id}/:adjustment
```

### Unmatched transactions CSV

```text
GET /bank/reconciliation/transactions/unmatched:csv?bankReconciliationId=...
```

---

## Suggested Agent Playbook

When receiving a task:

### Phase 1: Discovery (do these every time)

```text
GET /token/session/%3EwhoAmI                    → get employeeId, companyId
GET /ledger/vatSettings                          → VAT registration status
GET /ledger/account?count=200&fields=id,number,name,type  → account chart
GET /ledger/vatType?count=60&fields=id,name,number,percentage  → VAT codes
GET /ledger/voucherType?count=20&fields=id,name  → voucher types
GET /currency?count=25&fields=id,code             → currencies
GET /employee?count=10&fields=id,firstName,lastName  → employees
GET /department?count=20&fields=id,name,departmentNumber  → departments
```

### Phase 2: Domain-specific lookups

| Task domain | Pre-fetch |
|------------|-----------|
| Customer invoice | `GET /customer`, `GET /product` |
| Fixed-price project billing | `GET /customer`, `GET /employee`, `GET /project` |
| Supplier invoice | `GET /supplier`, accounts 2400 + expense accounts |
| Voucher/posting | Account IDs, vatTypes, ledgerTypes |
| Travel expense | `GET /travelExpense/costCategory`, `GET /travelExpense/paymentType` |
| Timesheet | `GET /activity/%3EforTimeSheet`, `GET /project` |
| Salary | `GET /salary/type`, `GET /employee` |

### Phase 3: Execute

- **Creating**: POST with minimal required fields, let Tripletex fill defaults
- **Updating**: GET first (need `id` + `version`), then PUT
- **Vouchers**: Always set `row >= 1`, use `amountGross`, include `vatType` for locked accounts
- **Invoices**: Create order first (with `deliveryDate`), then POST to `/invoice`
- **Fixed-price projects**: update the project with `customer`, `projectManager`, `isFixedPrice`, and `fixedprice`, then create a project-linked order for the milestone amount
- **Actions**: Use `PUT /resource/:action?param=value` (params often as query, not body)

### Phase 4: Verify

- After creating a voucher: `GET /ledger/voucher/{id}?fields=*` to confirm postings
- After creating an invoice: `GET /invoice?invoiceDateFrom=...` to confirm
- Check balance: `GET /balanceSheet?dateFrom=...&dateTo=...`

---

## Coverage Summary

Fully tested end-to-end:

- Auth/session discovery, company info, VAT settings
- Customer: create with full fields, auto-fills confirmed
- Supplier: create with fields, auto-fills confirmed
- Product: create, vatType restrictions tested
- Department: minimal create with only `name`
- Project: create with required fields (name, projectManager, startDate)
- Project billing: versioned `PUT /project/{id}` with `customer`, `projectManager`, `isFixedPrice`, `fixedprice`
- Ledger voucher: create with balanced postings, vatType locking, supplier attachment, free accounting dimension posting
- Supplier invoice via voucher: debit expense + credit 2400 with supplier
- Order: create with order lines, deliveryDate, and optional project link
- Invoice: full order → invoice flow, credit note creation
- Voucher reversal: via query param
- Travel expense: create with nested travelDetails, add cost with paymentType, mileage allowance, per-diem compensation, attachment upload
- Timesheet entry: create with activity lookup
- Employee: update requires `dateOfBirth`
- Contact: create and update
- Address updates: customer, supplier, employee
- Salary bootstrap: division create, employment create, salary transaction create, employment details create
- Bank reconciliation: settings create, reconciliation create and update
- Webhook subscription: create and update
- Balance sheet and ledger summary: read
- Accounting periods: read
- Reference data: accounts, vatTypes, voucherTypes, currencies, departments

Tested but permission-blocked:

- Asset endpoints (403)
- Incoming invoice BETA (403)

Present in spec but not writable in this sandbox:

- Purchase orders (`POST /purchaseOrder` returned 422 field-write denial)
- Inventory (`POST /inventory` returned 422 field-write denial)
- Reminder creation (`PUT /invoice/{id}/:createReminder` returned 422 invalid type despite spec enums)

Read-only / partially probed:

- Supplier invoice approval endpoints (no supplier invoices existed to approve)
- Currency / exchange-rate endpoints (read only in v2 spec)
- Year-end and close-period endpoints (read only; no destructive close action executed)
- Document archive uploads beyond voucher/travel attachment endpoints

---

## Common Task Workflows (Competition Patterns)

These map to the 30 task types in the competition. Each starts from a fresh empty sandbox.

### "Create employee" (Tier 1)

```
1. GET /department?count=1                           → get default department id
2. POST /employee  {firstName, lastName, email, userType: "STANDARD", department: {id}}
3. (optional) PUT /employee/entitlement/:grantEntitlementsByTemplate?employeeId={id}&template=ALL_PRIVILEGES
```

### "Create customer" (Tier 1)

```
1. POST /customer  {name, email, organizationNumber, ...}
```

### "Create product" (Tier 1)

```
1. POST /product  {name, number, priceExcludingVatCurrency}
```

### "Create project" (Tier 1)

```
1. GET /employee?count=1                             → get employee id for projectManager
2. POST /project  {name, projectManager: {id}, startDate}
```

### "Create invoice for customer" (Tier 2)

```
1. POST /customer  {name, ...}                       → customerId
2. POST /product   {name, priceExcludingVatCurrency}  → productId  (if needed)
3. POST /order     {customer: {id}, orderDate, deliveryDate, orderLines: [{product: {id}, count, unitPriceExcludingVatCurrency}]}  → orderId
4. GET /ledger/account?isBankAccount=true             → check bank account has bankAccountNumber set
5. POST /invoice?sendToCustomer=false  {invoiceDate, invoiceDueDate, customer: {id}, orders: [{id}]}
```

**Prerequisite**: Company must have a bank account number on account 1920. If not set: `PUT /ledger/account/{id}` with `bankAccountNumber`.

### "Configure fixed-price project and invoice a milestone" (Tier 2-3)

```
1. GET /customer?organizationNumber=... or create/find customer  → customerId
2. GET /employee?email=...                                       → projectManagerId
3. GET /project?name=...                                         → projectId
4. PUT /project/{id}  {id, version, customer: {id}, projectManager: {id}, isFixedPrice: true, fixedprice: totalAmount}
5. POST /order  {customer: {id}, project: {id}, orderDate, deliveryDate, orderLines: [{description, count: 1, unitPriceExcludingVatCurrency: milestoneAmount}]}
6. POST /invoice?sendToCustomer=false  {invoiceDate, invoiceDueDate, customer: {id}, orders: [{id}]}
```

Avoid `GET /order?count=1` or other bare collection reads while discovering this flow.

### "Register payment" (Tier 2)

```
1. (Create invoice first - see above)
2. GET /invoice/paymentType?count=10                  → get paymentTypeId
3. PUT /invoice/{id}/:payment?paymentDate=...&paymentTypeId=...&paidAmount=...
```

### "Create supplier invoice / book vendor bill" (Tier 2)

```
1. POST /supplier  {name, ...}                       → supplierId
2. GET /ledger/account?number=4000                    → expense account id
3. GET /ledger/account?number=2400                    → supplier payable account id
4. POST /ledger/voucher?sendToLedger=true  {date, description, vendorInvoiceNumber, postings: [
     {row:1, date, account: {id: expense}, amountGross: amount, amountGrossCurrency: amount},
     {row:2, date, account: {id: 2400}, supplier: {id}, amountGross: -amount, amountGrossCurrency: -amount}
   ]}
```

### "Create travel expense" (Tier 2-3)

```
1. GET /employee?count=1                              → employeeId
2. POST /travelExpense  {employee: {id}, title, travelDetails: {isDayTrip, departureDate, returnDate, departureFrom, destination, purpose}}
3. GET /travelExpense/costCategory?count=25            → get cost category ids
4. GET /travelExpense/paymentType?count=5              → get payment type id
5. POST /travelExpense/cost  {travelExpense: {id}, costCategory: {id}, paymentType: {id}, date, amountCurrencyIncVat, currency: {id: 1}}
6. PUT /travelExpense/:deliver?id={id}                → submit for approval
```

### "Add mileage allowance or per-diem to travel expense" (Tier 2-3)

```
1. POST /travelExpense  {employee: {id}, title, travelDetails: {...}}      → travelExpenseId
2. GET /travelExpense/rate?type=MILEAGE_ALLOWANCE&dateFrom=...&dateTo=...   → rateTypeId + rateCategoryId for mileage
3. POST /travelExpense/mileageAllowance  {travelExpense: {id}, rateType: {id}, rateCategory: {id}, date, departureLocation, destination, km}
4. GET /travelExpense/rate?type=PER_DIEM&dateFrom=...&dateTo=...            → rateTypeId + rateCategoryId for per-diem
5. POST /travelExpense/perDiemCompensation  {travelExpense: {id}, rateType: {id}, rateCategory: {id}, location, count}
6. For edits: GET child resource for `version`, then PUT the full child payload
```

### "Run salary / payroll" (Tier 2-3)

```
1. GET /company/divisions or POST /division                                     → divisionId
2. GET /employee?email=...&fields=id,firstName,lastName,dateOfBirth            → employeeId
3. Ensure employee has `dateOfBirth`
4. POST /employee/employment  {employee: {id}, startDate, division: {id}, isMainEmployer: true}
5. GET /salary/type?count=50&fields=id,number,name                             → find salary type IDs
   (Fastlønn = number 2000, Bonus = number 2002, etc.)
6. POST /salary/transaction?generateTaxDeduction=true
   {date, month, year, payslips: [{
     employee: {id}, date, month, year,
     specifications: [
       {salaryType: {id: fastlonnId}, rate: baseSalary, count: 1, amount: baseSalary},
       {salaryType: {id: bonusId}, rate: bonusAmount, count: 1, amount: bonusAmount}
     ]
   }]}
```

### "Create custom accounting dimension and use it on a voucher" (Tier 2-3)

```
1. POST /ledger/accountingDimensionName  {dimensionName, description, active}   → dimensionIndex
2. POST /ledger/accountingDimensionValue  {dimensionIndex, displayName, number, showInVoucherRegistration, active}  → valueId
3. POST /ledger/voucher?sendToLedger=true  {postings: [..., {freeAccountingDimension1: {id: valueId}}]}
4. GET /ledger/voucher/{id}?fields=postings(freeAccountingDimension1(...))      → verify linkage
```

### "Create or update contact details for a customer" (Tier 1-2)

```
1. POST /customer or GET /customer                                               → customerId
2. POST /contact  {firstName, lastName, customer: {id}, email?}                 → contactId
3. GET /contact/{id} or reuse create response                                    → version
4. PUT /contact/{id}  {id, version, firstName, lastName, customer: {id}, ...}
```

### "Update postal address on customer / supplier / employee" (Tier 1-2)

```
1. GET parent entity with `fields=id,version,...Address(...)`
2. PUT parent entity with `id`, `version`, and nested `postalAddress` / `physicalAddress` / `address`
3. Do not rely on nested address ids surviving; Tripletex may create a new address row
```

### "Create bank reconciliation" (Tier 2-3)

```
1. GET /ledger/account?number=1920                                               → bank account id
2. GET /ledger/accountingPeriod?count=...                                        → accountingPeriodId
3. POST /bank/reconciliation  {account: {id}, accountingPeriod: {id}, type: "MANUAL", bankAccountClosingBalanceCurrency}
4. PUT /bank/reconciliation/{id} with `id`, `version`, and updated balance
```

### "Upload voucher or travel attachment" (Tier 1-2)

```
1. Create or find voucher / travel expense id
2. POST /ledger/voucher/{id}/attachment or /travelExpense/{id}/attachment
   multipart/form-data with field name `file`
```

### "Create webhook subscription" (Tier 1-2)

```
1. GET /event                                                                     → choose event key
2. POST /event/subscription  {event, targetUrl, fields, authHeaderName?, authHeaderValue?}
3. GET /event/subscription/{id} or reuse create response                         → version
4. PUT /event/subscription/{id} to adjust callback details
```

### "Register timesheet hours" (Tier 1-2)

```
1. GET /project?count=10                              → find project
2. GET /activity/%3EforTimeSheet?projectId={id}&employeeId={id}&date=...  → get valid activity
3. POST /timesheet/entry  {employee: {id}, project: {id}, activity: {id}, date, hours}
```

### "Cancel/reverse payment" (Tier 2-3)

```
1. GET /customer?organizationNumber=...                 → find customerId
2. GET /invoice?customerId={id}&invoiceDateFrom=2020-01-01&invoiceDateTo=2030-12-31  → find invoiceId
3. PUT /invoice/{id}/:createCreditNote?date=...&comment=Payment returned by bank
```

### "Delete / reverse" (Tier 1-2)

```
# Delete travel expense:
GET /travelExpense?count=10  → find id
DELETE /travelExpense/{id}

# Reverse voucher:
PUT /ledger/voucher/{id}/:reverse?date=2026-03-20

# Create credit note:
PUT /invoice/{id}/:createCreditNote?date=2026-03-31&comment=...
```

### "Modify existing entity" (Tier 1)

```
1. GET /customer?customerName=...  (or /supplier, /employee, etc.)   → get id + version
2. PUT /customer/{id}  {id, version, ...changed fields...}
```

### Auth pattern for the competition proxy

In the competition, use the credentials from the request body:

```python
base_url = body["tripletex_credentials"]["base_url"]
token = body["tripletex_credentials"]["session_token"]
auth = ("0", token)  # requests library handles Basic auth encoding

response = requests.get(f"{base_url}/employee", auth=auth, params={"fields": "id,firstName,lastName"})
```

The `session_token` from the competition is used directly as the Basic auth password. The `requests` library base64-encodes `0:{token}` into the Authorization header automatically. Always use the provided `base_url`, never the direct Tripletex URL.
