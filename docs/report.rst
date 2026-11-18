Reports Application
===================

The ``core.applications.report`` app provides organization-scoped and
location-scoped business intelligence across Overview, Sales, Customers,
Suppliers, Refunds, Payments, and Inventory, along with structured JSON
and CSV exports.

Architecture
------------

* Reuses ``core.applications.dashboard.period`` and ``core.helper.org_time``
  for organization-timezone period resolution.
* Reuses ``core.applications.warehouse.access.LocationScope`` and
  ``OrganizationScopedMixin`` for branch-level isolation.
* Reuses ``core.applications.dashboard.services`` money definitions
  (``sales_total``, ``cost_of_goods_sold``, ``_sale_returns``) so figures
  match the Dashboard exactly.

API Endpoints
-------------

All endpoints are mounted under ``/api/organizations/{organization_id}/reports/``:

* ``GET /`` — Overview (Gross profit over time, Sales by category, Payment status & methods, Browse reports)
* ``GET /sales/`` — Sales Report (``VIEW_SALES_REPORTS``)
* ``GET /customers/`` — Customer Report (``VIEW_SALES_REPORTS``)
* ``GET /suppliers/`` — Supplier Report (``VIEW_INVENTORY_REPORTS``)
* ``GET /refunds/`` — Refund Report (``VIEW_SALES_REPORTS``)
* ``GET /payments/`` — Payment Report (``VIEW_FINANCIAL_REPORTS``)
* ``GET /inventory/`` — Inventory Report (``VIEW_INVENTORY_REPORTS``)
* ``GET /{report_type}/export/?export_format=json|csv`` — Full export data
