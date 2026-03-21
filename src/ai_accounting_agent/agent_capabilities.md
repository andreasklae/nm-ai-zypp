# Agent Capabilities Analysis

Analysis of 100 real API queries from GCP Cloud Logging (`2026-03-20`), abstracted into the generic capabilities the AI accounting agent needs.

## 1. Observed Task Types

| # | Task Type | Count | Languages Seen |
|---|-----------|-------|----------------|
| 1 | Create Employee | 8 | NO, FR, Nynorsk |
| 2 | Run Salary / Payroll | 8 | FR, PT, NO |
| 3 | Create Project | 5 | FR, ES, EN |
| 4 | Create & Send Invoice | 7 | ES, PT, NO, Nynorsk |
| 5 | Travel Expense (full flow) | 10 | PT, NO, DE, ES |
| 6 | Order → Invoice → Payment | 10 | FR, ES, EN |
| 7 | Register Supplier | 5 | NO, PT, FR |
| 8 | Create Departments | 5 | PT, NO, DE |
| 9 | Create Credit Note | 7 | NO, ES, FR, Nynorsk |
| 10 | Custom Accounting Dimension + Voucher | 4 | PT, NO, EN, Nynorsk |
| 11 | Create Customer | 6 | NO, ES |
| 12 | Register Supplier Invoice (as voucher) | 10 | DE, NO, Nynorsk, ES |
| 13 | Timesheet + Project Invoice | 5 | NO, FR, PT, ES, Nynorsk |
| 14 | Invoice with Multiple VAT Rates | 9 | NO, PT, DE, ES, Nynorsk |
| 15 | Register Invoice Payment | 4 | FR, EN, Nynorsk, ES |
| 16 | Reverse Payment (bank return) | 4 | ES, EN, DE, FR |
| 17 | Create Product | 4 | FR, ES, DE |
| 18 | Configure Project Billing (fixed price + partial invoice) | 1 | ES |

**Key observations:**
- Tasks arrive in 6+ languages: Norwegian (Bokmål + Nynorsk), French, Spanish, Portuguese, German, English
- The top 3 task types by volume are: travel expense, supplier invoice, and order-to-cash — all multi-step flows
- Every task type maps to 2–8 distinct API calls in sequence

---

## 2. Task Type Details

### 2.1 Create Employee

**Description:** Create a new employee with name, date of birth, email, and start date.

**Example prompts:**
- "Vi har en ny ansatt som heter Ingrid Johansen, født 9. November 1995. Opprett vedkommende som ansatt med e-post ingrid.johansen@example.org og startdato 13. January 2026."
- "Nous avons un nouvel employé nommé Arthur Martin, né le 20. May 1991."

**Required capabilities:**
1. Get today's date (for date fields)
2. Search department (find default department)
3. Create employee (firstName, lastName, email, userType, department)
4. Update employee (set dateOfBirth — required for later salary operations)
5. Create employment (set startDate, link to division)
6. Grant employee privileges (optionally set admin role)

**Tool chain:** `get_today_date` → `get_reference_data(departments)` → `create_employee` → `update_employee` → `create_employment` → `grant_employee_privileges`

**Tripletex endpoints:**
- `GET /department?count=1` — find default department
- `POST /employee` — create with firstName, lastName, email, userType, department.id
- `PUT /employee/{id}` — set dateOfBirth (required on update)
- `POST /employee/employment` — set startDate, division
- `PUT /employee/entitlement/:grantEntitlementsByTemplate` — set privileges

**Pitfalls:**
- `dateOfBirth` is not required on creation but becomes required on any PUT
- `userType` must be `STANDARD`, `EXTENDED`, or `NO_ACCESS` — not empty
- `department.id` is required — every sandbox has a default "Avdeling"
- `startDate` is on the employment record, NOT on the employee entity

---

### 2.2 Run Salary / Payroll

**Description:** Process monthly salary for an employee, typically base pay + optional one-time bonus.

**Example prompts:**
- "Kjør lønn for Kari Nordmann (kari.nordmann@example.org). Grunnlønn er 45000 NOK."
- "Exécutez la paie de Sarah Moreau (sarah.moreau@example.org) pour ce mois. Le salaire de base est de 56900 NOK. Ajoutez une prime unique de 15800 NOK."

**Required capabilities:**
1. Get today's date
2. Search employee (by email)
3. Get reference data (salary types — find "Fastlønn" and "Bonus" type IDs)
4. Ensure division exists (create if needed)
5. Ensure employment exists (create if needed, link to division)
6. Run salary transaction (with payslip specifications)

**Tool chain:** `get_today_date` → `search_employee` → `get_reference_data(salary_types)` → `create_division` (if needed) → `create_employment` (if needed) → `run_salary_transaction`

**Tripletex endpoints:**
- `GET /employee?email=...` — find employee
- `GET /salary/type?count=50&fields=id,number,name` — find salary type IDs
- `POST /division` — create division (needs organizationNumber, municipality, municipalityDate)
- `POST /employee/employment` — create employment (needs employee.id, startDate, division.id)
- `POST /salary/transaction?generateTaxDeduction=true` — run payroll

**Pitfalls:**
- Employee must have `dateOfBirth` set before salary transaction
- Employment must be linked to a division
- Salary type IDs vary per sandbox — always look them up
- For bonus: use salary type number 2002 ("Bonus"), same rate×count pattern
- `generateTaxDeduction=true` as query param to auto-calculate tax

---

### 2.3 Create Project

**Description:** Create a project linked to a customer, with a designated project manager (employee).

**Example prompts:**
- "Créez le projet 'Implémentation Montagne' lié au client Montagne SARL (nº org. 842138248). Le chef de projet est Jules Martin (jules.martin@example.org)."
- "Create the project 'Upgrade Silveroak' linked to the customer Silveroak Ltd (org no. 937657250)."

**Required capabilities:**
1. Get today's date
2. Search or create customer (by org number)
3. Search or create employee (project manager, by email)
4. Create project (name, projectManager, startDate, customer)

**Tool chain:** `get_today_date` → `search/create_customer` → `search/create_employee` → `create_project`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...` or `POST /customer`
- `GET /employee?email=...` or `POST /employee`
- `POST /project` — needs name, projectManager.id, startDate; optionally customer.id

**Pitfalls:**
- `projectManager` is required and must be an employee ID
- `startDate` is required
- Customer linkage can be set at creation or via later PUT

---

### 2.4 Create & Send Invoice

**Description:** Create a simple invoice to a customer for a service/product at a given amount, and send it.

**Example prompts:**
- "Opprett og send en faktura til kunden Brattli AS (org.nr 845762686) på 26450 kr eksklusiv MVA. Fakturaen gjelder Skylagring."
- "Crea y envía una factura al cliente Río Verde SL (org. nº 954834492) por 26450 NOK sin IVA."

**Required capabilities:**
1. Get today's date
2. Search or create customer
3. Create product (optional — can use freeform order line)
4. Create order (with order lines)
5. Ensure bank account has number (for invoice creation)
6. Create invoice (from order)
7. Send invoice (optional)

**Tool chain:** `get_today_date` → `search/create_customer` → `create_product` (optional) → `create_order` → `create_invoice` → `send_invoice`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...` or `POST /customer`
- `POST /product` (optional)
- `POST /order` — needs customer.id, orderDate, deliveryDate, orderLines
- `POST /invoice?sendToCustomer=false` — needs invoiceDate, invoiceDueDate, orders
- `PUT /invoice/{id}/:send?sendType=EMAIL` (optional)

**Pitfalls:**
- `deliveryDate` is REQUIRED on orders — missing it gives 422
- Bank account (1920) must have `bankAccountNumber` set before invoice creation
- Order lines can reference a product or be freeform (description + unitPriceExcludingVatCurrency)

---

### 2.5 Travel Expense (full flow)

**Description:** Register a travel expense for an employee, including per-diem allowances and individual cost items (flights, taxi, etc.), then deliver for approval.

**Example prompts:**
- "Registrer en reiseregning for en 3-dagers tur til Tromsø. Formål: Kundebesøk. Diett med dagssats 800 NOK. Utgifter: flybillett 5000 NOK og taxi 300 NOK."
- "Erfassen Sie eine Reisekostenabrechnung für Johanna Hoffmann (johanna.hoffmann@example.org) für 'Kundenbesuch Tromsø'. Die Reise dauerte 3 Tage."

**Required capabilities:**
1. Get today's date
2. Search or create employee
3. Get reference data (cost categories, payment types, per-diem rates)
4. Create travel expense (with nested travelDetails)
5. Add per-diem compensation (multi-day domestic or day trip)
6. Add travel expense costs (one per cost item — flights, taxi, hotel)
7. Transition travel expense (deliver → approve → createVouchers)

**Tool chain:** `get_today_date` → `search/create_employee` → `get_reference_data(cost_categories, payment_types, per_diem_rates)` → `create_travel_expense` → `add_travel_per_diem` → `add_travel_expense_cost` (×N) → `transition_travel_expense(deliver)` → `transition_travel_expense(approve)` → `transition_travel_expense(createVouchers)`

**Tripletex endpoints:**
- `GET /employee?email=...`
- `GET /travelExpense/costCategory?count=25`
- `GET /travelExpense/paymentType?count=10`
- `GET /travelExpense/rate?type=PER_DIEM&dateFrom=...&dateTo=...`
- `POST /travelExpense` — with nested `travelDetails`
- `POST /travelExpense/perDiemCompensation`
- `POST /travelExpense/cost` (one per cost line)
- `PUT /travelExpense/:deliver?id=...`
- `PUT /travelExpense/:approve?id=...`
- `PUT /travelExpense/:createVouchers?id=...`

**Pitfalls:**
- `travelDetails` MUST be nested inside the travel expense body, not at root level (422 / 16000 if at root)
- Travel expense writes MUST be sequential — never parallel
- `paymentType` is mandatory on cost lines (typically "Privat utlegg" for employee-paid expenses)
- Per-diem rate category must match trip type: day trip vs. overnight (different rate categories)
- In non-VAT companies, costs with VAT-carrying categories fail on delivery

---

### 2.6 Order → Invoice → Payment (full flow)

**Description:** Create an order with multiple products, convert it to an invoice, and register full payment.

**Example prompts:**
- "Créez une commande pour le client Forêt SARL (nº org. 962176127) avec les produits Maintenance (2417) à 32250 NOK et Développement système (7053) à 16900 NOK. Convertissez la commande en facture et enregistrez le paiement intégral."
- "Create an order for the customer Ridgepoint Ltd (org no. 925103489) with the products Consulting Hours (5359) at 21150 NOK and Analysis Report (1028) at 1600 NOK."

**Required capabilities:**
1. Get today's date
2. Search or create customer
3. Search or create products (by product number)
4. Create order (with product-referenced order lines)
5. Ensure bank account setup
6. Create invoice (from order)
7. Get payment types
8. Register invoice payment (full amount)

**Tool chain:** `get_today_date` → `search/create_customer` → `search/create_products` → `create_order` → `create_invoice` → `get_reference_data(payment_types)` → `register_invoice_payment`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...` or `POST /customer`
- `GET /product?number=...` or `POST /product`
- `POST /order`
- `POST /invoice?sendToCustomer=false`
- `GET /invoice/paymentType`
- `PUT /invoice/{id}/:payment?paymentDate=...&paymentTypeId=...&paidAmount=...`

**Pitfalls:**
- Payment type IDs vary per sandbox — always look them up via `GET /invoice/paymentType`
- `paidAmount` must match the invoice total (including VAT if applicable)
- All payment params are query parameters, not body

---

### 2.7 Register Supplier

**Description:** Register a new supplier with name, organization number, and email.

**Example prompts:**
- "Registrer leverandøren Dalheim AS med organisasjonsnummer 892196753. E-post: faktura@dalheim.no."
- "Enregistrez le fournisseur Cascade SARL avec le numéro d'organisation 997712560."

**Required capabilities:**
1. Get today's date
2. Create supplier

**Tool chain:** `get_today_date` → `create_supplier`

**Tripletex endpoints:**
- `POST /supplier` — with name, organizationNumber, email (or invoiceEmail)

**Pitfalls:**
- Organization number must be exactly 9 digits, no spaces or punctuation
- Supplier has both `email` (general) and `invoiceEmail` (invoice-specific) fields
- No `name` filter on GET — use `organizationNumber` or `supplierNumber` to search

---

### 2.8 Create Departments

**Description:** Create one or more departments by name.

**Example prompts:**
- "Opprett en avdeling som heter Logistikk."
- "Crie três departamentos no Tripletex: 'IT', 'Kvalitetskontroll' e 'Regnskap'."

**Required capabilities:**
1. Get today's date
2. Create department (one per department name)

**Tool chain:** `get_today_date` → `create_department` (×N)

**Tripletex endpoints:**
- `POST /department` — minimal: just `name`

**Pitfalls:**
- Minimal creation: just `name` is enough
- `departmentNumber` auto-fills to empty string if not provided

---

### 2.9 Create Credit Note

**Description:** Issue a full credit note to reverse an invoice, typically after a customer complaint.

**Example prompts:**
- "Kunden Tindra AS (org.nr 911680521) har reklamert på fakturaen for 'Systemutvikling' (8050 kr ekskl. MVA). Opprett en fullstendig kreditnota som reverserer hele fakturaen."
- "El cliente Viento SL (org. nº 978503071) ha reclamado sobre la factura por 'Licencia de software' (25450 NOK sin IVA)."

**Required capabilities:**
1. Get today's date
2. Search customer (by org number)
3. Search or create original invoice (customer + order → invoice flow if not exists)
4. Create credit note (on the original invoice)

**Tool chain:** `get_today_date` → `search_customer` → `search/create_invoice` → `create_credit_note`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...`
- `GET /invoice?customerId=...&invoiceDateFrom=...&invoiceDateTo=...&fields=id,invoiceNumber,amount,amountOutstanding,isCreditNote`
- If no invoice found: `POST /order` → `POST /invoice` (create the original first)
- `PUT /invoice/{id}/:createCreditNote?date=...&comment=...`

**Pitfalls:**
- Must find the correct invoice — filter by `customerId` and `isCreditNote=false`
- If the invoice doesn't exist yet, the agent must create the full order→invoice flow first, then credit it
- Date and comment on credit note are query parameters, not body

---

### 2.10 Custom Accounting Dimension + Voucher

**Description:** Create a custom accounting dimension with named values, then post a journal voucher linked to one of those values.

**Example prompts:**
- "Opprett ein fri rekneskapsdimensjon 'Marked' med verdiane 'Offentlig' og 'Privat'. Bokfør deretter eit bilag på konto 6340 for 25200 kr, knytt til dimensjonsverdien 'Offentlig'."
- "Create a custom accounting dimension 'Produktlinje' with the values 'Standard' and 'Basis'."

**Required capabilities:**
1. Get today's date
2. Create accounting dimension (name + description)
3. Create dimension values (one per value, linked by dimensionIndex)
4. Get account info (find the specified account by number)
5. Create voucher (with posting linked to dimension value via freeAccountingDimension1/2/3)

**Tool chain:** `get_today_date` → `create_accounting_dimension` → `create_dimension_values` → `get_reference_data(accounts)` → `create_voucher`

**Tripletex endpoints:**
- `POST /ledger/accountingDimensionName` — returns `dimensionIndex`
- `POST /ledger/accountingDimensionValue` — with `dimensionIndex`, `displayName`
- `GET /ledger/account?number=...`
- `POST /ledger/voucher?sendToLedger=true` — with `freeAccountingDimension1: {id: ...}` on posting

**Pitfalls:**
- `dimensionName` max length is 20 characters
- Link dimension value via `freeAccountingDimension1`, `freeAccountingDimension2`, or `freeAccountingDimension3`
- Voucher postings must balance (sum of amountGross = 0)
- Set `row: 1, 2, ...` on each posting (row 0 is reserved)

---

### 2.11 Create Customer

**Description:** Create a customer with name, organization number, address, and email.

**Example prompts:**
- "Opprett kunden Fjordkraft AS med organisasjonsnummer 843216285. Adressen er Fjordveien 129, 2317 Hamar. E-post: post@fjordkraft.no."
- "Crea el cliente Luna SL con número de organización 975692981. La dirección es Torggata 50, 9008 Tromsø."

**Required capabilities:**
1. Get today's date
2. Create customer (with name, orgNumber, email, address)

**Tool chain:** `get_today_date` → `create_customer`

**Tripletex endpoints:**
- `POST /customer` — with name, organizationNumber, email, postalAddress (addressLine1, postalCode, city)

**Pitfalls:**
- Organization number must be exactly 9 digits
- Address can be set at creation time via `postalAddress` nested object
- `invoiceSendMethod` defaults to reasonable value if not set

---

### 2.12 Register Supplier Invoice (as voucher)

**Description:** Record a received supplier invoice with correct VAT handling, posting to the appropriate expense account.

**Example prompts:**
- "Vi har mottatt faktura INV-2026-4085 fra leverandøren Snøhetta AS (org.nr 861790029) på 44450 kr inklusiv MVA. Beløpet gjelder kontortjenester (konto 6300). Registrer leverandørfakturaen med korrekt inngående MVA (25 %)."
- "Wir haben die Rechnung INV-2026-7156 vom Lieferanten Windkraft GmbH (Org.-Nr. 961246954) über 35350 NOK einschließlich MwSt. erhalten."

**Required capabilities:**
1. Get today's date
2. Search or create supplier (by org number)
3. Get reference data (accounts — find expense account and supplier payables account 2400)
4. Get reference data (VAT types — find inngående 25% = vatType.id 1)
5. Create voucher (expense debit + supplier credit, with vendorInvoiceNumber)

**Tool chain:** `get_today_date` → `search/create_supplier` → `get_reference_data(accounts)` → `get_reference_data(vat_types)` → `create_voucher`

**Tripletex endpoints:**
- `GET /supplier?organizationNumber=...` or `POST /supplier`
- `GET /ledger/account?number=...` — find expense account and 2400
- `GET /ledger/vatType?count=60&fields=id,name,number,percentage`
- `POST /ledger/voucher?sendToLedger=true`

**Pitfalls:**
- There is NO `POST /supplierInvoice` — must create as voucher
- Account 2400 (Leverandørgjeld) has `ledgerType=VENDOR` — posting MUST include `supplier.id`
- Amount is typically given INCLUDING VAT — must calculate net + VAT portions
- For 25% input VAT: use `vatType.id=1` (Fradrag inngående, høy sats)
- If VAT-locked accounts, include matching `vatType` on the posting

---

### 2.13 Timesheet + Project Invoice

**Description:** Register hours for an employee on a project activity, then generate a project invoice to the customer.

**Example prompts:**
- "Registrer 24 timer for Solveig Hansen (solveig.hansen@example.org) på aktiviteten 'Analyse' i prosjektet 'Apputvikling' for Tindra AS (org.nr 945097523). Timesats: 1850 kr/t. Generer en prosjektfaktura til kunden basert på de registrerte timene."

**Required capabilities:**
1. Get today's date
2. Search or create customer
3. Search or create employee
4. Create project (linked to customer, with employee as manager)
5. Configure project billing (set customer, billing type)
6. Get timesheet activities (for the project)
7. Create timesheet entry (hours for activity)
8. Create order (project-linked, with amount = hours × rate)
9. Create invoice (from order)

**Tool chain:** `get_today_date` → `search/create_customer` → `search/create_employee` → `create_project` → `configure_project_billing` → `get_timesheet_activities` → `create_timesheet_entry` → `create_order` → `create_invoice`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...` or `POST /customer`
- `GET /employee?email=...` or `POST /employee`
- `POST /project`
- `PUT /project/{id}` — set customer, billing config
- `GET /activity/%3EforTimeSheet?projectId=...&employeeId=...&date=...`
- `POST /timesheet/entry`
- `POST /order` — with project.id and order line for hours × rate
- `POST /invoice?sendToCustomer=false`

**Pitfalls:**
- `activity.id` MUST come from `GET /activity/>forTimeSheet` — using a non-existent ID gives 404
- Project must have customer linked before invoicing
- Timesheet entry needs employee.id, project.id, activity.id, date, hours

---

### 2.14 Invoice with Multiple VAT Rates

**Description:** Create an invoice with multiple product lines, each at a different VAT rate (25%, 15%, 0%).

**Example prompts:**
- "Opprett ein faktura til kunden Elvdal AS (org.nr 810713909) med tre produktlinjer: Nettverksteneste (7765) til 13150 kr med 25 % MVA, Konsulenttimar (4369) til 11800 kr med 15 % MVA (næringsmiddel), og Vedlikehald (5331) til 8700 kr med 0 % MVA (avgiftsfri)."

**Required capabilities:**
1. Get today's date
2. Search or create customer
3. Create products (each with correct vatType — 25%, 15%, 0%)
4. Create order (with product-referenced order lines)
5. Create invoice

**Tool chain:** `get_today_date` → `search/create_customer` → `create_product` (×3, with different vatType IDs) → `create_order` → `create_invoice`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...` or `POST /customer`
- `POST /product` — with `vatType.id` set per VAT rate (3=25%, 31=15%, 6=0%)
- `POST /order`
- `POST /invoice?sendToCustomer=false`

**Pitfalls:**
- In VAT-registered companies: vatType.id=3 (25%), 31 (15%), 6 or 5 (0%)
- In non-VAT companies: only vatType.id=6 is accepted for products
- 15% rate is labeled "næringsmiddel" (food) — used for special categories
- Product's vatType determines the VAT on the order line automatically

---

### 2.15 Register Invoice Payment

**Description:** Register full payment on an outstanding customer invoice.

**Example prompts:**
- "Le client Colline SARL (nº org. 850491941) a une facture impayée de 10550 NOK hors TVA pour 'Heures de conseil'. Enregistrez le paiement intégral de cette facture."
- "Kunden Strandvik AS (org.nr 827237108) har ein uteståande faktura på 30500 kr eksklusiv MVA for 'Skylagring'."

**Required capabilities:**
1. Get today's date
2. Search customer (by org number)
3. Search invoice (by customerId, find outstanding one)
4. Get payment types
5. If invoice not found: create full order → invoice flow first
6. Register invoice payment

**Tool chain:** `get_today_date` → `search_customer` → `search_invoice` → `get_reference_data(payment_types)` → (optionally create order → invoice) → `register_invoice_payment`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...`
- `GET /invoice?customerId=...&invoiceDateFrom=...&invoiceDateTo=...`
- `GET /invoice/paymentType`
- `PUT /invoice/{id}/:payment?paymentDate=...&paymentTypeId=...&paidAmount=...`

**Pitfalls:**
- Invoice search REQUIRES `invoiceDateFrom` + `invoiceDateTo` — without them you get 400
- `paidAmount` must include VAT if invoice has VAT
- Payment type IDs vary per sandbox
- All payment params are query parameters

---

### 2.16 Reverse Payment (bank return)

**Description:** A payment was returned by the bank. Reverse it so the invoice shows the outstanding amount again.

**Example prompts:**
- "The payment from Blueshore Ltd (org no. 989902121) for the invoice 'Software License' (39700 NOK excl. VAT) was returned by the bank. Reverse the payment so the invoice shows the outstanding amount again."
- "Die Zahlung von Brückentor GmbH (Org.-Nr. 944848479) für die Rechnung 'Wartung' (42200 NOK ohne MwSt.) wurde von der Bank zurückgebucht."

**Required capabilities:**
1. Get today's date
2. Search customer
3. Search invoice (find the paid one)
4. If invoice not found: create full order → invoice → payment flow first
5. Create credit note (reverses the invoice including its payment)

**Tool chain:** `get_today_date` → `search_customer` → `search_invoice` → (optionally create order → invoice → payment) → `create_credit_note`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...`
- `GET /invoice?customerId=...&invoiceDateFrom=...&invoiceDateTo=...`
- `PUT /invoice/{id}/:createCreditNote?date=...&comment=Payment returned by bank`

**Pitfalls:**
- Credit note is the mechanism for reversing a payment in Tripletex
- Must find the original invoice (not the credit note) — filter with `isCreditNote=false`
- If no invoice exists, create the full flow first (order → invoice → payment → credit note)

---

### 2.17 Create Product

**Description:** Create a product with name, number, price, and VAT rate.

**Example prompts:**
- "Créez le produit 'Stockage cloud' avec le numéro de produit 9433. Le prix est de 7550 NOK hors TVA, avec le taux standard de 25 %."
- "Créez le produit 'Journal quotidien' avec le numéro de produit 9219. Le prix est de 3150 NOK hors TVA, avec le taux de TVA de 0 % pour les journaux."

**Required capabilities:**
1. Get today's date
2. Create product

**Tool chain:** `get_today_date` → `create_product`

**Tripletex endpoints:**
- `POST /product` — with name, number, priceExcludingVatCurrency, vatType.id

**Pitfalls:**
- In non-VAT companies, only `vatType.id=6` is accepted
- Special 0% VAT for newspapers may use a different vatType than standard 0%
- Product `number` can be string (e.g. "KONS-001") or numeric

---

### 2.18 Configure Project Billing (fixed price + partial invoice)

**Description:** Set up a project with fixed-price billing and invoice a partial amount (milestone payment).

**Example prompts:**
- "Establezca un precio fijo de 457650 NOK en el proyecto 'Implementación ERP' para Solmar SL (org. nº 866378843). El director del proyecto es María Sánchez (maria.sanchez@example.org). Facture al cliente el 25 % del precio fijo como pago parcial."

**Required capabilities:**
1. Get today's date
2. Search or create customer
3. Search or create employee (project manager)
4. Create project
5. Configure project billing (set customer, fixed price, isFixedPrice)
6. Create order (with milestone/partial amount as freeform line, linked to project)
7. Create invoice

**Tool chain:** `get_today_date` → `search/create_customer` → `search/create_employee` → `create_project` → `configure_project_billing` → `create_order` → `create_invoice`

**Tripletex endpoints:**
- `GET /customer?organizationNumber=...` or `POST /customer`
- `GET /employee?email=...` or `POST /employee`
- `POST /project`
- `PUT /project/{id}` — set customer.id, isFixedPrice, fixedprice, projectManager.id
- `POST /order` — with project.id, freeform order line for partial amount
- `POST /invoice?sendToCustomer=false`

**Pitfalls:**
- PUT /project requires `version` for optimistic locking — GET first
- `invoicingPlan` can be set but milestone billing is safer via order lines
- Calculate partial amount correctly (e.g., 25% of 457650 = 114412.50)

---

## 3. Abstract Capability Inventory

These are the ~29 distinct capabilities the agent needs, derived from the 18 task types above:

| # | Capability | Purpose | Primary API Endpoint | Used By Tasks |
|---|---|---|---|---|
| 1 | get_today_date | Get current date for all date fields | System call | All |
| 2 | announce_step | Declare current phase to logging | System call | All |
| 3 | search_reference_docs | Search tripletex_api.md for guidance | Internal index | All |
| 4 | search_employee | Find employee by email or name | `GET /employee?email=...` | 2,3,5,6,13,15,16,18 |
| 5 | create_employee | Create new employee | `POST /employee` | 1,2,3,5,13,18 |
| 6 | update_employee | Update employee (dateOfBirth, etc.) | `PUT /employee/{id}` | 1,2 |
| 7 | grant_employee_privileges | Set admin/role privileges | `PUT /employee/entitlement/:grantEntitlementsByTemplate` | 1 |
| 8 | search_customer | Find customer by org number or name | `GET /customer?organizationNumber=...` | 3,4,6,9,11,13,14,15,16,18 |
| 9 | create_customer | Create customer with address | `POST /customer` | 3,4,6,9,11,13,14,15,16,18 |
| 10 | search_supplier | Find supplier by org number | `GET /supplier?organizationNumber=...` | 7,12 |
| 11 | create_supplier | Create supplier | `POST /supplier` | 7,12 |
| 12 | search_product | Find product by number | `GET /product?number=...` | 6,14,17 |
| 13 | create_product | Create product with price and VAT | `POST /product` | 4,6,14,17 |
| 14 | create_department | Create department by name | `POST /department` | 8 |
| 15 | create_project | Create project with manager | `POST /project` | 3,13,18 |
| 16 | configure_project_billing | Set fixed price, customer, billing | `PUT /project/{id}` | 13,18 |
| 17 | create_order | Create order with lines | `POST /order` | 4,6,9,13,14,15,16,18 |
| 18 | create_invoice | Create invoice from orders | `POST /invoice` | 4,6,9,13,14,15,16,18 |
| 19 | send_invoice | Send invoice to customer | `PUT /invoice/{id}/:send` | 4 |
| 20 | register_invoice_payment | Register payment on invoice | `PUT /invoice/{id}/:payment` | 6,15 |
| 21 | create_credit_note | Reverse an invoice / payment | `PUT /invoice/{id}/:createCreditNote` | 9,16 |
| 22 | create_voucher | Post journal entry | `POST /ledger/voucher?sendToLedger=true` | 10,12 |
| 23 | create_accounting_dimension | Create custom dimension + values | `POST /ledger/accountingDimensionName` + `POST /ledger/accountingDimensionValue` | 10 |
| 24 | create_travel_expense | Create travel expense with details | `POST /travelExpense` | 5 |
| 25 | add_travel_per_diem | Add per-diem compensation | `POST /travelExpense/perDiemCompensation` | 5 |
| 26 | add_travel_expense_cost | Add cost line (flight, taxi) | `POST /travelExpense/cost` | 5 |
| 27 | transition_travel_expense | Deliver/approve/create vouchers | `PUT /travelExpense/:action` | 5 |
| 28 | get_timesheet_activities | Look up project activities | `GET /activity/%3EforTimeSheet` | 13 |
| 29 | create_timesheet_entry | Log hours on activity | `POST /timesheet/entry` | 13 |
| 30 | run_salary_transaction | Run payroll with payslips | `POST /salary/transaction` | 2 |
| 31 | get_reference_data | Look up VAT types, accounts, payment types, salary types, cost categories, currencies, departments, etc. | Various `GET` endpoints | Most |
| 32 | tripletex_get | Generic GET for uncovered endpoints | `GET /{path}` | Fallback |
| 33 | tripletex_post | Generic POST for uncovered endpoints | `POST /{path}` | Fallback |
| 34 | tripletex_put | Generic PUT for uncovered endpoints | `PUT /{path}` | Fallback |

---

## 4. Frequency Distribution

Capabilities sorted by how many task types need them (higher = more critical):

| Rank | Capability | Task Types Using It |
|------|-----------|-------------------|
| 1 | get_today_date | 18/18 |
| 2 | create_order / create_invoice | 8/18 |
| 3 | search/create_customer | 11/18 |
| 4 | get_reference_data | 10/18 |
| 5 | search/create_employee | 7/18 |
| 6 | create_voucher | 2/18 (but high-value: supplier invoices + dimensions) |
| 7 | travel expense tools (create + cost + per-diem + transition) | 1/18 (but highest volume: 10 queries) |
| 8 | run_salary_transaction | 1/18 (but high volume: 8 queries) |
| 9 | create_credit_note | 2/18 (credit notes + payment reversals) |
| 10 | register_invoice_payment | 2/18 |

---

## 5. Language Distribution

Tasks arrive in multiple languages, requiring the agent to parse accounting terminology in each:

| Language | Approximate % | Key terms the agent must recognize |
|----------|--------------|-----------------------------------|
| Norwegian (Bokmål) | ~25% | faktura, leverandør, ansatt, reiseregning, bilag, avdeling, lønn |
| Norwegian (Nynorsk) | ~15% | faktura, leverandør, tilsett, reiserekning, bilag, avdeling, løn |
| French | ~20% | facture, fournisseur, employé, note de frais, commande, paie |
| Spanish | ~15% | factura, proveedor, empleado, gastos de viaje, pedido, nómina |
| Portuguese | ~15% | fatura, fornecedor, empregado, despesa de viagem, salário |
| German | ~5% | Rechnung, Lieferant, Mitarbeiter, Reisekosten, Gehalt |
| English | ~5% | invoice, supplier, employee, travel expense, salary |
