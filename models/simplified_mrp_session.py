# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
import json
import logging

_logger = logging.getLogger(__name__)


class SimplifiedMrpSession(models.Model):
    _name = 'simplified.mrp.session'
    _description = 'Sesion de manufactura simplificada (borrador persistente)'
    _order = 'write_date desc'

    name = fields.Char(default='Borrador', required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.uid, required=True, index=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmado'),
        ('cancelled', 'Cancelado'),
    ], default='draft', required=True, index=True)

    warehouse_id = fields.Many2one('stock.warehouse')
    product_id = fields.Many2one('product.product')
    product_qty = fields.Float(default=1.0)
    bom_id = fields.Many2one('mrp.bom')
    origin = fields.Char()
    location_dest_id = fields.Many2one('stock.location')
    current_step = fields.Char(default='warehouse')

    # Lot segments
    lot_seg1 = fields.Char()
    lot_seg2 = fields.Char()
    lot_seg3 = fields.Char()
    lot_seg4 = fields.Char()
    lot_seg5 = fields.Char()

    # JSON blobs for complex data
    components_json = fields.Text(default='[]')
    byproducts_json = fields.Text(default='[]')
    assigned_lots_json = fields.Text(default='{}')
    sale_order_ref = fields.Char()

    production_id = fields.Many2one('mrp.production', readonly=True)

    @api.model
    def save_session(self, data):
        """Crea o actualiza la sesion borrador del usuario actual."""
        session = self.search([
            ('user_id', '=', self.env.uid),
            ('state', '=', 'draft'),
        ], limit=1, order='write_date desc')

        vals = {
            'warehouse_id': data.get('warehouse_id') or False,
            'product_id': data.get('product_id') or False,
            'product_qty': data.get('product_qty', 1.0),
            'bom_id': data.get('bom_id') or False,
            'origin': data.get('origin') or '',
            'location_dest_id': data.get('location_dest_id') or False,
            'current_step': data.get('current_step', 'warehouse'),
            'lot_seg1': data.get('lot_seg1') or '',
            'lot_seg2': data.get('lot_seg2') or '',
            'lot_seg3': data.get('lot_seg3') or '',
            'lot_seg4': data.get('lot_seg4') or '',
            'lot_seg5': data.get('lot_seg5') or '',
            'components_json': json.dumps(data.get('components', [])),
            'byproducts_json': json.dumps(data.get('byproducts', [])),
            'assigned_lots_json': json.dumps(data.get('assigned_lots', {})),
            'sale_order_ref': data.get('sale_order_ref') or '',
        }

        if session:
            session.write(vals)
        else:
            vals['user_id'] = self.env.uid
            vals['company_id'] = self.env.company.id
            session = self.create(vals)

        return {'session_id': session.id}

    @api.model
    def load_session(self):
        """Recupera la sesion borrador mas reciente del usuario."""
        session = self.search([
            ('user_id', '=', self.env.uid),
            ('state', '=', 'draft'),
        ], limit=1, order='write_date desc')

        if not session:
            return {'found': False}

        return {
            'found': True,
            'session_id': session.id,
            'warehouse_id': session.warehouse_id.id or False,
            'product_id': session.product_id.id or False,
            'product_name': session.product_id.display_name if session.product_id else '',
            'product_tracking': session.product_id.tracking if session.product_id else 'none',
            'uom_name': session.product_id.uom_id.name if session.product_id else '',
            'product_qty': session.product_qty,
            'bom_id': session.bom_id.id or False,
            'origin': session.origin or '',
            'location_dest_id': session.location_dest_id.id or False,
            'current_step': session.current_step or 'warehouse',
            'lot_seg1': session.lot_seg1 or '',
            'lot_seg2': session.lot_seg2 or '',
            'lot_seg3': session.lot_seg3 or '',
            'lot_seg4': session.lot_seg4 or '',
            'lot_seg5': session.lot_seg5 or '',
            'components': json.loads(session.components_json or '[]'),
            'byproducts': json.loads(session.byproducts_json or '[]'),
            'assigned_lots': json.loads(session.assigned_lots_json or '{}'),
            'sale_order_ref': session.sale_order_ref or '',
        }

    @api.model
    def discard_session(self):
        """Descarta la sesion borrador actual."""
        sessions = self.search([
            ('user_id', '=', self.env.uid),
            ('state', '=', 'draft'),
        ])
        sessions.write({'state': 'cancelled'})
        return True

    @api.model
    def mark_confirmed(self, production_id=False):
        """Marca sesion como confirmada tras crear la MO."""
        session = self.search([
            ('user_id', '=', self.env.uid),
            ('state', '=', 'draft'),
        ], limit=1, order='write_date desc')
        if session:
            session.write({
                'state': 'confirmed',
                'production_id': production_id or False,
            })
        return True