# -*- coding: utf-8 -*-
{
    'name': 'AQ Simplified MRP',
    'version': '18.0.1.0.0',
    'summary': 'UI paso a paso para crear Órdenes de Producción',
    'category': 'Manufacturing',
    'author': 'alphaqueb.com',
    'license': 'LGPL-3',
    'depends': ['mrp', 'stock', 'web'],
    'assets': {
        'web.assets_backend': [
            'aq_simplified_mrp/static/src/js/simplified_mrp_client_action.js',
            'aq_simplified_mrp/static/src/scss/simplified_mrp.scss',
            'aq_simplified_mrp/static/src/xml/simplified_mrp_templates.xml',
        ],
    },
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/client_action.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
}