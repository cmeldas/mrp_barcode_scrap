# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ScrapBarcodeConfig(models.Model):
    _name = 'scrap.barcode.config'
    _description = 'Scrap Barcode Configuration'

    name = fields.Char(
        string='Name',
        required=True,
        default='Default Configuration',
    )
    default_scrap_reason_tag_id = fields.Many2one(
        'stock.scrap.reason.tag',
        string='Default Scrap Reason',
        help='Default scrap reason tag that will be pre-selected when scrapping products.',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
    )

    @api.model
    def get_default_config(self):
        """Return the default scrap reason tag for the current company."""
        config = self.search([
            ('active', '=', True),
            '|',
            ('company_id', '=', self.env.company.id),
            ('company_id', '=', False),
        ], limit=1)
        result = {
            'id': config.id if config else False,
            'default_scrap_reason_tag_id': False,
        }
        if config and config.default_scrap_reason_tag_id:
            result['default_scrap_reason_tag_id'] = config.default_scrap_reason_tag_id.id
        return result
