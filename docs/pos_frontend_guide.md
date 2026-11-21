# Point of Sale: frontend integration guide

How to build the sales screen (cart, checkout, walk-in customers, later payments) on the RIINOX API.
Every endpoint below is also in Swagger (`/api/docs/`) under **Carts**, **Sales**, **Payments** and
**Customers**, with full field lists and examples. This guide explains how the pieces fit together.

All paths start with `/api/organizations/{org_id}`. Send the JWT as usual. Ids are UUIDs.

---

## 1. The one rule

**The server owns the cart and does all the maths.** Prices, unit prices for cartons, discounts, tax and totals
are worked out by the backend, every time, with the same code that creates the sale. After every change you
send, the API returns the **whole cart, already priced**. Render that response. Do not:

- compute totals, tax or line prices in the frontend,
- keep your own copy of the cart as the source of truth (keep only the cart `id`),
- send prices unless the user deliberately overrides one (and has the right to).

Because the cart lives on the server, it survives a page refresh, a closed tab, a logout and a change of device.

---

## 2. The screen, step by step

| User action | Call | Notes |
|---|---|---|
| Opens the sales screen | `POST /carts/` `{"location": "<warehouse_id>"}` | Returns the cashier's cart at that location: `201` if new, `200` if it already existed. Store `id`. Call this on every screen load. |
| Scans or taps a product | `POST /carts/{id}/items/` `{"product_id": "..."}` | `quantity` defaults to 1. Adding the same product again **increases the quantity** of the existing line. |
| Sells by pack size | same, with `"unit_name": "carton"` | The unit must be the product's base unit or one of its unit conversions. Price is the carton price. |
| Changes a quantity | `PATCH /carts/{id}/items/{line_id}/` `{"quantity": 3}` | `0` removes the line. |
| Removes a line | `DELETE /carts/{id}/items/{line_id}/` | |
| Gives a line discount | `PATCH .../items/{line_id}/` `{"discount": "200.00"}` | Needs `APPLY_DISCOUNT`. |
| Overrides a price | `PATCH .../items/{line_id}/` `{"unit_price": "250.00"}` | Needs `EDIT_SELLING_PRICE`. `null` returns to the catalog price. |
| Picks the customer | `PATCH /carts/{id}/` `{"customer_id": "..."}` | Optional: a new cart is already on the **Walk-in Customer** (see §4). `null` switches back to walk-in. |
| Whole-cart discount | `PATCH /carts/{id}/` `{"discount_amount": "500.00"}` | Needs `APPLY_DISCOUNT`. Not allowed together with line discounts. |
| Empties the cart | `POST /carts/{id}/clear/` | Keeps customer and location. |
| Pays | `POST /carts/{id}/checkout/` (see §5) | Creates the sale, invoice and receipt. |
| After a successful checkout | `POST /carts/` again | Gives a fresh empty cart for the next customer. |

### Location (branch)

`location` is the warehouse id from the branch switcher. Members who work in **all** locations may leave it out
(the default location is used). Members limited to some locations **must** send it, otherwise
`400 LOCATION_REQUIRED`; a location they don't work in is `403 LOCATION_ACCESS_DENIED`. Each location has its
own cart, so switching branch means calling `POST /carts/` with the new location.

---

## 3. Reading the cart response

```json
{
  "id": "0b7c5e9a-...",
  "status": "active",
  "version": 5,
  "location": {"id": "495e06a8-...", "name": "Lekki"},
  "customer": {"id": "c41a0e6b-...", "name": "Walk-in Customer", "is_walk_in": true},
  "lines": [
    {
      "id": "7d1e3f5a-...",
      "product": {"id": "a2c4e6f8-...", "name": "Coca-Cola 50cl", "sku": "CC5-7K2Q9M"},
      "variant": null,
      "unit_name": "carton",
      "quantity": 2,
      "unit_price": "3600.00",
      "catalog_price": "3600.00",
      "price_overridden": false,
      "subtotal": "7200.00",
      "discount": "0.00",
      "tax": "540.00",
      "tax_rate": "7.5000",
      "total": "7740.00",
      "available_quantity": 14,
      "in_stock": true
    }
  ],
  "item_count": 2,
  "subtotal": "7200.00",
  "discount_amount": "0.00",
  "cart_discount": "0.00",
  "tax_amount": "540.00",
  "total": "7740.00",
  "prices_include_tax": false,
  "checkout_ready": true,
  "issues": [],
  "held_at": null,
  "updated_at": "2026-11-20T10:15:00Z"
}
```

| Field | How to use it |
|---|---|
| `lines[].id` | The `line_id` for PATCH / DELETE. |
| `lines[].available_quantity` | Stock at this location **in the line's unit** (30 pieces shows as 2 cartons). `null` = the product doesn't track stock (e.g. a service). |
| `lines[].in_stock` | `false` = show the line in red. Stock is **checked, not reserved**: another till can sell it meanwhile. |
| `lines[].price_overridden` | Show a "price changed" badge. |
| `total` | The amount to charge. Show this, never your own sum. |
| `prices_include_tax` | `true`: tax is inside the prices, print "VAT included". `false`: tax is added on top. |
| `checkout_ready` | **Enable the Pay button only when `true`.** |
| `issues` | Why checkout is blocked. Show each `message`; an issue with `item_id` belongs to that line. |
| `version` | Goes up on every change. If two devices edit one cart, show the response with the higher `version`. |
| money values | Strings with 2 decimals. Parse with a decimal library, not floats. |

### `issues[].code`

| Code | Meaning | What the user does |
|---|---|---|
| `CART_EMPTY` | No lines | Add a product |
| `OUT_OF_STOCK` | Not enough stock at this location for that line (counting every line of the same product) | Lower the quantity or remove it |
| `PRODUCT_UNAVAILABLE` | The product was made inactive after it was added | Remove the line |
| `PRICING_ERROR` | The cart can't be priced any more (e.g. a unit conversion was deleted) | Remove the line named in the message |

Stock and prices can change while a cart sits open, so re-fetch with `GET /carts/{id}/` when the screen regains
focus or before showing the payment dialog.

---

## 4. Customers and the Walk-in Customer

Every organization has one built-in **Walk-in Customer** (`is_walk_in: true`), created automatically. It is the
buyer for anonymous counter sales.

- Every **new cart starts on it**, so a normal shop sale needs no customer step at all.
- A walk-in sale **must be paid in full** at checkout. Anything less is `400 WALK_IN_MUST_PAY_IN_FULL`.
- To sell **on credit**, pick a registered customer first (`PATCH /carts/{id}` `{"customer_id": "..."}`).
- It can be **renamed** (e.g. "Cash Customer") but never deactivated, deleted, given credit, or put on a
  standalone invoice (`409 WALK_IN_CUSTOMER_PROTECTED`, `400 WALK_IN_NOT_INVOICEABLE`). Hide those actions for it.
- In customer pickers (`GET /customers/?search=...`), show it first or as the default.

Suggested UI: show the customer chip as "Walk-in" with a "Change" button; if the cashier picks a registered
customer, show their name and outstanding `balance`.

---

## 5. Checkout and payment

`POST /carts/{id}/checkout/`

| Situation | Body |
|---|---|
| Cash, exact or with change | `{"payment": {"method": "cash", "amount": "10000.00"}}` |
| Card / POS / transfer, full amount | `{"payment": {"method": "pos"}}` (no `amount` = the full total) |
| Part payment (registered customer only) | `{"payment": {"method": "bank_transfer", "amount": "5000.00", "reference": "TRF-123"}}` |
| On credit (registered customer only) | `{"due_date": "2026-12-20"}` (no `payment`) |

Payment methods: `cash`, `pos`, `card`, `bank_transfer`, `online` (see Swagger for the full list).

Rules:

- **Cash above the total** is fine: the response has `change_due` (show "Change: ₦500").
- Card, POS and transfer **can't exceed the total** (`400`).
- Credit beyond the customer's `credit_limit` needs approval rights (`409 CREDIT_LIMIT_EXCEEDED` otherwise).
- Stock is checked again, under lock. If it ran out since the line was added you get a `400` and **nothing
  happens**: no sale, no stock change, the cart stays open. Re-fetch the cart and let the user fix it.
- **Retries are safe.** If the request times out, send the same checkout again: a cart that is already checked
  out returns its sale with `200` instead of selling twice. Disable the Pay button while the request runs anyway.

Response: `201` with the sale (same body as `GET /sales/{id}/`): `sale_id`, `total`, `payment_status`
(`paid` / `partially_paid` / `unpaid`), `change_due`, items. Print or show the receipt from it, then call
`POST /carts/` for the next customer.

---

## 6. Hold and resume (serving the next person)

| Action | Call |
|---|---|
| Park the current cart | `POST /carts/{id}/hold/` `{"label": "Lady in red"}`, then `POST /carts/` for a fresh one |
| List parked carts | `GET /carts/?status=held` (rows have `label`, `customer`, `line_count`, `item_count`, `held_at`) |
| Bring one back | `POST /carts/{id}/resume/` |
| Throw one away | `DELETE /carts/{id}/` |

Resuming makes the held cart the active one. The cart you were using is held in its place (or deleted if it was
empty), so nothing is lost. An empty cart can't be held; each person can hold up to 20 carts. Carts untouched
for 7 days are abandoned automatically and disappear from the lists.

Each cashier only ever sees **their own** carts.

---

## 7. Paying an invoice later (credit customers)

When a registered customer comes back to pay what they owe:

1. Find their unpaid invoices: `GET /invoices/?customer=<id>` (look at `balance_due`).
2. Record the payment: `POST /payments/`
   ```json
   {"invoice_id": "...", "method": "bank_transfer", "amount": "20000.00", "reference": "TRF-558201"}
   ```
   Leave `amount` out to pay the whole balance. An invoice can take any number of payments.
3. The response is the payment **and its receipt** (`receipt_number`), plus the invoice's new `status` and
   `balance_due`. Cash above the balance comes back as `change_due`.
4. Payment history: `GET /payments/?invoice=<id>` or `?customer=<id>`.

Send a `reference` you generate (e.g. a UUID) for every payment attempt: resending the same reference returns
the original payment (`200`, `"replayed": true`) instead of charging twice.

---

## 8. Permissions (hide what the user can't do)

| Permission | Unlocks |
|---|---|
| `CREATE_SALE` | Everything on carts, checkout, `POST /sales/` |
| `APPLY_DISCOUNT` | Line and cart discounts |
| `EDIT_SELLING_PRICE` | Price overrides |
| `RECORD_PAYMENT` | `POST /payments/` |
| `VIEW_PAYMENTS` or `VIEW_RECEIPTS` | Payment / receipt lists |

The backend enforces all of these (`403` with a `code`), but hide or disable the controls so users aren't
surprised. Rights are checked again at checkout, because a held cart can outlive a role change.

---

## 9. Errors

Every error has the same shape: a machine-readable `code` and a human `detail` message (validation errors also
have `errors`, keyed by field).

```json
{"code": "WALK_IN_MUST_PAY_IN_FULL", "detail": "A walk-in sale must be paid in full. ..."}
{"code": "VALIDATION_ERROR", "detail": "Invalid input.", "errors": {"quantity": ["Ensure this value is greater than or equal to 1."]}}
```

Switch on `code`; show `detail` to the user. Unknown fields in a request body are rejected (`400`) so a typo
never silently does nothing.

| Code | Status | When |
|---|---|---|
| `CART_NOT_FOUND`, `CART_ITEM_NOT_FOUND` | 404 | Wrong id, or someone else's cart |
| `PRODUCT_NOT_FOUND`, `VARIANT_NOT_FOUND` | 404 | Product not in this organization, or variant of another product |
| `CART_CLOSED` | 409 | The cart was checked out or discarded: call `POST /carts/` for a new one |
| `CART_FULL` | 409 | 200 different lines |
| `HELD_CART_LIMIT` | 409 | 20 held carts |
| `CART_EMPTY` | 400 | Holding or checking out an empty cart |
| `PRODUCT_UNAVAILABLE` | 409 | Inactive or discontinued product |
| `DISCOUNT_NOT_ALLOWED`, `PRICE_OVERRIDE_NOT_ALLOWED` | 403 | Missing right |
| `LOCATION_REQUIRED` / `LOCATION_ACCESS_DENIED` | 400 / 403 | See §2 |
| `WALK_IN_MUST_PAY_IN_FULL` | 400 | Walk-in sale not fully paid |
| `CREDIT_LIMIT_EXCEEDED` | 409 | Credit sale above the customer's limit without approval |
| `INVOICE_NOT_PAYABLE`, `PAYMENT_EXCEEDS_BALANCE`, `DUPLICATE_PAYMENT_REFERENCE` | 409 | `POST /payments/` |

---

## 10. Checklist for the sales screen

- [ ] On load: `POST /carts/` with the selected branch; keep only the cart `id`.
- [ ] Render every response as the new cart state; no client-side maths.
- [ ] Pay button enabled only when `checkout_ready` is `true`; show `issues`.
- [ ] Out-of-stock lines in red, using `in_stock` and `available_quantity`.
- [ ] Customer chip defaults to "Walk-in"; credit options only for registered customers.
- [ ] Disable Pay while checkout runs; on timeout, retry the same checkout.
- [ ] Show `change_due` after a cash sale; then open a fresh cart.
- [ ] Held carts screen from `GET /carts/?status=held`, with Resume and Discard.
- [ ] Discount and price-override controls hidden without the matching permission.
- [ ] Re-fetch the cart when the screen regains focus.
