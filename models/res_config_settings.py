# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    simplified_mrp_auto_lot = fields.Boolean(
        string='Generar lote del producto terminado automaticamente',
        help=(
            'Activo: el sistema genera el lote con patron automatico.\n'
            'Desactivado: el operador captura los segmentos del lote manualmente.'
        ),
        config_parameter='aq_simplified_mrp.auto_lot',
        default=False,
    )

    # Poka-Yoke tolerance thresholds (percentage)
    smrp_tolerance_green = fields.Float(
        string='Tolerancia verde (%)',
        help='Desviacion maxima considerada OK.',
        config_parameter='aq_simplified_mrp.tolerance_green',
        default=2.0,
    )
    smrp_tolerance_yellow = fields.Float(
        string='Tolerancia amarilla (%)',
        help='Desviacion maxima considerada leve.',
        config_parameter='aq_simplified_mrp.tolerance_yellow',
        default=10.0,
    )
    smrp_tolerance_orange = fields.Float(
        string='Tolerancia naranja (%)',
        help='Desviacion maxima considerada importante.',
        config_parameter='aq_simplified_mrp.tolerance_orange',
        default=25.0,
    )
    smrp_allow_confirm_red = fields.Boolean(
        string='Permitir confirmar con alertas rojas',
        help='Si se activa, el usuario puede confirmar produccion con desviaciones criticas.',
        config_parameter='aq_simplified_mrp.allow_confirm_red',
        default=True,
    )
    smrp_auto_create_bom = fields.Boolean(
        string='Crear BOM automaticamente si no existe',
        help='Si se activa, el sistema crea la lista de materiales al confirmar produccion.',
        config_parameter='aq_simplified_mrp.auto_create_bom',
        default=True,
    )
    smrp_autosave = fields.Boolean(
        string='Autoguardado activo',
        help='Guardar borrador automaticamente al cambiar de paso.',
        config_parameter='aq_simplified_mrp.autosave',
        default=True,
    )