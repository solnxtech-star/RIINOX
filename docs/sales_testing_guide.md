# RIINOX Sales App — Interactive Manual Testing Guide & Payload Reference

> **Target Service**: RIINOX Sales API (`/api/organizations/{org_id}/sales/`)  
> **Relevant Specifications**: PRD §21 (Sales Order Management), §22 (Invoicing Linkage), §23 (Sales Returns & Refunds), §24 (Multi-Currency), §25 (Receipts), §26 (Payments), §30 (Discount Limits), §39 (Calculation Enforcement), §43 (FIFO Inventory Deductions), §44 (Cancellations & Immutability)  
> **Target Audience**: QA Engineers, Backend Developers, Frontend / Mobile Integrators  

---

## 1. Quick Answers to Crucial Sales Payload Questions

> [!IMPORTANT]
> ### 1. Should the frontend calculate or submit `subtotal`, `total`, `payment_status`, or `fulfillment_status`?
> **NO. The frontend should NOT calculate or submit these fields.**
> * **Fields the frontend should NOT send**:
>   * `subtotal` / `total`: Strictly calculated on the server per PRD §39.
>   * `payment_status`: Computed automatically from the tendered `payment.amount` vs the computed order total (`unpaid`, `partially_paid`, or `paid`).
>   * `fulfillment_status`: Automatically initialized to `unfulfilled` upon creation, updated by warehouse dispatch, and transitioned to `canceled` or `returned` via dedicated workflow actions.
> * **What the frontend sends**: Only `customer`, `location`, `items` (with `product`, `quantity`, and optional negotiated unit price/discount/tax), and optional `payment` tender.
> * **Backend Enforcement (PRD §39)**: In [`create_sale`](file:///c:/Users/solomonXuche/Documents/Programming/RIINOX/core/applications/sales/services/sales.py), the backend resolves unit prices against the product catalog or authorized price overrides, validates per-line or order-level discounts and taxes, and computes exact whole-cent totals using Hamilton largest-remainder apportionment.
> * **Business Reason**: In wholesale and retail commerce, client-side pricing calculation introduces rounding discrepancies, fraud vectors, and tax evasion risks.


> [!TIP]
> ### 2. How does inventory deduction and FIFO work on sale creation (PRD §43)?
> * **Automatic Deduction**: Calling `POST /api/organizations/{org_id}/sales/` immediately runs [`process_sale_inventory_and_ledger`](file:///c:/Users/solomonXuche/Documents/Programming/RIINOX/core/applications/inventory/services.py).
> * **FIFO Batch Ordering**: Stock is deducted from the warehouse associated with the specified `location`. Batches are consumed in First-In, First-Out (FIFO) order sorted by earliest expiry date.
> * **Immutable Ledger**: For every line item, an [`InventoryLedgerEntry`](file:///c:/Users/solomonXuche/Documents/Programming/RIINOX/core/applications/inventory/models.py) is created recording the quantity moved, unit purchase cost, and links to the system `Transaction`.
> * **Stock Guard**: If a product has `track_inventory=True` and insufficient stock is available, the transaction immediately rolls back with an out-of-stock validation error.

> [!NOTE]
> ### 3. Can historical sales orders be hard-deleted?
> **NO. Sales orders are immutable financial documents (PRD §44).**
> * **Immutability Enforcement**: Calling `DELETE /api/organizations/{org_id}/sales/{id}/` returns `HTTP 400 Bad Request`.
> * **Cancellation Workflow**: If an order needs to be revoked before fulfillment, call `POST /api/organizations/{org_id}/sales/{id}/cancel/`. This reverses deducted stock back to the warehouse, logs compensatory ledger entries, and transitions the order to `canceled`.
> * **Post-Fulfillment Corrections**: Once items have left the warehouse or partial deliveries have occurred, corrections must be handled through the Sales Return workflow (`POST /api/organizations/{org_id}/sales/returns/`).

---

## 2. Comprehensive Payload Field Dictionary

### 2.1 Sale Header Object (`POST /api/organizations/{org_id}/sales/`)

| Field Name | Type | Required? | Default | Source / Endpoint | Purpose & Business Rules |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`customer`** | `UUID` | **Required** | None | `GET /api/organizations/{org_id}/customers/` | B2B/B2C Customer. Locked pessimistically during creation to prevent credit race conditions. |
| **`warehouse`** | `UUID` | Optional | Org's active warehouse | `GET /api/organizations/{org_id}/warehouses/` | Warehouse facility. If omitted, server auto-defaults to organization's active warehouse. |
| **`location`** | `UUID` | Optional | Auto-resolved | `GET /api/organizations/{org_id}/locations/` | Optional specific shelf/bin/dock. If omitted, server auto-resolves from active warehouse. |
| **`items`** | `Array` | **Required** | `[]` | Array of Sale Line Item objects | Must contain at least 1 valid line item. |
| **`payment`** | `Object` | Optional | `null` | Payment Tender object | Upfront payment tendered at checkout (cash, card, bank transfer). Generates official Receipt. |
| **`due_date`** | `Date` (YYYY-MM-DD) | Optional | Customer payment terms | Calendar input | Payment due date. Defaults to customer credit terms if omitted. |
| **`discount_amount`** | `Decimal` (String) | Optional | `"0.00"` | Order concession | Header-level discount apportioned across lines. Requires `APPLY_DISCOUNT` permission. |
| **`tax_amount`** | `Decimal` (String) | Optional | `"0.00"` | Order tax calculation | Header-level tax apportioned across lines. |
| **`currency`** | `String` | Optional | Org default (`"USD"`) | 3-letter currency code | Sale and invoice transaction currency (PRD §24). |
| **`reference_number`**| `String` | Optional | `null` | External POS receipt / order code | External tracking reference or cashier receipt number. |
| **`notes`** | `String` | Optional | `""` | Free text | Delivery remarks or customer instructions. |

---

### 2.2 Sale Line Item Object (`items[...]`)

| Field Name | Type | Required? | Default | Source / Endpoint | Purpose & Business Rules |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`product`** | `UUID` | **Required** | None | `GET /api/organizations/{org_id}/products/products/` | Product from catalog. Must belong to the same organization. |
| **`quantity`** | `Integer` | **Required** | `1` | Cashier input | Must be an integer `>= 1`. |
| **`unit_price`** | `Decimal` (String) | Optional | Product `customer_sale_price` | Product catalog | Explicit price override. Counted in price override audit trail. |
| **`unit_name`** | `String` | Optional | Product `unit_of_measurement` | Packaging Unit | Packaging unit (e.g. `"Carton"`). Multiplier applied for stock movements. |
| **`variant`** | `UUID` | Optional | `null` | `GET /api/organizations/{org_id}/products/variants/` | Product variant (e.g. Size/Color). Must belong to the product. |
| **`discount`** | `Decimal` (String) | Optional | `"0.00"` | Cashier input | Per-line discount. Cannot be supplied if header `discount_amount` is given. |
| **`discount_type`** | `String` | Optional | `"fixed"` | Discount policy | Choice: `"fixed"`, `"percentage"`. |
| **`tax_rate`** | `Decimal` (String) | Optional | `"0.00"` | Tax percentage | Line tax rate percentage (e.g. `"7.50"`). |

---

### 2.3 Tendered Payment Object (`payment: { ... }`)

| Field Name | Type | Required? | Purpose & Business Rules |
| :--- | :--- | :--- | :--- |
| **`amount`** | `Decimal` (String) | **Required** | Amount tendered now. Must be `> 0` and `<= total`. |
| **`method`** | `String` | **Required** | Payment tender: `"cash"`, `"card"`, `"bank_transfer"`, `"pos"`. |
| **`reference`**| `String` | Optional | External transaction / POS terminal authorization reference. |

---

### 2.4 Sale Return Header (`POST /api/organizations/{org_id}/sales/returns/`)

| Field Name | Type | Required? | Source / Endpoint | Purpose & Business Rules |
| :--- | :--- | :--- | :--- | :--- |
| **`original_sale`** | `UUID` | **Required** | `GET /api/organizations/{org_id}/sales/` | Sale order being returned. |
| **`return_reason`** | `String` | **Required** | Cashier / Customer statement | Explanation for return (e.g. `"Damaged in transit"`). |
| **`refund_amount`** | `Decimal` (String) | Optional | Auto-computed from items | Must match the exact sum of line refund amounts. |
| **`refund_method`** | `String` | Optional | Choice: `"cash"`, `"store_credit"`, `"original_payment"` | Refund settlement method. |
| **`items`** | `Array` | **Required** | Array of Return Line Item objects | Must contain at least 1 return line. |

---

### 2.5 Sale Return Line Item Object (`items[...]`)

| Field Name | Type | Required? | Default | Purpose & Business Rules |
| :--- | :--- | :--- | :--- | :--- |
| **`sale_item`** | `UUID` | **Required** | Line UUID from `original_sale.items` | Original line being returned. |
| **`quantity`** | `Integer` | **Required** | `1` | Units returned. Cannot exceed `sale_item.quantity - sale_item.returned_quantity`. |
| **`refund_amount`**| `Decimal` (String) | **Required** | Line return value | Refund allocated to this line. Cannot exceed proportional line value. |
| **`condition`** | `String` | Optional | `"new"` | Item condition: `"new"`, `"open_box"`, `"damaged"`, `"defective"`. |
| **`restock_action`**| `String` | Optional | `"return_to_stock"` | `"return_to_stock"` (restocks warehouse) or `"write_off"` / `"quarantine"`. |

---

## 3. Sales Order Lifecycle & Inventory Mutation Diagram

```mermaid
flowchart TD
    A["POST /api/organizations/{org_id}/sales/<br/>Create Sale"] --> B["Atomic Transaction & Customer Row Lock"]
    B --> C["1. Validate & Apportion Lines<br/>_build_lines()"]
    C --> D["2. Deduct Inventory FIFO<br/>process_sale_inventory_and_ledger()"]
    D --> E["3. Synchronous Invoice Creation<br/>issue_invoice_for_sale()"]
    E --> F{"Payment Tendered?"}
    F -->|Yes: paid_now > 0| G["4. Record Payment & Generate Receipt<br/>record_invoice_payment()"]
    F -->|No: Credit Sale| H["5. Invoice Remains UNPAID"]
    G --> I["6. Centralized Audit Log<br/>sales_order.created"]
    H --> I

    I --> J{"Lifecycle Action"}
    J -->|Order Cancelled (PRD §44)| K["POST /sales/{id}/cancel/"]
    K --> L["Reverse Inventory & Log Ledger<br/>reverse_sale_inventory_and_ledger()"]
    L --> M["Set fulfillment_status = CANCELED<br/>sales_order.cancelled audit"]

    J -->|Customer Return (PRD §23)| N["POST /sales/returns/"]
    N --> O["Atomically increment returned_quantity<br/>Restock inventory & issue Refund"]
    O --> P["Set fulfillment_status = RETURNED if 100% returned"]
```

---

## 4. End-to-End Manual Testing Scenarios (Postman / cURL)

### Scenario 1: Paid Cash POS Sale (Full Payment Tendered Upfront)
* **Goal**: Validate that tender of full amount sets `payment_status="paid"`, creates an `Invoice` with status `"paid"`, creates a `Payment` record, and issues an official `InvoicePayment` (Receipt) starting with `R-`.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/`
* **Request Payload**:
```json
{
  "customer": "{{customer_id}}",
  "location": "{{location_id}}",
  "currency": "USD",
  "payment": {
    "amount": "100.00",
    "method": "cash",
    "reference": "POS-CASH-001"
  },
  "items": [
    {
      "product": "{{product_id}}",
      "quantity": 2,
      "unit_price": "50.00"
    }
  ]
}
```
* **Expected Response (`HTTP 201 Created`)**:
```json
{
  "sale_id": "SALE-XXXXXX",
  "payment_status": "paid",
  "fulfillment_status": "unfulfilled",
  "subtotal": "100.00",
  "total": "100.00",
  "items": [
    {
      "product": "{{product_id}}",
      "quantity": 2,
      "returned_quantity": 0,
      "unit_price": "50.00",
      "total": 100.00
    }
  ]
}
```

---

### Scenario 2: Unpaid B2B Credit Sale (Invoice Balance Due)
* **Goal**: Validate B2B wholesale credit sale where no payment is tendered at order placement.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/`
* **Request Payload**:
```json
{
  "customer": "{{customer_id}}",
  "location": "{{location_id}}",
  "due_date": "2026-11-30",
  "items": [
    {
      "product": "{{product_id}}",
      "quantity": 4,
      "unit_price": "50.00"
    }
  ]
}
```
* **Expected Response (`HTTP 201 Created`)**:
  * `payment_status`: `"unpaid"`
  * `total`: `"200.00"`
  * Backend generates an `Invoice` with status `"confirmed"` and `amount_paid: "0.00"`.

---

### Scenario 3: Partial Payment Sale (Split Balance)
* **Goal**: Customer pays $50 upfront on a $150 order.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/`
* **Request Payload**:
```json
{
  "customer": "{{customer_id}}",
  "location": "{{location_id}}",
  "payment": {
    "amount": "50.00",
    "method": "card",
    "reference": "TXN-CARD-9921"
  },
  "items": [
    {
      "product": "{{product_id}}",
      "quantity": 3,
      "unit_price": "50.00"
    }
  ]
}
```
* **Expected Response (`HTTP 201 Created`)**:
  * `payment_status`: `"partially_paid"`
  * `total`: `"150.00"`
  * Generates an official `InvoicePayment` receipt for $50 with remaining balance $100.

---

### Scenario 4: Minimalist Sale Without Location or Warehouse (Auto-Resolved)
* **Goal**: Validate that callers do not need to provide `location` or `warehouse`. The backend automatically defaults to the organization's active warehouse (and its default location if available).
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/`
* **Request Payload**:
```json
{
  "customer": "{{customer_id}}",
  "items": [
    {
      "product": "{{product_id}}",
      "quantity": 2,
      "unit_price": "50.00"
    }
  ]
}
```
* **Expected Response (`HTTP 201 Created`)**:
  * `warehouse`: Automatically populated with the organization's active warehouse UUID.
  * `location`: Automatically populated with the active warehouse's stock location UUID (or `null` if none configured).
  * `payment_status`: `"unpaid"`
  * `total`: `"100.00"`

---

### Scenario 5: Discount Permission Enforcement (Role-Based Access)
* **Goal**: Validate that users without `APPLY_DISCOUNT` (e.g. standard Sales Rep) are blocked with `HTTP 400 Bad Request` if attempting to grant discounts.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/` (Authenticated as Sales Rep)
* **Request Payload**:
```json
{
  "customer": "{{customer_id}}",
  "location": "{{location_id}}",
  "discount_amount": "20.00",
  "items": [
    {
      "product": "{{product_id}}",
      "quantity": 2,
      "unit_price": "50.00"
    }
  ]
}
```
* **Expected Response (`HTTP 400 Bad Request`)**:
```json
{
  "discount_amount": [
    "You do not have permission to apply discounts in this organization."
  ]
}
```

---

### Scenario 6: Customer Sales Return with Restocking & Cash Refund
* **Goal**: Customer returns 1 item from Scenario 1 for full refund and restocking.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/returns/`
* **Request Payload**:
```json
{
  "original_sale": "{{sale_id}}",
  "return_reason": "Customer ordered incorrect color",
  "refund_amount": "50.00",
  "refund_method": "cash",
  "items": [
    {
      "sale_item": "{{sale_item_id}}",
      "quantity": 1,
      "refund_amount": "50.00",
      "restock_action": "return_to_stock"
    }
  ]
}
```
* **Expected Response (`HTTP 201 Created`)**:
  * `status`: `"completed"`
  * `sale_item.returned_quantity` is atomically incremented to `1`.
  * Warehouse stock is increased by `1` and compensatory `InventoryLedgerEntry` is created.
  * A `Refund` record is generated against the original payment.

---

### Scenario 7: Over-Return Prevention (Boundary Check)
* **Goal**: Verify that returning more units than unreturned balance is strictly rejected.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/returns/`
* **Request Payload**:
```json
{
  "original_sale": "{{sale_id}}",
  "return_reason": "Over return attempt",
  "items": [
    {
      "sale_item": "{{sale_item_id}}",
      "quantity": 5,
      "refund_amount": "250.00"
    }
  ]
}
```
* **Expected Response (`HTTP 400 Bad Request`)**:
```json
{
  "code": "SALE_VALIDATION_ERROR",
  "detail": {
    "sale_item_id": "{{sale_item_id}}",
    "returnable": 1
  }
}
```

---

### Scenario 8: Sale Cancellation & Inventory Reversal (PRD §44)
* **Goal**: Cancel an unfulfilled sale order, restoring deducted stock back to the warehouse.
* **Method & URL**: `POST /api/organizations/{{org_id}}/sales/{{sale_id}}/cancel/`
* **Request Payload**:
```json
{
  "reason": "Customer called to cancel shipment prior to courier pickup."
}
```
* **Expected Response (`HTTP 200 OK`)**:
```json
{
  "sale_id": "SALE-XXXXXX",
  "fulfillment_status": "canceled",
  "cancelled_at": "2026-10-22T14:30:00Z",
  "cancelled_by": "{{user_id}}",
  "cancel_reason": "Customer called to cancel shipment prior to courier pickup."
}
```
* **Database Invariant Check**:
  * Warehouse `Inventory.quantity` is restored to pre-sale levels.
  * An `InventoryLedgerEntry` starting with `CNL-` is logged.
  * Centralized audit event `sales_order.cancelled` is logged.

---

### Scenario 9: Tenant Boundary Isolation Barrier
* **Goal**: Verify that users cannot view or manipulate sales belonging to an organization they do not belong to.
* **Method & URL**: `GET /api/organizations/{{other_org_id}}/sales/`
* **Expected Response (`HTTP 404 Not Found`)**:
```json
{
  "detail": "No Organization matches the given query."
}
```
