# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    simplified_mrp_auto_lot = fields.Boolean(
        string='Generar lote del producto terminado automáticamente',
        help=(
            'Activo: el sistema genera el lote con el patrón XX-##-##-##-## de forma automática.\n'
            'Desactivado: el operador captura los segmentos del lote manualmente al crear la orden.'
        ),
        config_parameter='aq_simplified_mrp.auto_lot',
    )