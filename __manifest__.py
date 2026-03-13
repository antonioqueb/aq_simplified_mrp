# -*- coding: utf-8 -*-
{
    'name': 'AQ Simplified MRP',
    'version': '18.0.2.0.0',
    'summary': 'UI paso a paso para crear Ordenes de Produccion con Poka-Yoke, BOM, subproductos y persistencia',
    'category': 'Manufacturing',
    'author': 'Alphaqueb Consulting SAS',
    'license': 'LGPL-3',
    'depends': ['mrp', 'stock', 'product', 'web'],
    'assets': {
        'web.assets_backend': [
            'aq_simplified_mrp/static/src/scss/simplified_mrp.scss',
            'aq_simplified_mrp/static/src/js/simplified_mrp_client_action.js',
            'aq_simplified_mrp/static/src/xml/simplified_mrp_templates.xml',
        ],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_view.xml',
        'views/client_action.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
}