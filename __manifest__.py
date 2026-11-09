# -*- coding: utf-8 -*-
{
    'name': 'MRP Barcode Scrap',
    'version': '20.0.1.2.1',
    'category': 'Manufacturing',
    'summary': 'Scan barcodes of expiring products and scrap them at once, or count the stock',
    'description': """
MRP Barcode Scrap
=================

Scan barcodes from products that expire and scrap them at once.
Supports both weight-embedded barcodes and piece-count products.

Features:

- POS-like barcode scanning interface
- Automatic barcode nomenclature detection (weight / pieces)
- Running totals per scanned product
- Editable quantities
- Configurable default scrap reason
- Total scrap value display
- Batch scrapping: one done scrap per product (Inventory › Operations › Adjustments › Scrap)
- Post-scrap stock level review with links to adjust
- Inventory count mode (switch in the top right corner): the scanned
  quantities add up and become the new stock of the scanned products
- Update Stock and Set Stock to Zero buttons on the product and variant form
- Negative Stock list (Inventory › Operations › Adjustments, dashboard in inventory mode):
  products with negative stock to filter first, then Set Stock to Zero with a preview
- Czech translation included
    """,
    'author': 'Matouš Čmelík',
    'license': 'LGPL-3',
    'depends': [
        'mrp',
        'stock',
        'barcodes',
        'product',
    ],
    'data': [
        'security/ir.access.csv',
        'data/scrap_config_data.xml',
        'views/scrap_config_views.xml',
        'views/scrap_dashboard_views.xml',
        # before the menus: action_negative_stock is used by a menu item
        'views/product_views.xml',
        'views/menu_views.xml',
        'wizard/stock_quick_update_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mrp_barcode_scrap/static/src/js/scrap_dashboard.js',
            'mrp_barcode_scrap/static/src/xml/scrap_dashboard.xml',
            'mrp_barcode_scrap/static/src/css/scrap_dashboard.css',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
