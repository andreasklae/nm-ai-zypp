from __future__ import annotations

from ai_accounting_agent.tests.live_api_support import ScenarioSpec


READ_ONLY_FORBIDDEN_TOOLS = [
    "create_employee",
    "grant_employee_privileges",
    "create_customer",
    "create_supplier",
    "create_product",
    "create_project",
    "create_voucher",
    "create_order",
    "create_invoice",
    "register_invoice_payment",
    "create_credit_note",
    "reverse_voucher",
    "create_travel_expense",
    "add_travel_expense_cost",
    "transition_travel_expense",
    "create_timesheet_entry",
]


SCENARIOS: list[ScenarioSpec] = [
    ScenarioSpec(
        scenario_id="read_only_proxy_verification",
        category="read_only",
        prompt_template=(
            "Bekreft at du kan bruke Tripletex-proxyen. "
            "Bruk bare lesetilgang hvis det er nyttig, og ikke opprett eller endre noe. "
            "trace_token={token}"
        ),
        expected_behavior=[
            "The model should confirm access without creating or changing any Tripletex entities.",
            "It may use a minimal read-only lookup, but should avoid exploratory write behavior.",
        ],
        success_criteria=[
            "HTTP response is completed.",
            "No Tripletex write calls are logged.",
            "No create or update workflow tools are used.",
        ],
        score_focus=[
            "Correctly interprets a no-op task.",
            "Minimizes unnecessary Tripletex calls.",
            "Avoids avoidable 4xx responses.",
        ],
        forbidden_tools=READ_ONLY_FORBIDDEN_TOOLS,
        expect_tripletex_write=False,
    ),
    ScenarioSpec(
        scenario_id="attachment_understanding_no_write",
        category="file_understanding",
        prompt_template=(
            "Les markdown-vedlegget, oppsummer kort hva dokumentet handler om, "
            "og ikke opprett eller endre noe i Tripletex. trace_token={token}"
        ),
        expected_behavior=[
            "The model should inspect the attachment and summarize it.",
            "The model should not create, update, or delete anything in Tripletex.",
        ],
        success_criteria=[
            "HTTP response is completed.",
            "Attachment metadata appears in logs.",
            "No write tools or Tripletex write requests appear.",
        ],
        score_focus=[
            "Grounds behavior in the attachment.",
            "Avoids unnecessary write attempts.",
            "Keeps API usage minimal for a read-only task.",
        ],
        attachment_filenames=["supplier_invoice_pdf.md"],
        forbidden_tools=READ_ONLY_FORBIDDEN_TOOLS,
        expect_tripletex_write=False,
    ),
    ScenarioSpec(
        scenario_id="employee_admin_creation",
        category="employee",
        prompt_template=(
            "Opprett en ansatt med fornavn Eval og etternavn Bruker {token}. "
            "E-post skal være eval-employee-{token}@example.org. "
            "Personen skal være kontoadministrator. Gjør bare det som er nødvendig. trace_token={token}"
        ),
        expected_behavior=[
            "The model should create an employee and grant the requested admin-level privileges.",
            "The model should avoid irrelevant extra setup outside the user request.",
        ],
        success_criteria=[
            "Employee is created with the requested email address.",
            "Privilege-granting behavior is visible in the tool trace.",
            "No avoidable 4xx responses occur.",
        ],
        score_focus=[
            "Correct entity creation.",
            "Correct privilege assignment.",
            "No unnecessary detours or retries.",
        ],
        expected_tools=["create_employee", "grant_employee_privileges"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="customer_creation_en",
        category="customer",
        prompt_template=(
            "Create a customer named Eval Customer {token} with email eval-customer-{token}@example.org. "
            "Do only what is necessary. trace_token={token}"
        ),
        expected_behavior=[
            "The model should create exactly one customer matching the requested name and email.",
            "The model should not create unrelated entities.",
        ],
        success_criteria=[
            "Customer creation appears in the tool trace.",
            "The customer can be found afterward by the unique name or email.",
        ],
        score_focus=[
            "Multilingual prompt handling.",
            "Direct customer creation correctness.",
            "Minimal unnecessary calls.",
        ],
        expected_tools=["create_customer"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="supplier_voucher_booking",
        category="supplier_voucher",
        prompt_template=(
            "Opprett en leverandør med navn Eval Supplier {token} og bokfør en leverandørfaktura basert på vedlegget. "
            "Bruk {token} i beskrivelsen på føringen eller voucheret. "
            "Ikke gjør unødvendige oppslag. trace_token={token}"
        ),
        expected_behavior=[
            "The model should use the attachment to ground a supplier + voucher workflow.",
            "The model should create the supplier if needed and then book a balanced voucher.",
        ],
        success_criteria=[
            "Supplier creation and voucher creation appear in the logs.",
            "No 4xx Tripletex responses are logged.",
            "The unique token is visible in the write path or created data.",
        ],
        score_focus=[
            "Attachment-grounded bookkeeping.",
            "Balanced voucher creation.",
            "Low-error execution.",
        ],
        attachment_filenames=["supplier_invoice_pdf.md"],
        expected_tools=["create_supplier", "create_voucher"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="product_creation_de",
        category="product",
        prompt_template=(
            "Erstelle ein Produkt namens Eval Produkt {token} mit einem Preis ohne MwSt. von 1250 NOK. "
            "Führe nur die notwendigen Schritte aus. trace_token={token}"
        ),
        expected_behavior=[
            "The model should create one product with the requested name and price.",
            "The model should not create unrelated customers or projects.",
        ],
        success_criteria=[
            "Product creation is visible in the tool trace.",
            "The created product can be found by the unique name.",
        ],
        score_focus=[
            "Multilingual prompt handling.",
            "Correct product creation.",
            "Minimal API usage.",
        ],
        expected_tools=["create_product"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="project_creation_es",
        category="project",
        prompt_template=(
            "Crea un proyecto llamado Eval Proyecto {token} que empiece hoy. "
            "No hagas nada adicional. trace_token={token}"
        ),
        expected_behavior=[
            "The model should create a single project with the requested name.",
            "The model should keep the workflow focused on the project task.",
        ],
        success_criteria=[
            "Project creation appears in the tool trace.",
            "The created project can be found afterward by the unique name.",
        ],
        score_focus=[
            "Multilingual prompt handling.",
            "Correct project creation.",
            "No unnecessary calls.",
        ],
        expected_tools=["create_project"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="fixed_price_project_billing_pt",
        category="project_billing",
        prompt_template=(
            "Crie um cliente Eval Billing Customer {token}, crie um projeto chamado Segurança de dados {token}, "
            "defina um preço fixo de 122800 NOK nesse projeto e fature 75 % do preço fixo como pagamento por etapa. "
            "Não registre pagamento. trace_token={token}"
        ),
        expected_behavior=[
            "The model should complete a fixed-price project billing flow without probing unsafe collection endpoints.",
            "The project should be configured for fixed-price billing before the milestone invoice is created.",
        ],
        success_criteria=[
            "Customer creation, project creation, project billing configuration, order creation, and invoice creation appear in the logs.",
            "No avoidable 4xx responses are logged.",
            "The Portuguese prompt is handled correctly end to end.",
        ],
        score_focus=[
            "Correct fixed-price project configuration.",
            "Safe milestone billing flow without generic schema-probing detours.",
            "Low-error execution.",
        ],
        expected_tools=["create_customer", "create_project", "configure_project_billing", "create_order", "create_invoice"],
        forbidden_tools=["register_invoice_payment"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="order_invoice_flow",
        category="order_invoice",
        prompt_template=(
            "Opprett en kunde Eval Invoice Customer {token}, et produkt Eval Invoice Product {token}, "
            "deretter en ordre og en faktura. Ikke registrer betaling. trace_token={token}"
        ),
        expected_behavior=[
            "The model should construct the prerequisite entities in the right order.",
            "The model should stop after invoice creation and not register payment.",
        ],
        success_criteria=[
            "Customer, product, order, and invoice tools appear in the log trace.",
            "No payment-registration tool is used.",
            "No avoidable 4xx responses are logged.",
        ],
        score_focus=[
            "Prerequisite planning.",
            "Correct order-to-invoice flow.",
            "Avoiding extra writes.",
        ],
        expected_tools=["create_customer", "create_product", "create_order", "create_invoice"],
        forbidden_tools=["register_invoice_payment"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="invoice_payment_flow",
        category="invoice_payment",
        prompt_template=(
            "Opprett kunde Eval Payment Customer {token}, produkt Eval Payment Product {token}, "
            "ordre og faktura, og registrer deretter betaling på fakturaen. trace_token={token}"
        ),
        expected_behavior=[
            "The model should complete the full order, invoice, and payment flow.",
            "The model should not stop early after invoice creation.",
        ],
        success_criteria=[
            "Invoice payment registration appears in the tool trace.",
            "No avoidable 4xx responses are logged.",
            "The workflow remains coherent from prerequisites to payment.",
        ],
        score_focus=[
            "Multi-step financial workflow correctness.",
            "Correct prerequisite planning.",
            "Low-error execution.",
        ],
        expected_tools=[
            "create_customer",
            "create_product",
            "create_order",
            "create_invoice",
            "register_invoice_payment",
        ],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="travel_expense_from_receipt",
        category="travel_expense",
        prompt_template=(
            "Lag en reiseutgift med tittel Eval Travel {token} basert på vedlegget. "
            "Bruk bare det som er nødvendig for å registrere reisen. trace_token={token}"
        ),
        expected_behavior=[
            "The model should inspect the receipt-like attachment and create a travel expense.",
            "The model should avoid unrelated customer or invoice actions.",
        ],
        success_criteria=[
            "Travel expense creation appears in logs.",
            "The travel title with the unique token can be found afterward.",
            "No 4xx Tripletex responses occur.",
        ],
        score_focus=[
            "Attachment-grounded workflow execution.",
            "Correct travel expense structure.",
            "Avoiding unnecessary API calls.",
        ],
        attachment_filenames=["expense_receipt_image.md"],
        expected_tools=["create_travel_expense"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="timesheet_entry_flow",
        category="timesheet",
        prompt_template=(
            "Opprett et prosjekt Eval Timesheet Project {token}, finn en gyldig aktivitet, "
            "og før 2,5 timer i dag med kommentaren Eval timesheet {token}. trace_token={token}"
        ),
        expected_behavior=[
            "The model should create the project, discover the needed activity, and create one timesheet entry.",
            "The model should not invent unsupported activity identifiers.",
        ],
        success_criteria=[
            "Project creation, reference lookup, and timesheet entry creation all appear in the logs.",
            "No avoidable 4xx responses are logged.",
        ],
        score_focus=[
            "Dependency-aware workflow planning.",
            "Correct use of reference lookup before write operations.",
            "Low-error execution.",
        ],
        expected_tools=["create_project", "get_timesheet_activities", "create_timesheet_entry"],
        expect_tripletex_write=True,
    ),
    ScenarioSpec(
        scenario_id="voucher_reversal_flow",
        category="corrective_workflow",
        prompt_template=(
            "Opprett en leverandør Eval Reverse Supplier {token}, bokfør en enkel balansert voucher "
            "med beskrivelsen Eval Reverse Voucher {token}, og reverser deretter voucheret med dagens dato. "
            "trace_token={token}"
        ),
        expected_behavior=[
            "The model should create the necessary accounting object and then reverse it.",
            "The corrective action should happen only after a valid original voucher exists.",
        ],
        success_criteria=[
            "Voucher creation and reversal appear in the tool trace.",
            "No avoidable 4xx responses are logged.",
        ],
        score_focus=[
            "Corrective workflow correctness.",
            "Ordering of dependent write steps.",
            "No unnecessary exploratory calls.",
        ],
        expected_tools=["create_supplier", "create_voucher", "reverse_voucher"],
        expect_tripletex_write=True,
    ),
]
