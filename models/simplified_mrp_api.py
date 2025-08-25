# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class AqSimplifiedMrpApi(models.TransientModel):
    _name = 'aq.simplified.mrp.api'
    _description = 'API Simplificada de MRP (UI paso a paso)'

    # ---------- Helpers ----------
    @api.model
    def _find_bom(self, product):
        Bom = self.env['mrp.bom']
        bom = Bom.search([('product_id', '=', product.id)], limit=1)
        if not bom:
            bom = Bom.search([
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('product_id', '=', False)
            ], limit=1)
        return bom

    @api.model
    def _find_picking_type(self, warehouse):
        SPT = self.env['stock.picking.type']
        pt = SPT.search([
            ('code', 'in', ['mrp_operation', 'manufacture', 'mrp_manufacture']),
            ('warehouse_id', '=', warehouse.id)
        ], limit=1)
        if not pt:
            pt = SPT.search([('code', 'in', ['mrp_operation', 'manufacture', 'mrp_manufacture'])], limit=1)
        return pt

    # ---------- Data sources ----------
    @api.model
    def get_warehouses(self):
        try:
            ws = self.env['stock.warehouse'].search([])
            return [{'id': w.id, 'name': w.name} for w in ws]
        except Exception as e:
            _logger.error("Error getting warehouses: %s", str(e))
            raise UserError(_('Error obteniendo almacenes: %s') % str(e))

    @api.model
    def get_finished_products(self, query='', limit=20):
        try:
            dom = [('type', 'in', ['product', 'consu'])]
            if query and query.strip():
                q = query.strip()
                dom += ['|', ('name', 'ilike', q), ('default_code', 'ilike', q)]
            prods = self.env['product.product'].search(dom, limit=int(limit), order='name asc')
            return [{
                'id': p.id,
                'name': p.display_name,
                'uom_id': p.uom_id.id,
                'uom_name': p.uom_id.name,
            } for p in prods]
        except Exception as e:
            _logger.error("Error searching products: %s", str(e))
            raise UserError(_('Error buscando productos: %s') % str(e))

    @api.model
    def search_components(self, query='', limit=20):
        """Buscar productos para usar como ingredientes."""
        try:
            dom = [('type', 'in', ['product', 'consu'])]
            if query and query.strip():
                q = query.strip()
                dom += ['|', ('name', 'ilike', q), ('default_code', 'ilike', q)]
            prods = self.env['product.product'].search(dom, limit=int(limit), order='name asc')
            return [{
                'id': p.id,
                'name': p.display_name,
                'uom_id': p.uom_id.id,
                'uom_name': p.uom_id.name,
            } for p in prods]
        except Exception as e:
            _logger.error("Error searching components: %s", str(e))
            raise UserError(_('Error buscando ingredientes: %s') % str(e))

    @api.model
    def get_bom_components(self, product_id, qty=1.0):
        """Prefill desde BOM. El usuario puede editar luego."""
        try:
            product = self.env['product.product'].browse(int(product_id))
            if not product.exists():
                raise UserError(_('Producto no encontrado'))
            bom = self._find_bom(product)
            if not bom:
                return {'bom_id': False, 'components': []}

            base = bom.product_qty or 1.0
            comps = []
            for line in bom.bom_line_ids:
                req_qty = (line.product_qty * float(qty)) / base
                comps.append({
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'uom_id': line.product_uom_id.id or line.product_id.uom_id.id,
                    'uom_name': line.product_uom_id.name or line.product_id.uom_id.name,
                    'qty_required': req_qty,
                    'tracking': line.product_id.tracking,
                })
            return {'bom_id': bom.id, 'components': comps}
        except Exception as e:
            _logger.error("Error getting BOM components: %s", str(e))
            raise UserError(_('Error obteniendo componentes BOM: %s') % str(e))

    @api.model
    def get_lots(self, product_id, warehouse_id, limit=40):
        try:
            product = self.env['product.product'].browse(int(product_id))
            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            if not product.exists() or not wh.exists():
                return []
            Quant = self.env['stock.quant']
            domain = [
                ('product_id', '=', product.id),
                ('location_id', 'child_of', wh.lot_stock_id.id),
                ('quantity', '>', 0),
                ('lot_id', '!=', False),
            ]
            data = Quant.read_group(domain, ['quantity:sum'], ['lot_id'], limit=int(limit), orderby='lot_id asc')
            lots = []
            for row in data:
                if row.get('lot_id'):
                    lot = self.env['stock.production.lot'].browse(row['lot_id'][0])
                    if lot.exists():
                        lots.append({'id': lot.id, 'name': lot.name, 'qty_available': row.get('quantity_sum', 0)})
            return lots
        except Exception as e:
            _logger.error("Error getting lots: %s", str(e))
            raise UserError(_('Error obteniendo lotes: %s') % str(e))

    # ---------- Create MO ----------
    @api.model
    def create_mo(self, payload):
        """Crea OP con validación de ingredientes y override de cantidades."""
        try:
            warehouse_id = payload.get('warehouse_id')
            product_id = payload.get('product_id')
            product_qty = payload.get('product_qty', 1.0)
            bom_id = payload.get('bom_id')
            components_map = payload.get('components') or []

            if not warehouse_id or not product_id:
                raise UserError(_('Faltan datos obligatorios'))

            # Debe haber al menos un ingrediente con cantidad > 0
            comps_clean = []
            for c in components_map:
                if not c:
                    continue
                pid = int(c.get('product_id')) if c.get('product_id') else False
                qty = float(c.get('qty', c.get('qty_required', 0.0) or 0.0))
                lot = int(c.get('lot_id')) if c.get('lot_id') else False
                if pid and qty > 0:
                    comps_clean.append({'product_id': pid, 'qty': qty, 'lot_id': lot})
            if not comps_clean:
                raise UserError(_('Debes capturar al menos un ingrediente con cantidad mayor a cero.'))

            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            product = self.env['product.product'].browse(int(product_id))
            qty = float(product_qty)
            if not wh.exists():
                raise UserError(_('Almacén inválido'))
            if not product.exists():
                raise UserError(_('Producto inválido'))

            pt = self._find_picking_type(wh)
            if not pt:
                raise UserError(_('No hay tipo de operación de fabricación configurado'))

            if not bom_id:
                bom = self._find_bom(product)
                bom_id = bom.id if bom else False

            Production = self.env['mrp.production']
            mo_vals = {
                'product_id': product.id,
                'product_qty': qty,
                'product_uom_id': product.uom_id.id,
                'bom_id': bom_id or False,
                'picking_type_id': pt.id,
                'origin': 'Simplified UI',
            }
            mo = Production.create(mo_vals)
            mo.action_confirm()  # genera movimientos si hay BOM

            # Index existentes
            existing_by_pid = {m.product_id.id: m for m in mo.move_raw_ids}

            # Actualizar o crear movimientos según componentes capturados
            for item in comps_clean:
                pid = item['product_id']
                qty_req = item['qty']
                prod = self.env['product.product'].browse(pid)
                if pid in existing_by_pid:
                    move = existing_by_pid[pid]
                    move.product_uom_qty = qty_req
                else:
                    self.env['stock.move'].create({
                        'name': prod.display_name,
                        'product_id': pid,
                        'product_uom_qty': qty_req,
                        'product_uom': prod.uom_id.id,
                        'raw_material_production_id': mo.id,
                        'company_id': mo.company_id.id,
                        'location_id': mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                    })

            # Reasignar después de ajustes
            try:
                mo.action_assign()
            except Exception as assign_error:
                _logger.warning("Could not assign stock automatically: %s", str(assign_error))

            # Lotes opcionales
            lot_by_pid = {i['product_id']: i.get('lot_id') for i in comps_clean if i.get('lot_id')}
            if lot_by_pid:
                for move in mo.move_raw_ids:
                    pid = move.product_id.id
                    lot_id = lot_by_pid.get(pid)
                    if not lot_id:
                        continue
                    if move.move_line_ids:
                        # actualizar líneas existentes
                        for ml in move.move_line_ids:
                            ml.lot_id = lot_id
                    else:
                        # crear línea si no existe
                        self.env['stock.move.line'].create({
                            'move_id': move.id,
                            'company_id': move.company_id.id,
                            'product_id': pid,
                            'lot_id': lot_id,
                            'location_id': move.location_id.id,
                            'location_dest_id': move.location_dest_id.id,
                            'product_uom_id': move.product_uom.id,
                            'qty_done': 0.0,
                        })

            return {'mo_id': mo.id, 'name': mo.name}

        except Exception as e:
            _logger.error("Error creating MO: %s", str(e))
            raise UserError(_('Error creando orden de producción: %s') % str(e))
