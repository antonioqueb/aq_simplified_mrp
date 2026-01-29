# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
from datetime import datetime

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
    def get_sale_orders(self, query='', limit=20):
        """Busca órdenes de venta confirmadas para origen."""
        try:
            domain = [('state', 'in', ['sale', 'done'])]
            if query:
                domain += [('name', 'ilike', query)]
            
            sos = self.env['sale.order'].search(domain, limit=int(limit), order='date_order desc, id desc')
            return [{'id': s.id, 'name': s.name} for s in sos]
        except Exception as e:
            _logger.error("Error getting sale orders: %s", e)
            return []

    @api.model
    def get_stock_locations(self, warehouse_id):
        """Obtiene ubicaciones internas para destino personalizable."""
        try:
            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            if not wh.exists():
                return []
            
            locs = self.env['stock.location'].search([
                ('usage', '=', 'internal'),
                ('location_id', 'child_of', wh.view_location_id.id)
            ], order='name asc')
            
            return [{'id': l.id, 'name': l.display_name} for l in locs]
        except Exception as e:
            _logger.error("Error getting locations: %s", e)
            return []

    @api.model
    def get_finished_products(self, query='', limit=20, **kwargs):
        try:
            _logger.debug("get_finished_products called with: query=%s", query)
            
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
    def get_lots(self, product_id, warehouse_id, limit=60):
        """Disponibilidad por lote bajo TODAS las ubicaciones internas del almacén."""
        try:
            product = self.env['product.product'].browse(int(product_id))
            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            
            if not product.exists() or not wh.exists():
                return []

            view_loc = wh.view_location_id
            if not view_loc:
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
            
            groups = Quant.read_group(
                domain,
                ['quantity:sum', 'reserved_quantity:sum'],
                ['lot_id'],
                limit=int(limit),
                lazy=True
            )
            
            out = []
            for g in groups:
                lot_data = g.get('lot_id')
                lot_id = lot_data[0] if lot_data else False
                
                qsum = self._grp_sum(g, 'quantity')
                rsum = self._grp_sum(g, 'reserved_quantity')
                qty = (qsum or 0.0) - (rsum or 0.0)
                
                if qty > 0:
                    name = lot_data[1] if lot_data else _('Sin lote / General')
                    # Usamos -1 para identificar 'Sin lote' en el frontend si es necesario
                    out.append({'id': lot_id or -1, 'name': name, 'qty_available': qty})

            out.sort(key=lambda x: x['name'] or 'ZZZZ')
            return out

        except Exception as e:
            _logger.error("Error getting lots: %s", e, exc_info=True)
            raise UserError(_('Error obteniendo lotes: %s') % e)

    # ---------- Create MO ----------
    @api.model
    def create_mo(self, payload):
        """
        Crea la MO soportando:
        - Múltiples lotes por ingrediente (payload['components'][x]['selected_lots'])
        - Origen desde Ventas (payload['origin'])
        - Destino personalizado (payload['location_dest_id'])
        """
        try:
            warehouse_id = payload.get('warehouse_id')
            product_id = payload.get('product_id')
            product_qty = payload.get('product_qty', 1.0)
            bom_id = payload.get('bom_id')
            components_map = payload.get('components') or []
            
            origin_ref = payload.get('origin') or 'Simplified UI'
            custom_dest_loc_id = payload.get('location_dest_id')

            # Limpiar y validar componentes
            comps_clean = []
            for c in components_map:
                if not c:
                    continue
                pid = int(c.get('product_id')) if c.get('product_id') else False
                total_qty = float(c.get('qty', 0.0))
                
                # 'selected_lots' debe ser una lista de {lot_id: int, qty: float}
                lots_data = c.get('selected_lots', [])
                
                if pid and total_qty > 0:
                    comps_clean.append({
                        'product_id': pid,
                        'qty': total_qty,
                        'lots': lots_data 
                    })

            self._logdbg("create_mo payload", f"wh={warehouse_id}", f"prod={product_id}", f"qty={product_qty}")

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
                'origin': origin_ref,
            }

            # Si el usuario seleccionó un destino específico (diferente al default)
            if custom_dest_loc_id:
                mo_vals['location_dest_id'] = int(custom_dest_loc_id)

            mo = Production.create(mo_vals)
            self._logdbg("MO creado", mo.id, mo.name)

            # ============== LOTE PRODUCTO TERMINADO ==============
            finished_lot = None
            if product.tracking in ['lot', 'serial']:
                Lot = self.env['stock.lot']
                date_str = datetime.now().strftime('%Y%m%d')
                ref = product.default_code or 'PROD'
                
                existing_lots = Lot.search([
                    ('product_id', '=', product.id),
                    ('company_id', '=', mo.company_id.id),
                    ('name', 'like', f"{date_str}-{ref}-%")
                ], order='name desc', limit=1)
                
                consecutive = 1
                if existing_lots:
                    try:
                        last_name = existing_lots[0].name
                        last_consecutive = int(last_name.split('-')[-1])
                        consecutive = last_consecutive + 1
                    except:
                        pass
                
                lot_name = f"{date_str}-{ref}-{consecutive:03d}"
                finished_lot = Lot.create({
                    'name': lot_name,
                    'product_id': product.id,
                    'company_id': mo.company_id.id,
                })
                mo.lot_producing_id = finished_lot.id

            mo.action_confirm()
            mo.user_id = self.env.uid

            # ============== ASIGNACIÓN DE COMPONENTES Y MÚLTIPLES LOTES ==============
            existing_by_pid = {m.product_id.id: m for m in mo.move_raw_ids}

            for item in comps_clean:
                pid = item['product_id']
                req_qty_total = item['qty']
                lots_distribution = item['lots'] # Lista de {lot_id, qty}
                
                prod = self.env['product.product'].browse(pid)
                move = existing_by_pid.get(pid)
                
                # 1. Ajustar la cantidad total del movimiento
                if move:
                    move.product_uom_qty = req_qty_total
                else:
                    move = self.env['stock.move'].create({
                        'name': prod.display_name,
                        'product_id': pid,
                        'product_uom_qty': req_qty_total,
                        'product_uom': prod.uom_id.id,
                        'raw_material_production_id': mo.id,
                        'company_id': mo.company_id.id,
                        'location_id': mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                    })
                    existing_by_pid[pid] = move

                # 2. Crear las líneas de movimiento (Stock Move Lines) para cada lote
                # Primero borramos cualquier asignación automática para tener control total
                if move.move_line_ids:
                    move.move_line_ids.unlink()

                if not lots_distribution:
                    # Si no se seleccionaron lotes (consumo genérico o producto sin tracking)
                    self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'product_id': pid,
                        'product_uom_id': prod.uom_id.id,
                        'location_id': move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                        'quantity': req_qty_total,
                    })
                else:
                    # Crear una línea por cada lote con su cantidad específica
                    for l_data in lots_distribution:
                        l_id = l_data.get('lot_id')
                        l_qty = float(l_data.get('qty', 0.0))
                        
                        if l_qty <= 0:
                            continue
                        
                        # Si l_id es -1 (flag de UI) o False, es sin lote
                        real_lot_id = l_id if (l_id and l_id != -1) else False

                        self.env['stock.move.line'].create({
                            'move_id': move.id,
                            'product_id': pid,
                            'product_uom_id': prod.uom_id.id,
                            'location_id': move.location_id.id,
                            'location_dest_id': move.location_dest_id.id,
                            'lot_id': real_lot_id,
                            'quantity': l_qty,
                        })

            try:
                mo.action_assign()
            except Exception as assign_error:
                _logger.warning("Auto assign warning: %s", assign_error)

            # ============== COMPLETAR MO ==============
            # Verificar el movimiento del producto terminado y asignar su lote
            if mo.move_finished_ids:
                finished_move = mo.move_finished_ids[0]
                if finished_move.move_line_ids:
                    for ml in finished_move.move_line_ids:
                        if finished_lot:
                            ml.lot_id = finished_lot.id
                        ml.quantity = qty
                else:
                    self.env['stock.move.line'].create({
                        'move_id': finished_move.id,
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id': mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                        'lot_id': finished_lot.id if finished_lot else False,
                        'quantity': qty,
                    })

            try:
                # Flujo estándar de cierre
                mo.action_toggle_is_locked() # Desbloquear para ediciones
                mo.is_locked = False
                mo.button_mark_done()
                self._logdbg("MO marcada como hecha con button_mark_done()", mo.state)

            except Exception as complete_error:
                self._logdbg("Error completando MO, usando fallback", complete_error)
                # Fallback manual
                try:
                    for move in mo.move_raw_ids:
                        if move.state not in ['done', 'cancel']:
                            move._action_done()
                    for move in mo.move_finished_ids:
                        if move.state not in ['done', 'cancel']:
                            move._action_done()
                    if mo.state != 'done':
                        mo.action_done()
                except Exception as fb_err:
                     _logger.error("Fallback failed: %s", fb_err)

            self._toast(_("MO creada: %(n)s", n=mo.name), level='success')
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
        """Detalle simplificado de una OP con lotes múltiples."""
        try:
            mo = self.env['mrp.production'].browse(int(mo_id))
            if not mo.exists():
                raise UserError(_('Orden no encontrada'))
            
            if mo.user_id.id != self.env.uid:
                raise UserError(_('No tienes permiso para ver esta orden'))
            
            components = []
            for move in mo.move_raw_ids:
                # Recopilar información de lotes usados
                lots_info = []
                qty_done = 0.0
                if move.move_line_ids:
                    for ml in move.move_line_ids:
                        qty_done += ml.quantity
                        lname = ml.lot_id.name if ml.lot_id else 'General'
                        lots_info.append(f"{lname} ({ml.quantity})")
                
                lot_str = ", ".join(lots_info) if lots_info else "Sin consumo"

                components.append({
                    'product_name': move.product_id.display_name,
                    'qty_required': move.product_uom_qty,
                    'qty_done': qty_done,
                    'uom_name': move.product_uom.name,
                    'lot_name': lot_str,
                })
            
            finished_lot = False
            if mo.move_finished_ids and mo.move_finished_ids[0].move_line_ids:
                finished_lot = mo.move_finished_ids[0].move_line_ids[0].lot_id.display_name or False
            
            return {
                'name': mo.name,
                'state': mo.state,
                'origin': mo.origin,
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