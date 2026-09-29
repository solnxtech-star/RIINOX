# RIINOX Product Requirements Document Version 1.0

**Status:** Product & Engineering Specification | **Platform:** Responsive Web Application | **Architecture:** Organization-Based Multi-Tenant SaaS

## 1. Vision, Architecture & Tenants

**1. Executive Summary:** RIINOX is a multi-tenant business management platform that enables organizations to manage their products, services, inventory, purchasing, sales, customers, suppliers, payments, invoices, receipts, staff, reporting and operational activities from one centralized system. RIINOX is intentionally not a warehouse management system; inventory is one capability within a broader platform, supporting businesses with physical stores, no inventory, or service-based operations. The platform provides a shared business foundation while allowing configuration of business types, terminology, modules, workflows, permissions, and operational requirements.

**2. Product Vision:** RIINOX should become the operating system for day-to-day business operations, replacing fragmented spreadsheets and tools. Organizations should be able to answer questions regarding inventory stock, sales/purchases history, transaction tracking, approvals, financial impacts, and profitability.

**3. Product Goals:** RIINOX must provide centralized business management, strict multi-tenant isolation, support products/services/inventory, manage sales/purchases/customers/suppliers, and handle invoices/receipts/payments/customer credit. It must provide reporting, granular permissions, maintain an immutable audit trail, prevent silent inventory changes, prevent unauthorized financial operations, preserve transaction history, support multiple business types and locations, provide configurable document templates, provide subscription-based SaaS access, and remain extensible.

**4. Product Non-Goals:** RIINOX is not initially intended to be a full accounting/ERP replacement, a banking platform, a warehouse-only platform, a payment processor, or a complete management system specialized for hospitals, schools, hotels, or POS hardware ecosystems.

**5. Core Product Architecture:**
* **Conceptual relationship:** User > Organization > Business Type > Roles & Permissions > Products / Services > Customers / Suppliers > Operations > Transactions > Payments > Reports / Audit.
* **Business event model:** Business Event > Validation > Authorization > Transaction > Financial Effect > Inventory Effect > Document > Audit Event > Notification.

**6. Multi-Tenant Architecture:** Every organization is an independent tenant, and users may belong to multiple organizations. Each organization owns its profile, memberships, settings, and business data. The backend must enforce organization boundaries; frontend filtering is never considered a security mechanism, and every database record must contain or resolve to an organization context.

**7. Organization Management:** The creator becomes the owner. Profiles contain: name/legal name, business type, logo, phone/email/address, country/currency/timezone, tax info, registration info, invoice/receipt settings, document numbering, and operational configuration. Switching organizations must invalidate organization-scoped frontend data.

**8. Business Types:** Initial types include Wholesale, Retail, Fashion, Pharmacy, School, Hospitality, Supermarket, Distribution, General Trading, Services, and Other. Business type influences terminology, suggested modules, dashboards, product attributes, workflows, and reports, while sharing the core architecture.

**9. Configurable Modules:** Core modules include Dashboard, Products, Categories, Customers, Suppliers, Sales, Purchases, Inventory, Invoices, Receipts, Payments, Reports, Team, Roles & Permissions, Notifications, Audit Logs, Settings, and Subscription/Billing. Optional modules include Locations/branches, Stock transfers, Product variants, Barcode/QR, Customer credit, Supplier balances, Returns, Approvals, Batch/expiry, Advanced tax, Student/Guest/Service management, and Document templates.

**10. SaaS Subscription:** Subscriptions belong to the organization. Plans may control limits on users, products, transactions, locations, storage, API access, and advanced features. Free access provides core functionality; paid plans unlock custom templates and integrations. Limits must be enforced server-side.

**11. Authentication & Onboarding:** Requirements include registration, email verification, login/logout, password reset, session management, secure password hashing, HTTPS, failed-login monitoring, and inactivity timeout, with future plans for 2FA and SSO.
* **Onboarding flow:** Sign Up > Verify Email > Create Organization > Select Business Type > Configure Business > Add Products / Services > Configure Branding > Invite Team > Dashboard.

## 2. Core Business Operations

**12. Dashboard:** Default metrics include today's sales, revenue, outstanding payments, purchases, customers, suppliers, products, available/low/out-of-stock items, recent transactions, top-selling products, sales by staff, alerts, and pending approvals. Sensitive metrics like cost and profit require explicit permission.

**13. Product & Service Management:** Supports physical products, services, consumables, digital items, bundles, packages, non-stock items, and variants. Fields include: ID, name, SKU, barcode, QR code, image, category, brand, description, product type, unit of measurement, quality/grade, purchase/vendor cost, selling price, minimum stock, inventory tracking, opening/current stock, tax configuration, status, and created/updated metadata. Purchase costs are permission-controlled.

**14. Product UI & Variants:** List pages provide search, filters, pagination, sorting, and permission-aware columns. Detail tabs include Overview, Pricing, Inventory, Variants, Transactions, Activity, and Audit.
* **Creation flow:** Products > Add Product > Basic Information > Pricing > Inventory Configuration > Variants > Tax > Review > Create Product > Audit Event.

**15. Inventory Management:** Every inventory movement creates a ledger entry containing: transaction ID, organization, product, variant, location, quantity before/moved/after, transaction type, reference document, user, timestamp, approval status, reason, and notes. Direct arbitrary stock mutation is prohibited.

**16. Inventory Transactions & Rules:** Types include opening stock, purchase, goods received, sale, returns, stock adjustment, damaged, lost, transfer, count, correction, and authorized movement.
* **Purchase created:** Purchase Created > No Stock Change.
* **Receiving:** Goods Received > Increase Inventory > Create Ledger Entry.
* **Sale:** Sale Confirmed > Validate Stock > Decrease Inventory > Create Ledger Entry.
* **Transfer:** Source Location > Decrease > Destination Location > Increase.

**17. Stock Adjustment & Approval Flow:**
* **Adjustment flow:** Inventory > Select Product > Request Adjustment > Enter Quantity > Select Reason > Add Notes > Submit > Approval Required?.
* **If approval required:** Pending > Manager Review > Approved > Inventory Updated > Ledger Created > Audit Created (rejected requests leave inventory unchanged and audit).

**18. Purchase Management:** Records contain supplier, products, quantities, unit costs, ordered/received quantities, totals, dates, supplier invoice, payment status, amounts, user, documents, and notes. Only received quantities affect inventory.
* **Purchase flow:** Purchases > Create Purchase > Select Supplier > Add Products > Enter Quantities > Enter Costs > Review > Create Purchase > Receive Goods > Update Inventory > Record Supplier Liability.

**19. Supplier Management:** Records contain supplier ID, company, contact information, address, products supplied, purchase history, total purchases, amount paid, outstanding balance, status, and notes. Balances are derived from history.

**20. Customer Management:** Records contain customer ID, name/company, contact information, address, customer type, credit limit, balance, sales history, payment history, status, and notes. Terminology adapts by business type.

**21. Sales Management:**
* **Core flow:** Select Customer > Select Product / Service > Enter Quantity > Validate > Calculate > Apply Discount / Tax > Confirm Sale > Generate Invoice > Record Payment > Generate Receipt > Update Inventory > Audit.

**22. Stock Availability & Sales Integrity:** Sales are blocked if stock is insufficient unless explicitly authorized by organization rules. Sale confirmation must be atomic, leaving no partially completed business states.

**23. Sales Returns:** Returns reference original sales and include transaction data, condition, reason, and effects.
* **Return flow:** Original Sale > Request Return > Select Items > Enter Reason > Validate Returnable Quantity > Approval > Approve > Inventory Effect > Refund / Credit > Audit.

**24. Invoice Management:** Invoices track organizations, dates, products, pricing, tax, and status. Statuses include Draft, Pending, Confirmed, Paid, Partially Paid, Overdue, Cancelled. Completed invoices cannot be silently edited.
* **Correction flow:** Original Invoice > Cancel / Void > Create Corrected Invoice.

**25. Receipts & Documents:** Receipts log amounts paid, methods, and balances, outputting to PDF, downloadable, email-ready, or WhatsApp-ready formats. Templates (Default, System, Custom) support custom branding, headers, footers, and numbering based on subscription plans.

**26. Payments & Customer Credit:** Supported payment methods include cash, bank transfer, POS, card, online payment, and organization-defined methods. Multiple payments can attach to one transaction, and optional credit tracking logs limits, due dates, and balances.

**27. Financial Calculations:** Revenue represents generated money, COGS represents inventory costs. Gross Profit = Sales Revenue - Cost of Goods Sold. Profit information is permission-controlled.

## 3. Security, Traceability & System Modules

**28. Roles & Permissions:** Granular permissions control viewing/editing products, costs, inventory, sales, invoices, reports, users, settings, and audit logs. Default roles: Owner, Administrator, Manager, Sales Manager, Sales Representative, Inventory Manager, Inventory Staff, Accountant, Finance Officer, Teacher/Staff, Front Desk, Custom Role.

**29. Permission Enforcement:** Enforced at Frontend (hiding UI), API (rejecting requests), and Business Logic (validating before execution). Frontend checks alone are insufficient.

**30. Approval Engine:** Approvals are required for large inventory adjustments, price changes, invoice/receipt cancellations, large discounts, credit, returns, and write-offs.
* **Approval states:** Pending > Approved / Rejected / Cancelled.

**31. Audit & Traceability:** Immutable audit events log the organization, user, action, resource, previous/new values, timestamp, IP/device, reason, and approval reference.
* **Traceability flow:** Supplier > Purchase > Receiving > Inventory > Sale > Invoice > Payment > Receipt > Customer.

**32. Fraud & Loss Prevention:** Detects unrecorded sales, unauthorized adjustments/price changes, suspicious cancellations/returns/discounts, stock variance, suspicious staff activity, failed logins, and permission changes.

**33. Notifications:** Categories cover Inventory (low/out of stock, variance, expiry), Sales (large sales, failed transactions, refunds), Security (failed logins, permission changes), and Operations (pending approvals). Channels include in-app, email, SMS, and future WhatsApp.

**34. Reporting & Search:** Supports reporting for Sales (date, staff, product, category), Inventory (current stock, movement, valuation, variance), Purchases, and Financials (revenue, COGS, gross profit, balances). Global search respects permission boundaries.

**35. Locations & Branches:**
* **Hierarchy:** Organization > Branch / Location > Storage Area > Inventory. Locations can have isolated staff, inventory, customers, and reports.

**36. Industry Modules** | Capabilities Extending Core Platform
---|---
**Retail & Wholesale** | Quick sales, barcodes, bulk quantities, customer-pricing, MOQ, purchase orders.
**Fashion & Pharmacy** | Sizes, colors, variants, seasonal collections, batch, expiry, strength, dosage/form, expiry alerts.
**School & Hospitality** | Students, guardians, academic sessions, fees, guests, rooms, reservations, service charges.

## 4. Engineering, Data & API Contracts

**37. Frontend Application Structure:** Routes dynamically hide unavailable modules (/dashboard, products, inventory, sales, purchases, customers, suppliers, invoices, receipts, payments, reports, team, roles, notifications, audit, locations, settings, billing).

**38. Standard UI Patterns & States:**
* **List pages:** Page Header > Primary Action > Search / Filters > Summary > Data Table > Pagination.
* **Detail pages:** Header > Status > Primary Actions > Summary > Tabs > Activity > Audit.
* **Form pages:** Header > Form Sections > Validation > Review > Submit.

**39. Frontend API Contract:** Frontend must not independently implement business rules. The backend handles authorization, organization validation, pricing, inventory validation, tax calculations, ledger creation, and document generation.

// Example Sale POST Payload
```json
{
"customer_id": "customer-id",
"items": [{ "product_id": "product-id", "variant_id": "variant-id", "quantity": 2 }],
"discount": 500,
"payment": { "amount": 10000, "method": "cash" }
}
```

**40. Backend Domain Structure:** Organized by business domains: identity, organizations, memberships, roles, permissions, subscriptions, products, categories, customers, suppliers, inventory, purchases, sales, invoices, receipts, payments, reports, notifications, audit, locations, templates.

**41. Core Backend Entities:** User, Organization, Membership, Role, Permission, SubscriptionPlan, Category, Product, ProductVariant, Customer, Supplier, Location, StorageArea, Purchase, PurchaseItem, Receiving, Sale, SaleItem, SaleReturn, Invoice, Receipt, Payment, InventoryLedgerEntry, Approval, ApprovalAction, Notification, AuditLog, DocumentTemplate, OrganizationBranding.

**42. Backend Transaction Boundaries:**
* **Sale execution:** Begin Transaction > Validate Permission > Validate Organization > Validate Customer > Validate Products > Validate Stock > Calculate Amount > Create Sale > Create Sale Items > Update Inventory > Create Ledger > Create Invoice > Create Payment > Create Receipt > Create Audit Event > Commit.

**43. Inventory Concurrency:** The backend must protect stock from race conditions using appropriate database-level concurrency control, preventing multiple sales from consuming the same stock simultaneously.

**44. Data Integrity & Deletion:** Completed business records must generally not be hard-deleted; operations should use cancel, void, reverse, archive, or corrected transaction status.

**45. API Design:** Must be versioned, predictable, permission-aware, organization-aware, paginated, filterable, sortable, documented, and idempotent (e.g., /api/v1/products/, /api/v1/sales/).

**46. API Responses:** Must return structured standard JSON for success, validation errors, permission errors, and business-rule errors (e.g., INSUFFICIENT_STOCK).

**47. Pagination, Filtering & Sorting:** Large collections require server-side support for search, date ranges, statuses, categories, locations, and user filtering.

**48. Performance & Scalability:** Engineering strategies include proper database indexes, query optimization, caching, background processing, asynchronous tasks, and optimized reporting queries.

**49. Security:** Requires secure password hashing, HTTPS, strict tenant isolation, permission checks, rate limiting, secure sessions, input validation, output filtering, and sensitive-field protection.

**50. Data Backup, Recovery & Observability:** Backups cover all operational data. Observability includes structured logs, request IDs, error tracking, database monitoring, and security event logging.

**51. Background Jobs & Automation:** Handles emails, scheduled reports, PDF generation, low-stock alerts, analytics, payment reminders, and future AI insights or reorder recommendations.

## 5. Implementation Strategy & Principles

**52. Core User Journeys:**
* **New organization:** Register > Verify > Create Organization > Select Business Type > Configure > Add Product > Invite Staff > Dashboard.
* **Retail sale:** Dashboard > New Sale > Search Product > Add Product > Enter Quantity > Payment > Confirm > Receipt.
* **Wholesale purchase:** Purchases > Create Purchase > Supplier > Products > Quantities > Costs > Save > Receive > Inventory Updated > Supplier Balance Updated.
* **Customer credit payment:** Customer > Outstanding Balance > Record Payment > Payment Validation > Payment Created > Balance Updated > Receipt.

**53. Frontend Development Requirements:** Engineers must receive API contracts, permission matrix, validation rules, status definitions, UI states, route definitions, and pagination behavior.

**54. Backend Development Requirements:** Engineers must provide request/response schemas, permission requirements, business rules, transaction boundaries, audit events, pagination, and organization scoping.

**55. Permission Matrix** | **Access Profiles**
---|---
**Owner & Admin** | Full organization, billing, settings, users, roles, reports, audit access, and broad administration.
**Manager & Sales** | Operational management, products, selling prices, sales, invoices, payments, and receipts.
**Inventory & Finance** | Inventory, receiving, adjustments, financial reports, invoices, profit-related information, and custom roles.

**56. MVP Strategy** | **Implementation Phases**
---|---
**Phase 1 & 2** | SaaS Foundation (registration, isolation, switching, roles, subscriptions, onboarding) and Core Business (products, customers, sales, purchases, payments, inventory ledger).
**Phase 3 & 4** | Business Control (reports, audit, approvals, returns, templates) and Advanced Platform (barcode, industry modules, AI analytics, mobile, automation).

**57. Phase Definition of Done:** Phase 1 is done when tenant isolation and onboarding work end-to-end. Phase 2 is done when atomic critical operations and inventory ledgers are functional. Phase 3 covers functional reporting and approvals, while Phase 4 requires production-ready advanced automation and mobile integrations.

**58. Acceptance Criteria:** Organizations are strictly isolated, users can switch contexts, stock availability is strictly validated during sales (preventing overselling/silent changes), server-side permissions protect sensitive fields, and immutable audit logs capture all actions.

**59. Success Metrics:** Evaluated by organization onboarding rates, transaction volumes, API latency, and uptime. Operational targets mandate Inventory Accuracy >= 99% and Unrecorded Sales = 0.

**60. Engineering Principles & Final Product Definition:**
* 1. Business events are the source of truth.
* 2. Inventory never changes silently.
* 3. Completed transactions are historical records.
* 4. Frontend authorization is not security.
* 5. Every organization is isolated.
* 6. Sensitive information is permission-controlled.
* 7. Important business actions are attributable.
* 8. Industry-specific functionality extends the core platform.
* 9. The system optimizes for business workflows, not database structure.
* 10. Frontend and backend share explicit contracts.
* **Final product model:** User > Organization > Business Type > Roles & Permissions > Products / Services > Business Operations > Transactions > Payments > Documents > Reports > Audit.
