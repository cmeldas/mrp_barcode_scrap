# MRP Barcode Scrap

Odoo 19 Community Edition addon for batch scrapping products via barcode scanning, counting the
stock with the same scanner, and quick stock updates from the product form.

## Features

- **POS-like barcode scanning interface** — fast, keyboard-driven workflow
- **Weight-embedded barcode support** — automatic detection via Odoo barcode nomenclature
- **Piece-count barcode support** — standard product barcodes increment by 1
- **Running totals** — see quantity and value per scanned product in real-time
- **Editable quantities** — adjust amounts with +/- buttons or direct input
- **Configurable default scrap reason** — set once, use every time
- **Total scrap value** — always visible summary
- **Batch scrap creation** — one click creates separate scrap orders per product and confirms them
- **Confirmation popup** — review everything before committing
- **Post-scrap stock review** — see updated stock values with links to adjust inventory
- **Inventory count mode** — switch in the top right corner of the dashboard; scanned quantities add up
  and become the new stock of the scanned products
- **Update Stock / Set Stock to Zero** — buttons on the product and variant form, plus a list action
  that sets the stock of the selected products to zero
- **Czech translation included**

## Screenshots

![Main](./docs/main_window.png)

![Confirmation](./docs/confirm_window.png)

![Done](./docs/done_window.png)

## Installation

1. Copy the `mrp_barcode_scrap` folder to your Odoo addons directory.
2. Update the apps list in Odoo (Settings → Apps → Update Apps List).
3. Search for "MRP Barcode Scrap" and install.

## Configuration

1. Go to **Manufacturing → Configuration → Scrap Configuration**.
2. Set the default scrap reason (e.g., "Expired product").

## Usage

### Scrapping

1. Navigate to **Manufacturing → Barcode Scrap**.
2. Scan product barcodes (or type them manually).
3. Adjust quantities as needed.
4. Edit the scrap reason if necessary.
5. Click **Scrap** and confirm in the popup.
6. Review the post-scrap stock levels and adjust if needed.

### Inventory count

1. On the dashboard turn on the **Inventory** switch in the top right corner (only inventory
   administrators see it). The screen turns blue and the title changes to *Inventory Count*.
2. Scan every piece that is really in stock. Scans of the same product add up (pieces +1, weight
   barcodes add their weight); the quantity can also be typed or changed with +/-. A quantity of 0
   is a valid count: the stock of that product goes to zero.
3. The list shows the current stock and the difference. **Apply Inventory Count** shows a summary
   and after confirmation sets the stock of the scanned products to the counted quantity.
   **Products that were not scanned are not changed.**
4. The result screen shows the stock before and after, with links to the product and to *Update Stock*.

An unfinished count is kept in the browser (per user) and restored after a page reload or when you
come back from another menu. Switching the mode with scanned products asks first and clears the list.
Products that do not track stock are refused on scan; products tracked by lots or serial numbers
must be counted in *Inventory › Physical Inventory*.

### Update Stock / Set Stock to Zero

- **Product or variant form** (header, inventory administrators only):
  - *Update Stock* opens a dialog with the current and the new quantity, one line per variant
    (a product template with variants lists all of them). Only changed lines are booked.
  - *Set Stock to Zero* asks for confirmation and sets the stock of the product (all its variants on
    a template, only that variant on a variant form) to 0.
- **Product or variant list**: select products → *Actions* → *Set Stock to Zero*. A dialog lists the
  selected products that have stock (already zero ones are left out), lines can be removed, and the
  button *Set Stock to Zero* applies it.

Every change is a regular inventory adjustment (move from/to the inventory loss location) in the
main warehouse of the company, with the reference *Inventory count (barcode)*, *Stock updated
manually* or *Stock set to zero* in the moves history. The whole stock of the product in the
warehouse (all its internal locations) is set to the new quantity. Negative stock is fine as a
starting point; a negative target is refused.

## Dependencies

- `mrp` (Manufacturing)
- `stock` (Inventory)
- `barcodes` (Barcode Nomenclature)
- `product` (Products)

## Tests

```
odoo -d <db> -i mrp_barcode_scrap --test-enable --test-tags /mrp_barcode_scrap --stop-after-init
```
