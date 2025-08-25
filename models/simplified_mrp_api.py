# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

DEBUG_SMRP = True  # pon False para silenciar toasts

class AqSimplifiedMrpApi(models.TransientModel):
    _name = 'aq.simplified.mrp.api'
    _description = 'API Simplificada de MRP (UI paso a paso)'

    # ---------- Debug helpers ----------
    def _toast(self, msg, title='MRP Debug', level='info', sticky=False):
        if not DEBUG_SMRP:
            return
        try:
            if level == 'success':
                self.env.user.notify_success(message=msg, title=title, sticky=sticky)
            elif level == 'warning':
                self.env.user.notify_warning(message=msg, title=title, sticky=sticky)
            else:
                self.env.user.notify_info(message=msg, title=title, sticky=sticky)
        except Exception as e:
            _logger.debug("Toast failed: %s", e)

    def _logdbg(self, *parts):
        txt = " | ".join(str(p) for p in parts)
        _logger.warning("SMRPDBG: %s", txt)

    @staticmethod
    def _grp_sum(d, base):
        """Soporta claves 'quantity_sum' y 'quantity' indistintamente."""
        return (d.get(f'{base}_sum')
                if d.get(f'{base}_sum') is not None
                else (d.get(base) or 0.0))

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
            return [{'id': w.id, 'name': w.name, 'code': w.code} for w in ws]
        except Exception as e:
            _logger.error("Error getting warehouses: %s", e)
            raise UserError(_('Error obteniendo almacenes: %s') % e)

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
            _logger.error("Error searching products: %s", e)
            raise UserError(_('Error buscando productos: %s') % e)

    @api.model
    def search_components(self, query='', limit=20):
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
            _logger.error("Error searching components: %s", e)
            raise UserError(_('Error buscando ingredientes: %s') % e)

    @api.model
    def get_bom_components(self, product_id, qty=1.0):
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
            _logger.error("Error getting BOM components: %s", e)
            raise UserError(_('Error obteniendo componentes BOM: %s') % e)

    @api.model
    def get_lots(self, product_id, warehouse_id, limit=40):
        """Disponibilidad por lote bajo TODAS las ubicaciones internas del almacén:
           sum(quantity) - sum(reserved_quantity). Con trazas de depuración robustas."""
        try:
            product = self.env['product.product'].browse(int(product_id))
            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            if not product.exists() or not wh.exists():
                self._logdbg("get_lots", "producto/almacén inexistente", product_id, warehouse_id)
                self._toast(_("DBG: producto/almacén inexistente"), level='warning', sticky=True)
                return []

            view_loc = wh.view_location_id
            if not view_loc:
                self._logdbg("get_lots", "warehouse sin view_location_id", wh.id, wh.name)
                self._toast(_("DBG: almacén sin ubicación raíz"), level='warning', sticky=True)
                return []

            Quant = self.env['stock.quant'].sudo()
            domain = [
                ('product_id', '=', product.id),
                ('location_id', 'child_of', view_loc.id),
                ('location_id.usage', '=', 'internal'),
                ('lot_id', '!=', False),
                ('quantity', '>', 0),
            ]

            # Muestreo de quants previos al group-by
            sample_quants = Quant.search(domain, limit=10, order='quantity desc')
            for q in sample_quants:
                self._logdbg(
                    "quant", f"prod={q.product_id.display_name}",
                    f"lot={q.lot_id.display_name or '-'}",
                    f"qty={q.quantity}", f"res={q.reserved_quantity}",
                    f"loc={q.location_id.complete_name}({q.location_id.usage})"
                )

            groups = Quant.read_group(
                domain,
                ['quantity:sum', 'reserved_quantity:sum'],
                ['lot_id'],
                limit=int(limit),
            )
            self._logdbg("read_group result count", len(groups))
            for g in groups:
                self._logdbg("group keys", list(g.keys()))

            Lot = self.env['stock.lot'].sudo()
            out, total_qty = [], 0.0
            for g in groups:
                lot_id = g.get('lot_id') and g['lot_id'][0]
                qsum = self._grp_sum(g, 'quantity')
                rsum = self._grp_sum(g, 'reserved_quantity')
                qty = (qsum or 0.0) - (rsum or 0.0)
                self._logdbg("lot group", g.get('lot_id') and g['lot_id'][1], "qsum=", qsum, "rsum=", rsum, "avail=", qty)
                if lot_id and qty > 0:
                    lot = Lot.browse(lot_id)
                    if lot.exists():
                        out.append({'id': lot.id, 'name': lot.display_name, 'qty_available': qty})
                        total_qty += qty

            # Fallback si el producto no lleva tracking y el stock quedó sin lote
            if not out and product.tracking == 'none':
                g2 = Quant.read_group(
                    [
                        ('product_id', '=', product.id),
                        ('location_id', 'child_of', view_loc.id),
                        ('location_id.usage', '=', 'internal'),
                        ('lot_id', '=', False),
                        ('quantity', '>', 0),
                    ],
                    ['quantity:sum', 'reserved_quantity:sum'],
                    [],
                )
                qsum2 = g2 and self._grp_sum(g2[0], 'quantity') or 0.0
                rsum2 = g2 and self._grp_sum(g2[0], 'reserved_quantity') or 0.0
                qty2 = (qsum2 or 0.0) - (rsum2 or 0.0)
                self._logdbg("fallback no-tracking", "qsum=", qsum2, "rsum=", rsum2, "avail=", qty2)
                if qty2 > 0:
                    out = [{'id': False, 'name': _('Sin lote'), 'qty_available': qty2}]
                    total_qty = qty2

            # Diagnóstico: stock en otras ubicaciones internas
            if not out:
                others = Quant.read_group(
                    [
                        ('product_id', '=', product.id),
                        ('location_id.usage', '=', 'internal'),
                        ('quantity', '>', 0),
                    ],
                    ['location_id', 'quantity:sum'],
                    ['location_id'],
                    limit=5
                )
                pieces = []
                for x in others:
                    loc = x.get('location_id') and x['location_id'][0]
                    if not loc:
                        continue
                    qty_here = self._grp_sum(x, 'quantity')
                    pieces.append(f"{self.env['stock.location'].browse(loc).complete_name}: {qty_here}")
                self._logdbg("stock en otras ubicaciones", ", ".join(pieces) or "sin datos")

            out.sort(key=lambda x: x['name'])

            self._toast(
                _("DBG Lotes » Prod: %(p)s [%(trk)s] | WH: %(w)s | child_of=%(loc)s | grupos=%(g)d | devueltos=%(r)d | total=%(t).2f",
                  p=product.display_name, trk=product.tracking, w=wh.code or wh.name,
                  loc=view_loc.complete_name, g=len(groups), r=len(out), t=total_qty),
                level='info', sticky=True
            )
            return out[:int(limit)]
        except Exception as e:
            _logger.error("Error getting lots: %s", e)
            self._toast(_("DBG Error obteniendo lotes: %s") % e, level='warning', sticky=True)
            raise UserError(_('Error obteniendo lotes: %s') % e)

    # ---------- Create MO ----------
    @api.model
    def create_mo(self, payload):
        try:
            warehouse_id = payload.get('warehouse_id')
            product_id = payload.get('product_id')
            product_qty = payload.get('product_qty', 1.0)
            bom_id = payload.get('bom_id')
            components_map = payload.get('components') or []

            # Normaliza componentes > 0
            comps_clean = []
            for c in components_map:
                if not c:
                    continue
                pid = int(c.get('product_id')) if c.get('product_id') else False
                qty = float(c.get('qty', c.get('qty_required', 0.0) or 0.0))
                lot = int(c.get('lot_id')) if c.get('lot_id') else False
                if pid and qty > 0:
                    comps_clean.append({'product_id': pid, 'qty': qty, 'lot_id': lot})

            self._logdbg("create_mo payload", f"wh={warehouse_id}", f"prod={product_id}", f"qty={product_qty}",
                         f"bom={bom_id}", f"comps={len(comps_clean)}")
            if not warehouse_id or not product_id:
                raise UserError(_('Faltan datos obligatorios'))
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
            self._logdbg("MO creado", mo.id, mo.name)

            mo.action_confirm()
            self._logdbg("MO confirmado", "moves:", len(mo.move_raw_ids))

            existing_by_pid = {m.product_id.id: m for m in mo.move_raw_ids}

            for item in comps_clean:
                pid = item['product_id']
                qty_req = item['qty']
                prod = self.env['product.product'].browse(pid)
                move = existing_by_pid.get(pid)
                if move:
                    self._logdbg("upd move", move.id, prod.display_name, "qty", qty_req)
                    move.product_uom_qty = qty_req
                else:
                    move = self.env['stock.move'].create({
                        'name': prod.display_name,
                        'product_id': pid,
                        'product_uom_qty': qty_req,
                        'product_uom': prod.uom_id.id,
                        'raw_material_production_id': mo.id,
                        'company_id': mo.company_id.id,
                        'location_id': mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                    })
                    existing_by_pid[pid] = move
                    self._logdbg("new move", move.id, prod.display_name, "qty", qty_req)

            try:
                mo.action_assign()
                self._logdbg("MO asignado", "moves:", [(m.id, float(m.reserved_availability)) for m in mo.move_raw_ids])
            except Exception as assign_error:
                self._logdbg("assign error", assign_error)
                _logger.warning("Could not assign stock automatically: %s", assign_error)

            lot_by_pid = {i['product_id']: i.get('lot_id') for i in comps_clean if i.get('lot_id')}
            qty_by_pid = {i['product_id']: i['qty'] for i in comps_clean}
            for pid, move in existing_by_pid.items():
                lot_id = lot_by_pid.get(pid)
                qty_req = qty_by_pid.get(pid, 0.0)
                if not lot_id and qty_req <= 0:
                    continue
                ml = move.move_line_ids[:1]
                if ml:
                    rec = ml[0]
                    if lot_id:
                        rec.lot_id = lot_id
                    rec.quantity = qty_req  # Odoo 18
                    self._logdbg("upd mline", rec.id, "move", move.id, "lot", lot_id, "qty", qty_req)
                else:
                    new_ml = self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'company_id': move.company_id.id,
                        'product_id': move.product_id.id,
                        'product_uom_id': move.product_uom.id,
                        'location_id': move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                        'lot_id': lot_id or False,
                        'quantity': qty_req,  # Odoo 18
                    })
                    self._logdbg("new mline", new_ml.id, "move", move.id, "lot", lot_id, "qty", qty_req)

            self._toast(_("DBG MO creada: %(n)s | Líneas: %(c)d",
                          n=mo.name, c=len(mo.move_raw_ids)), level='success')

            return {'mo_id': mo.id, 'name': mo.name}

        except Exception as e:
            _logger.error("Error creating MO: %s", e)
            self._toast(_("DBG Error creando OP: %s") % e, level='warning', sticky=True)
            raise UserError(_('Error creando orden de producción: %s') % e)
