# -*- coding: utf-8 -*-
{
    'name': 'MRP Barcode Scrap',
    'version': '18.0.2.0.0',
    'category': 'Manufacturing',
    'summary': 'Scan barcodes to scrap products or count actual inventory',
    'description': """
MRP Barcode Scrap & Inventory Count
====================================

Scan barcodes from products to either scrap them or perform inventory counts.
Supports both weight-embedded barcodes and piece-count products.

Features:
- POS-like barcode scanning interface
- Automatic barcode nomenclature detection (weight / pieces)
- **Dual mode**: Scrap or Inventory Count
- Running totals per scanned product
- Editable quantities
- Configurable default scrap reason
- Total scrap value display
- Batch scrap document creation and confirmation
- **Inventory Count mode**: set actual stock from scanned quantities
- Post-action stock level review with links to adjust
- Czech translation included
    """,
    'author': 'Custom',
    'license': 'LGPL-3',
    'depends': [
        'mrp',
        'stock',
        'barcodes',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/scrap_config_data.xml',
        'views/scrap_config_views.xml',
        'views/scrap_dashboard_views.xml',
        'views/menu_views.xml',
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
