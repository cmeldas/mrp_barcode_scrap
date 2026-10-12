# -*- coding: utf-8 -*-
"""Migrace 19.0.1.1.0 → 20.0.1.1.0, před načtením dat modulu.

Odoo 20 nahradilo ir.model.access a ir.rule modelem ir.access. Modul v 19 měl šest řádků
v security/ir.model.access.csv, ve 20 je zakládá security/ir.access.csv pod stejnými XML ID.
Když v ir_model_data zůstane staré XML ID s modelem 'ir.model.access', načtení CSV spadne
(„found record of different model ir.model.access“). Proto se tu ta stará XML ID modulu
smažou a 20 záznamy založí znovu. Staré řádky tabulky ir_model_access zůstávají (OpenUpgrade
nic nemaže), Odoo 20 je nečte.

Pokud migrační rámec XML ID už převedl na ir.access, nic se tu nesmaže (hledají se jen
záznamy se starým modelem), skript je tedy neškodný i při opakovaném spuštění.

Vlastní data modulu (scrap.barcode.config s výchozím důvodem vyřazení) migraci nepotřebují:
stock.scrap.reason.tag ve 20 zůstává. Modul na stock.scrap neodkazuje žádným uloženým polem;
převod historických vyřazení (stock.scrap → stock.move s is_scrap) je věc migrace modulu stock.
"""
import logging

_logger = logging.getLogger(__name__)

MODULE = "mrp_barcode_scrap"
OLD_SECURITY_MODELS = ("ir.model.access", "ir.rule")


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = %s
           AND model IN %s
     RETURNING name, model
        """,
        (MODULE, OLD_SECURITY_MODELS),
    )
    removed = cr.fetchall()
    if removed:
        _logger.info(
            "%s: removed %d obsolete access XML IDs (%s), ir.access.csv recreates them",
            MODULE, len(removed), ", ".join(name for name, _model in removed),
        )
