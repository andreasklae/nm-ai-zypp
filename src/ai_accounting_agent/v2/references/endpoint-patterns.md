# Common Endpoint Choice Patterns

This reference document maps common user intents to specific endpoint families and multi-step flows in the Tripletex API.

## Unpaid Invoices & Open Items
**Intent:** "Show me unpaid items for this customer" or "Find unpaid invoices for ACME."
**Pattern:**
1. Resolve the customer name to an internal ID first: `GET /customer`
2. Fetch the open posts (ledger truth) using `GET /ledger/posting/openPost` (with the `customerId`). Do NOT jump straight to `/invoice`.

## Creating Operational Documents
**Intent:** "Create an invoice for Nord AS" or "Book a purchase from Supplier Inc."
**Pattern:**
1. Do I already have the object ID? If not, `GET /customer` or `GET /supplier`.
2. Do I need to create the customer/supplier first? If `GET` fails, `POST /customer` or `POST /supplier`.
3. Create the document using the resolved ID (e.g., `POST /order` then `POST /order/orderline` then `PUT /order/{id}/:invoice`).

## Registering Payments
**Intent:** "Customer paid their invoice" or "Register invoice payment."
**Pattern:**
1. Use the most specific endpoint available: `PUT /invoice/{id}/:payment` zeros out the invoice `amountOutstanding`.
2. Do NOT use manual vouchers (`POST /ledger/voucher`) for the primary invoice payment.

## FX Agio / Disagio (Exchange Rate Differences)
**Intent:** "The customer paid the invoice, but the exchange rate changed from 11.65 NOK to 12.35 NOK. Register the payment and difference."
**Pattern:**
1. Do NOT try to search `GET /ledger/posting/openPost` to check if it's unpaid - just proceed to register the payment on the invoice you were told about.
2. Fetch the invoice first using `GET /invoice` (with `invoiceDateFrom` and `invoiceDateTo`).
3. Pay the invoice in full via `PUT /invoice/{id}/:payment`.
4. Calculate the FX difference by multiplying the FULL nominal invoice `amount` (INCLUDING VAT) by the difference in exchange rates. (Never use the ex. VAT amount).
5. Book the difference as a separate manual voucher using `POST /ledger/voucher`:
   - Debit/Credit `8060` (Valutagevinst) or `8160` (Valutatap).
   - Offset against `1920` (Bankinnskudd).

## Supplier Invoices
**Intent:** "Register a supplier invoice."
**Pattern:**
1. Resolve the supplier ID via `GET /supplier`.
2. There is no `POST /supplierInvoice`. You must book supplier invoices as manual vouchers via `POST /ledger/voucher`.
3. The voucher should debit the relevant expense account (e.g. 6590) and credit the accounts payable account (2400) using the `supplier_id` on the posting row.

## Creating Timesheets
**Intent:** "Register 20 hours for Geir Berge on project Fossekraft."
**Pattern:**
1. Resolve the project ID (`GET /project`).
2. Resolve the employee ID (`GET /employee`).
3. Find the valid activity ID for the project (`GET /activity/>forTimeSheet`).
4. `POST /timesheet/entry` using the resolved IDs.

## Fixed Price Project Billing
**Intent:** "Invoice a fixed price amount for project X."
**Pattern:**
1. Do NOT set the `budget` field on the project when attempting to set a fixed price billing amount. The `budget` field implies internal/expected costs, not the invoiced contract value to the customer.
2. Instead, use `PUT /project/{id}` with `isFixedPrice=true` and `fixedprice=total_contract_value`.
3. Create an order via `POST /order` linked to the project, add order lines, and then invoice it via `PUT /order/{id}/:invoice`.
