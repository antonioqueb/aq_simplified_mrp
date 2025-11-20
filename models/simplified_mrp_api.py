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
    def get_finished_products(self, query='', limit=20, **kwargs):
        try:
            _logger.debug("get_finished_products called with: query=%s, limit=%s, kwargs=%s", query, limit, kwargs)
            
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
    def search_components(self, query='', limit=20, **kwargs):
        try:
            _logger.debug("search_components called with: query=%s, limit=%s, kwargs=%s", query, limit, kwargs)
            
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
                'tracking': p.tracking,
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
        """Disponibilidad por lote bajo TODAS las ubicaciones internas del almacén."""
        try:
            product = self.env['product.product'].browse(int(product_id))
            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            
            if not product.exists() or not wh.exists():
                self._logdbg("get_lots", "producto/almacén inexistente", product_id, warehouse_id)
                return []

            view_loc = wh.view_location_id
            if not view_loc:
                self._logdbg("get_lots", "warehouse sin view_location_id", wh.id, wh.name)
                return []

            Quant = self.env['stock.quant'].sudo()
            
            internal_locs = self.env['stock.location'].search([
                ('location_id', 'child_of', view_loc.id),
                ('usage', '=', 'internal')
            ])
            
            domain = [
                ('product_id', '=', product.id),
                ('location_id', 'in', internal_locs.ids),
                ('quantity', '>', 0),
            ]
            
            total_stock = Quant.search_count(domain)
            self._logdbg("Stock total encontrado (con o sin lote):", total_stock)

            groups = Quant.read_group(
                domain,
                ['quantity:sum', 'reserved_quantity:sum'],
                ['lot_id'],
                limit=int(limit),
                lazy=True
            )
            
            self._logdbg("read_group result count", len(groups))

            Lot = self.env['stock.lot'].sudo()
            out, total_qty = [], 0.0
            
            for g in groups:
                lot_data = g.get('lot_id')
                lot_id = lot_data[0] if lot_data else False
                
                qsum = self._grp_sum(g, 'quantity')
                rsum = self._grp_sum(g, 'reserved_quantity')
                qty = (qsum or 0.0) - (rsum or 0.0)
                
                self._logdbg("lot group", lot_data, "qsum=", qsum, "rsum=", rsum, "avail=", qty)
                
                if qty > 0:
                    if lot_id:
                        out.append({'id': lot_id, 'name': lot_data[1], 'qty_available': qty})
                    else:
                        out.append({'id': False, 'name': _('Sin lote / General'), 'qty_available': qty})
                    
                    total_qty += qty

            out.sort(key=lambda x: x['name'] or 'ZZZZ')

            self._toast(
                _("Lotes » Prod: %(p)s | WH: %(w)s | Encontrados: %(r)d | Total: %(t).2f",
                  p=product.display_name, w=wh.code or wh.name, r=len(out), t=total_qty),
                level='info', sticky=True
            )
            return out[:int(limit)]

        except Exception as e:
            _logger.error("Error getting lots: %s", e, exc_info=True)
            self._toast(_("Error obteniendo lotes: %s") % e, level='warning', sticky=True)
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
            mo.user_id = self.env.uid
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
                    rec.quantity = qty_req
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
                        'quantity': qty_req,
                    })
                    self._logdbg("new mline", new_ml.id, "move", move.id, "lot", lot_id, "qty", qty_req)

            # ============== AUTOMATIZACIÓN COMPLETA DE LA MO ==============
            
            # 1. Crear/actualizar el move line para el producto terminado
            finished_lot = None
            if product.tracking in ['lot', 'serial']:
                Lot = self.env['stock.lot']
                
                # Generar nombre del lote: FECHA-REF-CONSECUTIVO
                from datetime import datetime
                date_str = datetime.now().strftime('%Y%m%d')
                ref = product.default_code or 'PROD'
                
                # Buscar el último consecutivo del día para este producto
                existing_lots = Lot.search([
                    ('product_id', '=', product.id),
                    ('company_id', '=', mo.company_id.id),
                    ('name', 'like', f"{date_str}-{ref}-%")
                ], order='name desc', limit=1)
                
                if existing_lots:
                    # Extraer el consecutivo del último lote
                    last_name = existing_lots[0].name
                    try:
                        last_consecutive = int(last_name.split('-')[-1])
                        consecutive = last_consecutive + 1
                    except (ValueError, IndexError):
                        consecutive = 1
                else:
                    consecutive = 1
                
                lot_name = f"{date_str}-{ref}-{consecutive:03d}"
                
                finished_lot = Lot.create({
                    'name': lot_name,
                    'product_id': product.id,
                    'company_id': mo.company_id.id,
                })
                self._logdbg("Lote creado para producto terminado", finished_lot.id, finished_lot.name)
            
            # Verificar si ya existe move_line EN EL MOVE FINISHED, si sí actualizarlo, si no crearlo
            if mo.move_finished_ids:
                finished_move = mo.move_finished_ids[0]
                self._logdbg("Move finished encontrado", finished_move.id, finished_move.product_id.display_name)
                
                if finished_move.move_line_ids:
                    # Ya existe move_line, actualizarlo
                    for ml in finished_move.move_line_ids:
                        if finished_lot:
                            ml.lot_id = finished_lot.id
                        ml.quantity = qty
                        self._logdbg("Move line FINISHED actualizado", ml.id, "move", finished_move.id, "lot", finished_lot.id if finished_lot else None, "qty", qty)
                else:
                    # No existe move_line, crearlo
                    finished_move_line = self.env['stock.move.line'].create({
                        'move_id': finished_move.id,
                        'company_id': mo.company_id.id,
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id': mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                        'lot_id': finished_lot.id if finished_lot else False,
                        'quantity': qty,
                    })
                    self._logdbg("Move line FINISHED creado", finished_move_line.id, "move", finished_move.id, "lot", finished_lot.id if finished_lot else None, "qty", qty)

            # 2. Marcar como iniciado
            try:
                mo.action_toggle_is_locked()
                mo.is_locked = False
                mo.action_toggle_is_locked()
                self._logdbg("MO iniciado", mo.state)
            except Exception as start_error:
                self._logdbg("Error iniciando MO", start_error)
                _logger.warning("Could not start production automatically: %s", start_error)

            # 3. Completar automáticamente
            try:
                mo.button_mark_done()
                self._logdbg("MO marcada como hecha con button_mark_done()", mo.state)

            except Exception as complete_error:
                self._logdbg("Error completando MO con button_mark_done", complete_error)
                _logger.warning("Could not complete production with button_mark_done: %s", complete_error)
                
                try:
                    for move in mo.move_raw_ids:
                        if move.state not in ['done', 'cancel']:
                            move._action_done()
                            self._logdbg("Move raw validado", move.id, move.product_id.display_name)
                    
                    for move in mo.move_finished_ids:
                        if move.state not in ['done', 'cancel']:
                            move._action_done()
                            self._logdbg("Move finished validado", move.id, move.product_id.display_name)

                    if mo.state != 'done':
                        mo.action_done()
                        self._logdbg("MO marcada como hecha con action_done()", mo.state)

                except Exception as fallback_error:
                    self._logdbg("Error en fallback", fallback_error)
                    _logger.warning("Could not complete production with fallback method: %s", fallback_error)
                    try:
                        if mo.state == 'confirmed':
                            mo.action_toggle_is_locked()
                            self._logdbg("MO al menos iniciada", mo.state)
                    except Exception:
                        pass

            self._toast(_("MO creada y completada: %(n)s | Estado: %(s)s | Líneas: %(c)d",
                          n=mo.name, s=mo.state, c=len(mo.move_raw_ids)), level='success')

            return {'mo_id': mo.id, 'name': mo.name, 'state': mo.state}

        except Exception as e:
            _logger.error("Error creating MO: %s", e, exc_info=True)
            self._toast(_("Error creando OP: %s") % e, level='warning', sticky=True)
            raise UserError(_('Error creando orden de producción: %s') % e)

    # ---------- List & Detail ----------
    @api.model
    def get_my_productions(self, limit=50):
        """Lista de OPs del usuario actual."""
        try:
            Production = self.env['mrp.production']
            domain = [('user_id', '=', self.env.uid)]
            
            mos = Production.search(domain, limit=int(limit), order='date_start desc, id desc')
            
            result = []
            for mo in mos:
                result.append({
                    'id': mo.id,
                    'name': mo.name,
                    'state': mo.state,
                    'product_id': mo.product_id.id,
                    'product_name': mo.product_id.display_name,
                    'product_qty': mo.product_qty,
                    'uom_name': mo.product_uom_id.name,
                    'date_start': mo.date_start.isoformat() if mo.date_start else False,
                    'date_finished': mo.date_finished.isoformat() if mo.date_finished else False,
                })
            
            return result
        except Exception as e:
            _logger.error("Error getting my productions: %s", e, exc_info=True)
            raise UserError(_('Error obteniendo mis órdenes: %s') % e)

    @api.model
    def get_production_detail(self, mo_id):
        """Detalle simplificado de una OP."""
        try:
            mo = self.env['mrp.production'].browse(int(mo_id))
            if not mo.exists():
                raise UserError(_('Orden no encontrada'))
            
            if mo.user_id.id != self.env.uid:
                raise UserError(_('No tienes permiso para ver esta orden'))
            
            components = []
            for move in mo.move_raw_ids:
                lot_name = False
                qty_done = 0.0
                if move.move_line_ids:
                    lot_name = move.move_line_ids[0].lot_id.display_name if move.move_line_ids[0].lot_id else False
                    qty_done = sum(ml.quantity for ml in move.move_line_ids)
                
                components.append({
                    'product_name': move.product_id.display_name,
                    'qty_required': move.product_uom_qty,
                    'qty_done': qty_done,
                    'uom_name': move.product_uom.name,
                    'lot_name': lot_name or 'Sin lote',
                })
            
            finished_lot = False
            if mo.move_finished_ids and mo.move_finished_ids[0].move_line_ids:
                finished_lot = mo.move_finished_ids[0].move_line_ids[0].lot_id.display_name or False
            
            return {
                'name': mo.name,
                'state': mo.state,
                'product_name': mo.product_id.display_name,
                'product_qty': mo.product_qty,
                'uom_name': mo.product_uom_id.name,
                'date_start': mo.date_start.isoformat() if mo.date_start else False,
                'date_finished': mo.date_finished.isoformat() if mo.date_finished else False,
                'finished_lot': finished_lot or 'Sin lote',
                'components': components,
            }
        except Exception as e:
            _logger.error("Error getting production detail: %s", e, exc_info=True)
            raise UserError(_('Error obteniendo detalle: %s') % e)