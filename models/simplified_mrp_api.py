# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
import logging
from datetime import datetime
import re

_logger = logging.getLogger(__name__)

LOT_PATTERN = re.compile(r'^[A-Za-z]{2}-\d{2}-\d{2}-\d{2}-\d{2}$')


class AqSimplifiedMrpApi(models.TransientModel):
    _name = 'aq.simplified.mrp.api'
    _description = 'API Simplificada de MRP con Poka-Yoke'

    # ─── Config ────────────────────────────────────────────────────────────
    @api.model
    def get_mrp_config(self):
        param = self.env['ir.config_parameter'].sudo()

        def _bool(key, default='False'):
            return str(param.get_param(key, default=default)).strip() in ('True', '1', 'true')

        def _float(key, default='0'):
            try:
                return float(param.get_param(key, default=default))
            except (ValueError, TypeError):
                return float(default)

        return {
            'auto_lot': _bool('aq_simplified_mrp.auto_lot'),
            'tolerance_green': _float('aq_simplified_mrp.tolerance_green', '2'),
            'tolerance_yellow': _float('aq_simplified_mrp.tolerance_yellow', '10'),
            'tolerance_orange': _float('aq_simplified_mrp.tolerance_orange', '25'),
            'allow_confirm_red': _bool('aq_simplified_mrp.allow_confirm_red', 'True'),
            'auto_create_bom': _bool('aq_simplified_mrp.auto_create_bom', 'True'),
            'autosave': _bool('aq_simplified_mrp.autosave', 'True'),
        }

    # ─── Helpers ───────────────────────────────────────────────────────────
    @api.model
    def _find_bom(self, product):
        Bom = self.env['mrp.bom']
        bom = Bom.search([('product_id', '=', product.id)], limit=1)
        if not bom:
            bom = Bom.search([
                ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ('product_id', '=', False),
            ], limit=1)
        return bom

    @api.model
    def _find_picking_type(self, warehouse):
        SPT = self.env['stock.picking.type']
        pt = SPT.search([
            ('code', 'in', ['mrp_operation', 'manufacture', 'mrp_manufacture']),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if not pt:
            pt = SPT.search([
                ('code', 'in', ['mrp_operation', 'manufacture', 'mrp_manufacture']),
            ], limit=1)
        return pt

    @api.model
    def _product_uom_category_ok(self, product, uom):
        return bool(product.uom_id and uom and product.uom_id.category_id.id == uom.category_id.id)

    @api.model
    def _validate_no_direct_cycle(self, finished_product, components, byproducts=None):
        finished_id = finished_product.id
        component_ids = {
            int(c.get('product_id', 0))
            for c in (components or [])
            if c.get('product_id')
        }
        byproduct_ids = {
            int(bp.get('product_id', 0))
            for bp in (byproducts or [])
            if bp.get('product_id')
        }
        if finished_id in component_ids:
            raise UserError(_(
                'Configuracion invalida: el producto terminado "%s" no puede estar incluido '
                'como ingrediente de su propia lista de materiales.'
            ) % finished_product.display_name)
        if finished_id in byproduct_ids:
            raise UserError(_(
                'Configuracion invalida: el producto terminado "%s" no puede estar incluido '
                'como subproducto de su propia lista de materiales.'
            ) % finished_product.display_name)

    @api.model
    def _validate_bom_component_data(self, components):
        cleaned = []
        for c in (components or []):
            pid = int(c.get('product_id', 0))
            cqty = float(c.get('qty', 0.0))
            if not pid or cqty <= 0:
                continue
            prod = self.env['product.product'].browse(pid)
            if not prod.exists():
                raise UserError(_('Ingrediente invalido: producto no encontrado (ID %s).') % pid)
            if not prod.uom_id:
                raise UserError(_('El ingrediente "%s" no tiene unidad de medida definida.') % prod.display_name)
            cleaned.append({
                'product': prod,
                'product_id': prod.id,
                'qty': cqty,
                'uom_id': prod.uom_id.id,
            })
        return cleaned

    @api.model
    def _validate_bom_byproduct_data(self, byproducts):
        cleaned = []
        for bp in (byproducts or []):
            bp_pid = int(bp.get('product_id', 0))
            bp_qty = float(bp.get('qty', 0.0))
            if not bp_pid or bp_qty <= 0:
                continue
            bp_prod = self.env['product.product'].browse(bp_pid)
            if not bp_prod.exists():
                raise UserError(_('Subproducto invalido: producto no encontrado (ID %s).') % bp_pid)
            if not bp_prod.uom_id:
                raise UserError(_('El subproducto "%s" no tiene unidad de medida definida.') % bp_prod.display_name)
            cleaned.append({
                'product': bp_prod,
                'product_id': bp_prod.id,
                'qty': bp_qty,
                'uom_id': bp_prod.uom_id.id,
            })
        return cleaned

    # ─── Data sources ──────────────────────────────────────────────────────
    @api.model
    def get_warehouses(self):
        ws = self.env['stock.warehouse'].search([])
        return [{'id': w.id, 'name': w.name, 'code': w.code} for w in ws]

    @api.model
    def get_sale_orders(self, query='', limit=20):
        domain = [('state', 'in', ['sale', 'done'])]
        if query:
            domain += [('name', 'ilike', query)]
        sos = self.env['sale.order'].search(domain, limit=int(limit), order='date_order desc, id desc')
        return [{'id': s.id, 'name': s.name} for s in sos]

    @api.model
    def get_stock_locations(self, warehouse_id):
        wh = self.env['stock.warehouse'].browse(int(warehouse_id))
        if not wh.exists():
            return []
        locs = self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            ('location_id', 'child_of', wh.view_location_id.id),
        ], order='name asc')
        return [{'id': l.id, 'name': l.display_name} for l in locs]

    @api.model
    def get_finished_products(self, query='', limit=20, **kwargs):
        dom = [('type', 'in', ['product', 'consu'])]
        if query and query.strip():
            q = query.strip()
            dom += ['|', ('name', 'ilike', q), ('default_code', 'ilike', q)]
        prods = self.env['product.product'].search(dom, limit=int(limit), order='name asc')
        result = []
        for p in prods:
            bom = self._find_bom(p)
            result.append({
                'id': p.id,
                'name': p.display_name,
                'uom_id': p.uom_id.id,
                'uom_name': p.uom_id.name,
                'tracking': p.tracking,
                'has_bom': bool(bom),
            })
        return result

    @api.model
    def search_components(self, query='', limit=20, **kwargs):
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

    @api.model
    def search_byproducts(self, query='', limit=20):
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

    @api.model
    def get_bom_components(self, product_id, qty=1.0):
        product = self.env['product.product'].browse(int(product_id))
        if not product.exists():
            raise UserError(_('Producto no encontrado'))
        bom = self._find_bom(product)
        if not bom:
            return {'bom_id': False, 'bom_exists': False, 'components': []}
        base = bom.product_qty or 1.0
        comps = []
        for line in bom.bom_line_ids:
            req_qty = (line.product_qty * float(qty)) / base
            comps.append({
                'product_id': line.product_id.id,
                'name': line.product_id.display_name,
                'uom_id': line.product_uom_id.id or line.product_id.uom_id.id,
                'uom_name': line.product_uom_id.name or line.product_id.uom_id.name,
                'qty_formula': req_qty,
                'qty_real': req_qty,
                'tracking': line.product_id.tracking,
            })
        return {'bom_id': bom.id, 'bom_exists': True, 'components': comps}

    @api.model
    def get_lots(self, product_id, warehouse_id, limit=60, query=''):
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
            ('usage', '=', 'internal'),
        ])
        domain = [
            ('product_id', '=', product.id),
            ('location_id', 'in', internal_locs.ids),
            ('quantity', '>', 0),
        ]
        if query and query.strip():
            matching_lots = self.env['stock.lot'].search([
                ('product_id', '=', product.id),
                ('name', 'ilike', query.strip()),
            ])
            domain.append(('lot_id', 'in', matching_lots.ids))
        quants = Quant.search(domain, limit=int(limit) * 10)
        lot_totals = {}
        for q in quants:
            lot_key = q.lot_id.id if q.lot_id else False
            if lot_key not in lot_totals:
                lot_totals[lot_key] = {
                    'id': lot_key or -1,
                    'name': q.lot_id.name if q.lot_id else _('Sin lote / General'),
                    'qty': 0.0, 'reserved': 0.0,
                }
            lot_totals[lot_key]['qty'] += q.quantity
            lot_totals[lot_key]['reserved'] += q.reserved_quantity
        out = []
        for data in lot_totals.values():
            available = data['qty'] - data['reserved']
            if available > 0:
                out.append({'id': data['id'], 'name': data['name'], 'qty_available': round(available, 4)})
        out.sort(key=lambda x: x['name'] or 'ZZZZ')
        return out[:int(limit)]

    # ─── Validar lote manual ───────────────────────────────────────────────
    @api.model
    def validate_manual_lot(self, product_id, lot_name):
        lot_name = (lot_name or '').strip().upper()
        if not LOT_PATTERN.match(lot_name):
            raise UserError(_(
                'El lote "%(n)s" no cumple el patron requerido XX-##-##-##-##.', n=lot_name
            ))
        existing = self.env['stock.lot'].search([
            ('name', '=', lot_name),
            ('product_id', '=', int(product_id)),
        ], limit=1)
        if existing:
            raise UserError(_('El lote "%s" ya existe para este producto.') % lot_name)
        return True

    # ─── Crear/Actualizar BOM ──────────────────────────────────────────────
    @api.model
    def create_or_update_bom(self, product_id, components, byproducts=None, qty=1.0):
        product = self.env['product.product'].browse(int(product_id))
        if not product.exists():
            raise UserError(_('Producto no encontrado'))
        if not product.uom_id:
            raise UserError(_('El producto terminado "%s" no tiene unidad de medida definida.') % product.display_name)
        bom = self._find_bom(product)
        if bom:
            return {
                'bom_id': bom.id,
                'created': False,
                'message': _('Ya existe una lista de materiales para este producto.'),
            }
        self._validate_no_direct_cycle(product, components, byproducts)
        cleaned_components = self._validate_bom_component_data(components)
        cleaned_byproducts = self._validate_bom_byproduct_data(byproducts)
        if not cleaned_components:
            raise UserError(_('No se puede crear la lista de materiales sin ingredientes validos.'))
        Bom = self.env['mrp.bom'].sudo()
        bom_lines = []
        for c in cleaned_components:
            bom_lines.append((0, 0, {
                'product_id': c['product_id'],
                'product_qty': c['qty'],
                'product_uom_id': c['uom_id'],
            }))
        bom_vals = {
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_id': product.id,
            'product_qty': float(qty) or 1.0,
            'bom_line_ids': bom_lines,
        }
        if 'product_uom_id' in self.env['mrp.bom']._fields:
            bom_vals['product_uom_id'] = product.uom_id.id
        if cleaned_byproducts:
            bp_lines = []
            for bp in cleaned_byproducts:
                bp_lines.append((0, 0, {
                    'product_id': bp['product_id'],
                    'product_qty': bp['qty'],
                    'product_uom_id': bp['uom_id'],
                }))
            bom_vals['byproduct_ids'] = bp_lines
        try:
            bom = Bom.create(bom_vals)
        except ValidationError as ve:
            raise UserError(_('No fue posible crear la lista de materiales: %s') % ve)
        except Exception as e:
            raise UserError(_('Error al crear la lista de materiales: %s') % e)
        return {
            'bom_id': bom.id,
            'created': True,
            'message': _('Se creo una nueva lista de materiales para este producto.'),
        }

    # ─── Completar MO (robusto, multi-estrategia) ─────────────────────────
    @api.model
    def _complete_mo_robust(self, mo, product, qty, finished_lot):
        """
        Intenta completar la MO usando múltiples estrategias.
        Retorna dict con:
          - completed: bool
          - state: str (estado final de la MO)
          - error_detail: str (detalle del error si no se completó)
          - strategy_used: str (qué estrategia funcionó)
        """
        errors_log = []

        # ─── Preparar move lines de producto terminado ─────────────
        try:
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
        except Exception as prep_err:
            errors_log.append(f"Preparacion move lines: {prep_err}")
            _logger.warning("Error preparando finished move lines: %s", prep_err)

        # ─── Asegurar qty_producing en la MO ───────────────────────
        try:
            if hasattr(mo, 'qty_producing'):
                mo.qty_producing = qty
        except Exception as qp_err:
            errors_log.append(f"qty_producing: {qp_err}")
            _logger.warning("Error seteando qty_producing: %s", qp_err)

        # ─── Estrategia 1: Desbloquear + button_mark_done ──────────
        try:
            # Desbloquear si está bloqueada
            if mo.is_locked:
                try:
                    mo.action_toggle_is_locked()
                except Exception:
                    mo.is_locked = False

            mo.button_mark_done()
            mo.invalidate_recordset()
            if mo.state == 'done':
                return {
                    'completed': True,
                    'state': 'done',
                    'error_detail': '',
                    'strategy_used': 'button_mark_done',
                }
            errors_log.append(f"button_mark_done ejecuto sin error pero estado={mo.state}")
        except Exception as e1:
            errors_log.append(f"button_mark_done: {e1}")
            _logger.warning("Estrategia 1 (button_mark_done) fallo: %s", e1)

        # ─── Estrategia 2: Wizard de backorder (immediate_production) ─
        try:
            mo.invalidate_recordset()
            if mo.state != 'done':
                # Algunos Odoo devuelven un wizard al llamar button_mark_done
                # Intentamos buscar si hay wizard pendiente
                wizard_model = 'mrp.immediate.production'
                if wizard_model in self.env:
                    # Crear wizard y procesar
                    wiz = self.env[wizard_model].with_context(
                        active_id=mo.id,
                        active_ids=[mo.id],
                    ).create({})
                    if hasattr(wiz, 'process'):
                        wiz.process()
                    elif hasattr(wiz, 'action_confirm'):
                        wiz.action_confirm()

                    mo.invalidate_recordset()
                    if mo.state == 'done':
                        return {
                            'completed': True,
                            'state': 'done',
                            'error_detail': '',
                            'strategy_used': 'immediate_production_wizard',
                        }
                    errors_log.append(f"immediate_production wizard ejecuto pero estado={mo.state}")
        except Exception as e2:
            errors_log.append(f"immediate_production wizard: {e2}")
            _logger.warning("Estrategia 2 (immediate_production) fallo: %s", e2)

        # ─── Estrategia 3: Forzar backorder wizard ─────────────────
        try:
            mo.invalidate_recordset()
            if mo.state != 'done':
                backorder_model = 'mrp.production.backorder'
                if backorder_model in self.env:
                    ctx = {
                        'active_id': mo.id,
                        'active_ids': [mo.id],
                        'button_mark_done_production_ids': [mo.id],
                    }
                    try:
                        wiz = self.env[backorder_model].with_context(**ctx).create({})
                        if hasattr(wiz, 'action_close_mo'):
                            wiz.action_close_mo()
                        elif hasattr(wiz, 'action_produce'):
                            wiz.action_produce()

                        mo.invalidate_recordset()
                        if mo.state == 'done':
                            return {
                                'completed': True,
                                'state': 'done',
                                'error_detail': '',
                                'strategy_used': 'backorder_wizard',
                            }
                        errors_log.append(f"backorder wizard ejecuto pero estado={mo.state}")
                    except Exception as bw_err:
                        errors_log.append(f"backorder wizard create/action: {bw_err}")
        except Exception as e3:
            errors_log.append(f"backorder wizard setup: {e3}")
            _logger.warning("Estrategia 3 (backorder wizard) fallo: %s", e3)

        # ─── Estrategia 4: Forzar moves a done manualmente ────────
        try:
            mo.invalidate_recordset()
            if mo.state != 'done':
                for move in mo.move_raw_ids:
                    if move.state not in ('done', 'cancel'):
                        move.quantity = move.product_uom_qty
                        move._action_done()
                for move in mo.move_finished_ids:
                    if move.state not in ('done', 'cancel'):
                        move.quantity = move.product_uom_qty
                        move._action_done()

                # Intentar action_done o write state
                if hasattr(mo, 'action_done') and mo.state != 'done':
                    mo.action_done()

                mo.invalidate_recordset()
                if mo.state == 'done':
                    return {
                        'completed': True,
                        'state': 'done',
                        'error_detail': '',
                        'strategy_used': 'force_moves_done',
                    }
                errors_log.append(f"force_moves_done ejecuto pero estado={mo.state}")
        except Exception as e4:
            errors_log.append(f"force_moves_done: {e4}")
            _logger.warning("Estrategia 4 (force moves) fallo: %s", e4)

        # ─── Ninguna estrategia funcionó ───────────────────────────
        mo.invalidate_recordset()
        error_summary = " | ".join(errors_log[-4:])  # últimos 4 errores
        _logger.error(
            "MO %s (ID %s) no pudo completarse. Estado final: %s. Errores: %s",
            mo.name, mo.id, mo.state, error_summary
        )
        return {
            'completed': False,
            'state': mo.state,
            'error_detail': error_summary,
            'strategy_used': 'none',
        }

    # ─── Forzar validación de MO existente (retry) ─────────────────────────
    @api.model
    def force_validate_mo(self, mo_id):
        """
        Reintento de validación para MOs que quedaron en estado intermedio.
        Llamado desde el frontend cuando el usuario presiona "Forzar validación".
        """
        mo = self.env['mrp.production'].browse(int(mo_id))
        if not mo.exists():
            raise UserError(_('Orden de produccion no encontrada (ID %s)') % mo_id)

        if mo.state == 'done':
            return {
                'success': True,
                'state': 'done',
                'message': _('La orden ya estaba marcada como hecha.'),
            }

        if mo.state == 'cancel':
            raise UserError(_('La orden esta cancelada, no se puede validar.'))

        product = mo.product_id
        qty = mo.product_qty
        finished_lot = mo.lot_producing_id or None

        # Asegurar que las move lines de producto terminado estén correctas
        try:
            if mo.move_finished_ids:
                finished_move = mo.move_finished_ids[0]
                if not finished_move.move_line_ids:
                    self.env['stock.move.line'].create({
                        'move_id': finished_move.id,
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id': mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                        'lot_id': finished_lot.id if finished_lot else False,
                        'quantity': qty,
                    })
                else:
                    for ml in finished_move.move_line_ids:
                        ml.quantity = qty
                        if finished_lot and not ml.lot_id:
                            ml.lot_id = finished_lot.id

            # Asegurar cantidades en move lines de componentes
            for move in mo.move_raw_ids:
                if move.state in ('done', 'cancel'):
                    continue
                if move.move_line_ids:
                    total_ml = sum(ml.quantity for ml in move.move_line_ids)
                    if total_ml <= 0:
                        # Poner la cantidad requerida en la primera move line
                        move.move_line_ids[0].quantity = move.product_uom_qty
                else:
                    self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'product_id': move.product_id.id,
                        'product_uom_id': move.product_uom.id,
                        'location_id': move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                        'quantity': move.product_uom_qty,
                    })
        except Exception as prep_err:
            _logger.warning("force_validate_mo - prep error: %s", prep_err)

        result = self._complete_mo_robust(mo, product, qty, finished_lot)

        if result['completed']:
            # Marcar sesion como confirmada
            try:
                self.env['simplified.mrp.session'].mark_confirmed(mo.id)
            except Exception:
                pass

            return {
                'success': True,
                'state': 'done',
                'message': _('Orden %s validada exitosamente (metodo: %s).') % (
                    mo.name, result['strategy_used']
                ),
            }
        else:
            return {
                'success': False,
                'state': result['state'],
                'message': _('No se pudo completar la orden %s. Estado: %s.') % (
                    mo.name, result['state']
                ),
                'error_detail': result['error_detail'],
            }

    # ─── Crear MO ──────────────────────────────────────────────────────────
    @api.model
    def create_mo(self, payload):
        try:
            warehouse_id = payload.get('warehouse_id')
            product_id = payload.get('product_id')
            product_qty = payload.get('product_qty', 1.0)
            bom_id = payload.get('bom_id')
            components_map = payload.get('components') or []
            byproducts_map = payload.get('byproducts') or []
            origin_ref = payload.get('origin') or 'Simplified UI'
            custom_dest_loc = payload.get('location_dest_id')
            manual_lot_name = payload.get('manual_lot_name') or None
            auto_create_bom = payload.get('auto_create_bom', False)

            comps_clean = []
            for c in components_map:
                if not c:
                    continue
                pid = int(c.get('product_id')) if c.get('product_id') else False
                total_qty = float(c.get('qty', 0.0))
                lots_data = c.get('selected_lots', [])
                if pid and total_qty > 0:
                    comps_clean.append({'product_id': pid, 'qty': total_qty, 'lots': lots_data})

            if not warehouse_id or not product_id:
                raise UserError(_('Faltan datos obligatorios'))
            if not comps_clean:
                raise UserError(_('Debes capturar al menos un ingrediente con cantidad mayor a cero.'))

            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            product = self.env['product.product'].browse(int(product_id))
            qty = float(product_qty)
            if not wh.exists():
                raise UserError(_('Almacen invalido'))
            if not product.exists():
                raise UserError(_('Producto invalido'))

            pt = self._find_picking_type(wh)
            if not pt:
                raise UserError(_('No hay tipo de operacion de fabricacion configurado'))

            # BOM handling
            bom_message = ''
            if not bom_id:
                bom = self._find_bom(product)
                if bom:
                    bom_id = bom.id
                    bom_message = 'bom_existing'
                elif auto_create_bom:
                    bom_comps = [{'product_id': c['product_id'], 'qty': c['qty']} for c in comps_clean]
                    bom_bps = []
                    for bp in byproducts_map:
                        bp_pid = int(bp.get('product_id', 0))
                        bp_qty = float(bp.get('qty', 0))
                        if bp_pid and bp_qty > 0:
                            bom_bps.append({'product_id': bp_pid, 'qty': bp_qty})
                    bom_result = self.create_or_update_bom(product_id, bom_comps, bom_bps, qty)
                    bom_id = bom_result['bom_id']
                    bom_message = 'bom_created' if bom_result['created'] else 'bom_existing'

            mo_vals = {
                'product_id': product.id,
                'product_qty': qty,
                'product_uom_id': product.uom_id.id,
                'bom_id': bom_id or False,
                'picking_type_id': pt.id,
                'origin': origin_ref,
            }
            if custom_dest_loc:
                mo_vals['location_dest_id'] = int(custom_dest_loc)

            mo = self.env['mrp.production'].create(mo_vals)

            # ─── Lote producto terminado ───────────────────────────────
            finished_lot = None
            if product.tracking in ['lot', 'serial']:
                Lot = self.env['stock.lot']
                if manual_lot_name:
                    lot_name = manual_lot_name.strip().upper()
                    if not LOT_PATTERN.match(lot_name):
                        raise UserError(_(
                            'El lote "%(n)s" no cumple el patron XX-##-##-##-##.', n=lot_name
                        ))
                    if Lot.search([
                        ('name', '=', lot_name),
                        ('product_id', '=', product.id),
                        ('company_id', '=', mo.company_id.id),
                    ], limit=1):
                        raise UserError(_('El lote "%s" ya existe para este producto.') % lot_name)
                    finished_lot = Lot.create({
                        'name': lot_name,
                        'product_id': product.id,
                        'company_id': mo.company_id.id,
                    })
                else:
                    date_str = datetime.now().strftime('%Y%m%d')
                    ref = product.default_code or 'PROD'
                    existing = Lot.search([
                        ('product_id', '=', product.id),
                        ('company_id', '=', mo.company_id.id),
                        ('name', 'like', f"{date_str}-{ref}-%"),
                    ], order='name desc', limit=1)
                    consecutive = 1
                    if existing:
                        try:
                            consecutive = int(existing[0].name.split('-')[-1]) + 1
                        except Exception:
                            pass
                    finished_lot = Lot.create({
                        'name': f"{date_str}-{ref}-{consecutive:03d}",
                        'product_id': product.id,
                        'company_id': mo.company_id.id,
                    })
                mo.lot_producing_id = finished_lot.id

            mo.action_confirm()
            mo.user_id = self.env.uid

            # ─── Componentes y lotes ───────────────────────────────────
            existing_by_pid = {m.product_id.id: m for m in mo.move_raw_ids}

            for item in comps_clean:
                pid = item['product_id']
                req_qty_total = item['qty']
                lots_distribution = item['lots']
                prod = self.env['product.product'].browse(pid)
                move = existing_by_pid.get(pid)

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

                if move.move_line_ids:
                    move.move_line_ids.unlink()

                if not lots_distribution:
                    self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'product_id': pid,
                        'product_uom_id': prod.uom_id.id,
                        'location_id': move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                        'quantity': req_qty_total,
                    })
                else:
                    for l_data in lots_distribution:
                        l_id = l_data.get('lot_id')
                        l_qty = float(l_data.get('qty', 0.0))
                        if l_qty <= 0:
                            continue
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
            except Exception as e:
                _logger.warning("Auto assign warning: %s", e)

            # ─── Completar MO (robusto) ───────────────────────────────
            completion = self._complete_mo_robust(mo, product, qty, finished_lot)

            # Marcar sesion como confirmada
            try:
                self.env['simplified.mrp.session'].mark_confirmed(mo.id)
            except Exception:
                pass

            result = {
                'mo_id': mo.id,
                'name': mo.name,
                'state': completion['state'],
                'bom_message': bom_message,
                'completed': completion['completed'],
                'completion_strategy': completion['strategy_used'],
            }

            if not completion['completed']:
                result['completion_error'] = completion['error_detail']
                result['needs_force_validate'] = True

            return result

        except Exception as e:
            _logger.error("Error creating MO: %s", e, exc_info=True)
            raise UserError(_('Error creando orden de produccion: %s') % e)

    # ─── List & Detail ─────────────────────────────────────────────────────
    @api.model
    def get_my_productions(self, limit=50):
        mos = self.env['mrp.production'].search(
            [('user_id', '=', self.env.uid)],
            limit=int(limit), order='date_start desc, id desc',
        )
        return [{
            'id': mo.id,
            'name': mo.name,
            'state': mo.state,
            'product_id': mo.product_id.id,
            'product_name': mo.product_id.display_name,
            'product_qty': mo.product_qty,
            'uom_name': mo.product_uom_id.name,
            'date_start': mo.date_start.isoformat() if mo.date_start else False,
            'date_finished': mo.date_finished.isoformat() if mo.date_finished else False,
        } for mo in mos]

    @api.model
    def get_production_detail(self, mo_id):
        mo = self.env['mrp.production'].browse(int(mo_id))
        if not mo.exists():
            raise UserError(_('Orden no encontrada'))
        if mo.user_id.id != self.env.uid:
            raise UserError(_('No tienes permiso para ver esta orden'))
        components = []
        for move in mo.move_raw_ids:
            lots_info = []
            qty_done = 0.0
            for ml in move.move_line_ids:
                qty_done += ml.quantity
                lname = ml.lot_id.name if ml.lot_id else 'General'
                lots_info.append(f"{lname} ({ml.quantity})")
            components.append({
                'product_name': move.product_id.display_name,
                'qty_required': move.product_uom_qty,
                'qty_done': qty_done,
                'uom_name': move.product_uom.name,
                'lot_name': ", ".join(lots_info) if lots_info else "Sin consumo",
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