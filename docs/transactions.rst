Transactions App
================

Overview
--------

The ``transactions`` app is the core financial and inventory ledger for the RIINOX platform. It enforces strict double-entry accounting principles and acts as the single source of truth for every business event that has financial or stock implications. 

This app replaces the old decentralized approach where inventory and financial records were loosely coupled. Now, a single unified ``Transaction`` parent object guarantees that financial debits/credits and inventory stock movements are perfectly synchronized and balanced.

Core Models
-----------

1. **Transaction**
   The unified parent event. This represents a single atomic operation (e.g., Sale, Purchase, Stock Adjustment). 
   - **Fields**: ``transaction_id``, ``transaction_type``, ``status``, ``organization``.
   - **Relation**: Uses a ``GenericForeignKey`` (via ``content_type`` and ``object_id``) to point back to the originating business document (e.g., an ``Invoice``, ``Purchase``, ``PurchaseReturn``, or ``Payment``).

2. **FinancialLedgerEntry**
   The financial effect of a ``Transaction``, adhering to strict double-entry accounting rules.
   - **Fields**: ``account_type``, ``entry_type`` (``debit`` / ``credit``), ``amount``.
   - **Rule**: For any given ``Transaction``, the sum of all ``FinancialLedgerEntry`` debits must equal the sum of all credits.

3. **InventoryLedgerEntry (Located in `inventory` app)**
   The inventory effect of a ``Transaction``. This replaces the deprecated ``InventoryTransaction`` model. It resides in the ``inventory`` app to maintain strict domain boundaries, but links directly back to the ``Transaction`` event.
   - **Fields**: ``product``, ``variant``, ``batch``, ``warehouse``, ``quantity_moved``, ``unit_cost``.
   - **Rule**: Every quantity change anywhere in the system is represented by exactly one row here. Positive for stock coming in, negative for stock going out.

Chart of Accounts
-----------------
The platform uses a hardcoded, simplified chart of accounts (defined in ``LedgerAccountTypeChoices``):
- ``cash_bank`` (Cash / Bank)
- ``accounts_receivable`` (Accounts Receivable)
- ``accounts_payable`` (Accounts Payable)
- ``sales_revenue`` (Sales Revenue)
- ``cost_of_goods_sold`` (Cost of Goods Sold)
- ``inventory`` (Inventory Asset)
- ``discounts`` (Discounts Given)
- ``tax_payable`` (Tax Payable)


Transaction Flows (Matching the PRD)
------------------------------------

The following workflows outline how business events translate into Ledger Entries:

### 1. Sales Flow (Invoicing a Customer)
When an ``Invoice`` or ``Sale`` is finalized for a physical product:

- **Inventory Effect**:
  - The stock is reduced from the warehouse.
  - Generates an ``InventoryLedgerEntry`` with a negative ``quantity_moved``.

- **Financial Effect**:
  - Debit: ``accounts_receivable`` (Total Invoice Value)
  - Credit: ``sales_revenue`` (Product Selling Price)
  - Credit: ``tax_payable`` (If applicable)
  - Debit: ``cost_of_goods_sold`` (Cost of the product)
  - Credit: ``inventory`` (Cost of the product)

### 2. Customer Payment Flow (Receiving Cash from Customer)
When a ``Payment`` is logged against a customer ``Invoice``:

- **Financial Effect** (``record_payment_financial_entries``):
  - Debit: ``cash_bank`` (Amount paid)
  - Credit: ``accounts_receivable`` (Amount paid)

### 3. Purchase Flow (Receiving Stock from Supplier)
When a ``Purchase`` order is received into the warehouse via ``receive_purchase_order``:

- **Inventory Effect**:
  - Physical stock is added to the warehouse in base units (applying ``unit.multiplier`` and optional ``ProductBatch``).
  - Generates an ``InventoryLedgerEntry`` with a positive ``quantity_moved`` and base ``unit_cost``.

- **Financial Effect** (``record_purchase_financial_entries``):
  - Debit: ``inventory`` (Cost of goods received)
  - Credit: ``accounts_payable`` (Amount owed to supplier)

### 4. Supplier Payment Flow (Paying a Supplier / Vendor)
When a ``PurchasePayment`` is recorded against a ``Purchase`` order via ``record_purchase_payment``:

- **Financial Effect** (``record_purchase_payment_financial_entries``):
  - Debit: ``accounts_payable`` (Amount paid to supplier)
  - Credit: ``cash_bank`` (Amount paid to supplier)

### 5. Supplier Return Flow (Returning Goods to Supplier)
When a ``PurchaseReturn`` is recorded against a ``Purchase`` order via ``create_purchase_return``:

- **Inventory Effect**:
  - Physical stock is deducted from the warehouse in base units.
  - Generates an ``InventoryLedgerEntry`` with a negative ``quantity_moved``.

- **Financial Effect** (``record_purchase_return_financial_entries``):
  - Debit: ``accounts_payable`` (Total refund/credit value of returned goods)
  - Credit: ``inventory`` (Total refund/credit value of returned goods)

### 6. Stock Adjustment (Manual Corrections)
When an admin approves a ``StockAdjustmentRequest`` (e.g., missing or damaged stock):

- **Inventory Effect**:
  - Adjusts stock up or down.
  - Generates an ``InventoryLedgerEntry`` for the adjustment.

- **Financial Effect**:
  - Debit/Credit: ``inventory`` (Adjusting asset value)
  - Credit/Debit: ``cost_of_goods_sold`` or a dedicated variance expense account.

Immutability & Audit Trail
--------------------------
To satisfy PRD requirements regarding auditing and preventing "silent stock changes":
- ``Transaction``, ``FinancialLedgerEntry``, and ``InventoryLedgerEntry`` records are **immutable** once created.
- Editing historical entries is strictly forbidden. Any corrections to past mistakes must be made by creating a new, reversing ``Transaction`` (e.g., a Customer Return, Supplier Return, or Refund).
- The Django Admin panels for these models are strictly read-only.
