## ./__init__.py
```py
from . import models
```

## ./__manifest__.py
```py
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
}```

## ./models/__init__.py
```py
from . import simplified_mrp_api
from . import simplified_mrp_session
from . import res_config_settings```

## ./models/res_config_settings.py
```py
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
    )```

## ./models/simplified_mrp_api.py
```py
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

    # ─── Completar MO (robusto, multi-estrategia para Odoo 19) ──────────
    @api.model
    def _prepare_mo_for_completion(self, mo, product, qty, finished_lot):
        """
        Prepara la MO antes de intentar completarla:
        - Setea qty_producing
        - Asegura move lines de producto terminado
        - Asegura cantidades en move lines de componentes
        """
        errors = []

        # 1. qty_producing — FUNDAMENTAL en Odoo 19
        try:
            if hasattr(mo, 'qty_producing'):
                mo.qty_producing = qty
        except Exception as e:
            errors.append(f"qty_producing: {e}")

        # 2. Lote de producto terminado
        try:
            if finished_lot and hasattr(mo, 'lot_producing_id'):
                mo.lot_producing_id = finished_lot.id
        except Exception as e:
            errors.append(f"lot_producing_id: {e}")

        # 3. Move lines de producto terminado
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
        except Exception as e:
            errors.append(f"finished move lines: {e}")

        # 4. Asegurar cantidades en componentes
        try:
            for move in mo.move_raw_ids:
                if move.state in ('done', 'cancel'):
                    continue
                if move.move_line_ids:
                    total_ml = sum(ml.quantity for ml in move.move_line_ids)
                    if total_ml <= 0:
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
        except Exception as e:
            errors.append(f"component move lines: {e}")

        # 5. Desbloquear si está bloqueada
        try:
            if mo.is_locked:
                try:
                    mo.action_toggle_is_locked()
                except Exception:
                    mo.is_locked = False
        except Exception as e:
            errors.append(f"unlock: {e}")

        return errors

    @api.model
    def _complete_mo_robust(self, mo, product, qty, finished_lot):
        """
        Intenta completar la MO usando múltiples estrategias.
        
        En Odoo 19, button_mark_done() deja la MO en 'to_close'.
        Para pasar a 'done' se necesita llamar button_mark_done() DE NUEVO
        cuando la MO está en 'to_close', o usar el backorder wizard con
        action_close_mo().
        
        Retorna dict con:
          - completed: bool
          - state: str (estado final de la MO)
          - error_detail: str (detalle del error si no se completó)
          - strategy_used: str (qué estrategia funcionó)
        """
        errors_log = []

        # ─── Preparación ───────────────────────────────────────────
        prep_errors = self._prepare_mo_for_completion(mo, product, qty, finished_lot)
        if prep_errors:
            for pe in prep_errors:
                _logger.warning("Prep warning: %s", pe)

        # ─── Helper: check if done after each attempt ──────────────
        def _is_done():
            mo.invalidate_recordset()
            return mo.state == 'done'

        # ═══════════════════════════════════════════════════════════
        # Estrategia 1: button_mark_done (primera llamada)
        #   En Odoo 19 esto típicamente lleva a 'to_close'
        # ═══════════════════════════════════════════════════════════
        try:
            result = mo.button_mark_done()
            if _is_done():
                return {
                    'completed': True, 'state': 'done',
                    'error_detail': '', 'strategy_used': 'button_mark_done',
                }

            # button_mark_done puede devolver un wizard action dict
            if isinstance(result, dict) and result.get('res_model'):
                try:
                    wiz_model = result['res_model']
                    wiz_id = result.get('res_id')
                    ctx = result.get('context', {})
                    if wiz_id:
                        wiz = self.env[wiz_model].with_context(**ctx).browse(wiz_id)
                    else:
                        wiz = self.env[wiz_model].with_context(**ctx).create({})

                    # Intentar los métodos comunes del wizard
                    for method_name in ['process', 'action_close_mo', 'action_produce', 'action_confirm']:
                        if hasattr(wiz, method_name):
                            getattr(wiz, method_name)()
                            if _is_done():
                                return {
                                    'completed': True, 'state': 'done',
                                    'error_detail': '',
                                    'strategy_used': f'button_mark_done+wizard.{method_name}',
                                }
                            break
                except Exception as wiz_err:
                    errors_log.append(f"wizard from button_mark_done: {wiz_err}")

            errors_log.append(f"button_mark_done: estado={mo.state}")
        except Exception as e1:
            errors_log.append(f"button_mark_done: {e1}")
            _logger.warning("Estrategia 1 fallo: %s", e1)

        # ═══════════════════════════════════════════════════════════
        # Estrategia 2: Si está en 'to_close', llamar button_mark_done
        #   DE NUEVO — en Odoo 19 esto cierra la MO
        # ═══════════════════════════════════════════════════════════
        try:
            mo.invalidate_recordset()
            if mo.state == 'to_close':
                _logger.info("MO %s en to_close, llamando button_mark_done por segunda vez", mo.name)
                result2 = mo.button_mark_done()
                if _is_done():
                    return {
                        'completed': True, 'state': 'done',
                        'error_detail': '',
                        'strategy_used': 'double_button_mark_done',
                    }
                # Si devuelve wizard de nuevo
                if isinstance(result2, dict) and result2.get('res_model'):
                    try:
                        wiz_model = result2['res_model']
                        wiz_id = result2.get('res_id')
                        ctx = result2.get('context', {})
                        if wiz_id:
                            wiz = self.env[wiz_model].with_context(**ctx).browse(wiz_id)
                        else:
                            wiz = self.env[wiz_model].with_context(**ctx).create({})
                        for method_name in ['process', 'action_close_mo', 'action_produce', 'action_confirm']:
                            if hasattr(wiz, method_name):
                                getattr(wiz, method_name)()
                                if _is_done():
                                    return {
                                        'completed': True, 'state': 'done',
                                        'error_detail': '',
                                        'strategy_used': f'double_mark_done+wizard.{method_name}',
                                    }
                                break
                    except Exception as wiz2_err:
                        errors_log.append(f"wizard from 2nd button_mark_done: {wiz2_err}")
                errors_log.append(f"double_button_mark_done: estado={mo.state}")
        except Exception as e2:
            errors_log.append(f"double_button_mark_done: {e2}")
            _logger.warning("Estrategia 2 fallo: %s", e2)

        # ═══════════════════════════════════════════════════════════
        # Estrategia 3: Backorder wizard con contexto explícito
        # ═══════════════════════════════════════════════════════════
        try:
            mo.invalidate_recordset()
            if mo.state in ('to_close', 'progress', 'confirmed'):
                backorder_model = 'mrp.production.backorder'
                if backorder_model in self.env:
                    ctx = {
                        'active_id': mo.id,
                        'active_ids': [mo.id],
                        'button_mark_done_production_ids': [mo.id],
                    }
                    wiz = self.env[backorder_model].with_context(**ctx).create({})
                    # Probar todos los métodos posibles
                    for method_name in ['action_close_mo', 'action_produce', 'process', 'action_confirm']:
                        if hasattr(wiz, method_name):
                            try:
                                getattr(wiz, method_name)()
                                if _is_done():
                                    return {
                                        'completed': True, 'state': 'done',
                                        'error_detail': '',
                                        'strategy_used': f'backorder_wizard.{method_name}',
                                    }
                            except Exception as m_err:
                                errors_log.append(f"backorder.{method_name}: {m_err}")
                    errors_log.append(f"backorder wizard: estado={mo.state}")
        except Exception as e3:
            errors_log.append(f"backorder wizard: {e3}")
            _logger.warning("Estrategia 3 fallo: %s", e3)

        # ═══════════════════════════════════════════════════════════
        # Estrategia 4: immediate.production wizard
        # ═══════════════════════════════════════════════════════════
        try:
            mo.invalidate_recordset()
            if mo.state != 'done':
                immediate_model = 'mrp.immediate.production'
                if immediate_model in self.env:
                    wiz = self.env[immediate_model].with_context(
                        active_id=mo.id, active_ids=[mo.id],
                    ).create({})
                    for method_name in ['process', 'action_confirm', 'generate_produce']:
                        if hasattr(wiz, method_name):
                            try:
                                getattr(wiz, method_name)()
                                if _is_done():
                                    return {
                                        'completed': True, 'state': 'done',
                                        'error_detail': '',
                                        'strategy_used': f'immediate.{method_name}',
                                    }
                            except Exception:
                                pass
                    errors_log.append(f"immediate wizard: estado={mo.state}")
        except Exception as e4:
            errors_log.append(f"immediate wizard: {e4}")

        # ═══════════════════════════════════════════════════════════
        # Estrategia 5: Forzar moves a done + action_done en la MO
        # ═══════════════════════════════════════════════════════════
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

                if _is_done():
                    return {
                        'completed': True, 'state': 'done',
                        'error_detail': '',
                        'strategy_used': 'force_moves_done',
                    }

                # Último intento: button_mark_done después de forzar moves
                try:
                    mo.button_mark_done()
                    if _is_done():
                        return {
                            'completed': True, 'state': 'done',
                            'error_detail': '',
                            'strategy_used': 'force_moves+button_mark_done',
                        }
                except Exception:
                    pass

                errors_log.append(f"force_moves_done: estado={mo.state}")
        except Exception as e5:
            errors_log.append(f"force_moves_done: {e5}")
            _logger.warning("Estrategia 5 fallo: %s", e5)

        # ═══════════════════════════════════════════════════════════
        # Estrategia 6 (último recurso): SQL directo para to_close→done
        # Solo se usa cuando TODAS las demás fallaron y la MO está
        # en to_close (todos los moves ya están done)
        # ═══════════════════════════════════════════════════════════
        try:
            mo.invalidate_recordset()
            if mo.state == 'to_close':
                # Verificar que todos los moves estén done
                all_raw_done = all(m.state in ('done', 'cancel') for m in mo.move_raw_ids)
                all_fin_done = all(m.state in ('done', 'cancel') for m in mo.move_finished_ids)
                if all_raw_done and all_fin_done:
                    _logger.warning(
                        "MO %s: forzando state=done via SQL (todos los moves estan done)",
                        mo.name
                    )
                    self.env.cr.execute(
                        "UPDATE mrp_production SET state = 'done', date_finished = NOW() "
                        "WHERE id = %s AND state = 'to_close'",
                        (mo.id,)
                    )
                    mo.invalidate_recordset()
                    if mo.state == 'done':
                        return {
                            'completed': True, 'state': 'done',
                            'error_detail': '',
                            'strategy_used': 'sql_force_done',
                        }
                    errors_log.append("SQL update no cambio el estado")
                else:
                    errors_log.append(
                        f"to_close pero moves no done: raw={all_raw_done} fin={all_fin_done}"
                    )
        except Exception as e6:
            errors_log.append(f"sql_force: {e6}")
            _logger.warning("Estrategia 6 (SQL) fallo: %s", e6)

        # ─── Ninguna estrategia funcionó ───────────────────────────
        mo.invalidate_recordset()
        error_summary = " | ".join(errors_log[-5:])
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
        }```

## ./models/simplified_mrp_session.py
```py
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
        return True```

## ./security/security.xml
```xml
<odoo>
  <data noupdate="1">
    <record id="group_simplified_mrp_user" model="res.groups">
      <field name="name">Simplified MRP User</field>
      <field name="category_id" ref="base.module_category_manufacturing"/>
      <field name="implied_ids" eval="[(4, ref('mrp.group_mrp_user'))]"/>
    </record>
    <record id="group_simplified_mrp_supervisor" model="res.groups">
      <field name="name">Simplified MRP Supervisor</field>
      <field name="category_id" ref="base.module_category_manufacturing"/>
      <field name="implied_ids" eval="[(4, ref('group_simplified_mrp_user'))]"/>
    </record>
  </data>
</odoo>```

## ./static/src/js/simplified_mrp_client_action.js
```js
/** @odoo-module **/
import { registry } from '@web/core/registry';
import { Component, useState, onWillStart, onMounted } from '@odoo/owl';
import { useService } from '@web/core/utils/hooks';

const LOT_RE = /^[A-Za-z]{2}-\d{2}-\d{2}-\d{2}-\d{2}$/;

class SimplifiedMrp extends Component {
    static props = { "*": true };

    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            view: 'create',
            step: 'warehouse',

            // Config
            autoLot: false,
            toleranceGreen: 2,
            toleranceYellow: 10,
            toleranceOrange: 25,
            allowConfirmRed: true,
            autoCreateBom: true,
            autosave: true,

            // Step 1: Warehouse
            warehouses: [],
            warehouseId: null,

            // Step 2: Product
            products: [],
            productQuery: '',
            productId: null,
            productName: '',
            uomName: '',
            productTracking: 'none',
            productHasBom: false,
            qty: 1.0,
            saleOrderQuery: '',
            saleOrderResults: [],
            selectedSaleOrder: null,
            destLocations: [],
            selectedDestLocation: null,

            // Step lot_config
            lotSeg1: '', lotSeg2: '', lotSeg3: '', lotSeg4: '', lotSeg5: '',
            lotPreview: '',
            lotSegErrors: { s1: false, s2: false, s3: false, s4: false, s5: false },

            // Step 3: Components
            bomId: null,
            bomExists: false,
            components: [],
            compSearchQuery: '',
            compSearchResults: [],
            newCompQty: 1.0,

            // Step byproducts
            byproducts: [],
            bpSearchQuery: '',
            bpSearchResults: [],
            newBpQty: 1.0,

            // Step 4: Lots
            lots: [],
            lotQuery: '',
            assignedLots: {},
            compIndex: 0,

            // Review
            reviewWarnings: [],

            // Result
            resultMoId: null,
            resultMoName: '',
            bomMessage: '',
            resultMoState: '',
            needsForceValidate: false,
            completionError: '',
            forceValidating: false,

            // List / Detail
            myProductions: [],
            selectedMo: null,
            moDetail: null,

            // Persistence
            hasRecoverableSession: false,
            saving: false,
            lastSavedAt: null,

            // UI
            submitting: false,
        });

        onWillStart(async () => {
            await this.loadConfig();
            await this.loadWarehouses();
            await this.loadMyProductions();
            await this.checkRecoverableSession();
        });
    }

    // ═══════════════════════════════════════════════════════════════════════
    // UTILITIES
    // ═══════════════════════════════════════════════════════════════════════
    toNum(v) {
        const n = typeof v === 'number' ? v : parseFloat(v);
        return Number.isFinite(n) ? n : 0;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // POKA-YOKE ENGINE
    // ═══════════════════════════════════════════════════════════════════════
    getWarningLevel(expected, real) {
        if (!expected || expected === 0) {
            if (real > 0) return { level: 'orange', msg: 'Componente extra no contemplado en formula', icon: '⚠', pct: 100 };
            return { level: 'green', msg: 'OK', icon: '✓', pct: 0 };
        }
        const diff = real - expected;
        const pct = Math.abs((diff / expected) * 100);

        if (real === 0 && expected > 0) {
            return { level: 'red', msg: `Cantidad cero — se esperaban ${expected}`, icon: '⛔', pct: 100 };
        }
        if (pct <= this.state.toleranceGreen) {
            return { level: 'green', msg: 'Dentro de tolerancia', icon: '✓', pct };
        }
        if (pct <= this.state.toleranceYellow) {
            const dir = diff > 0 ? 'mas' : 'menos';
            return { level: 'yellow', msg: `${Math.abs(diff).toFixed(2)} ${dir} de lo esperado`, icon: '⚠', pct };
        }
        if (pct <= this.state.toleranceOrange) {
            const dir = diff > 0 ? 'por encima' : 'por debajo';
            return { level: 'orange', msg: `Desviacion importante: ${pct.toFixed(1)}% ${dir}`, icon: '⚠', pct };
        }
        const dir = diff > 0 ? 'por encima' : 'por debajo';
        return { level: 'red', msg: `Desviacion critica: ${pct.toFixed(1)}% ${dir} de la formula`, icon: '⛔', pct };
    }

    getComponentWarning(comp) {
        return this.getWarningLevel(comp.qty_formula || 0, comp.qty_real || 0);
    }

    get globalWarnings() {
        const warnings = [];
        for (const c of this.state.components) {
            const w = this.getComponentWarning(c);
            if (w.level !== 'green') {
                warnings.push({ ...w, name: c.name, product_id: c.product_id });
            }
        }
        return warnings;
    }

    get hasRedWarnings() {
        return this.globalWarnings.some(w => w.level === 'red');
    }

    get hasOrangeWarnings() {
        return this.globalWarnings.some(w => w.level === 'orange');
    }

    // ═══════════════════════════════════════════════════════════════════════
    // LOT SEGMENTS
    // ═══════════════════════════════════════════════════════════════════════
    _computePreview() {
        const { lotSeg1, lotSeg2, lotSeg3, lotSeg4, lotSeg5 } = this.state;
        const s1 = (lotSeg1 || '__').padEnd(2, '_').slice(0, 2).toUpperCase();
        const s2 = (lotSeg2 || '__').padStart(2, '0').slice(0, 2);
        const s3 = (lotSeg3 || '__').padStart(2, '0').slice(0, 2);
        const s4 = (lotSeg4 || '__').padStart(2, '0').slice(0, 2);
        const s5 = (lotSeg5 || '__').padStart(2, '0').slice(0, 2);
        this.state.lotPreview = `${s1}-${s2}-${s3}-${s4}-${s5}`;
    }

    _assembleLotName() {
        const { lotSeg1, lotSeg2, lotSeg3, lotSeg4, lotSeg5 } = this.state;
        const s1 = (lotSeg1 || '').trim().toUpperCase();
        const s2 = lotSeg2 ? String(parseInt(lotSeg2) || 0).padStart(2, '0') : '';
        const s3 = lotSeg3 ? String(parseInt(lotSeg3) || 0).padStart(2, '0') : '';
        const s4 = lotSeg4 ? String(parseInt(lotSeg4) || 0).padStart(2, '0') : '';
        const s5 = lotSeg5 ? String(parseInt(lotSeg5) || 0).padStart(2, '0') : '';
        if (!s1 || !s2 || !s3 || !s4 || !s5) return null;
        return `${s1}-${s2}-${s3}-${s4}-${s5}`;
    }

    _validateSegments() {
        const errs = {
            s1: !/^[A-Za-z]{2}$/.test(this.state.lotSeg1),
            s2: !/^\d{1,2}$/.test(this.state.lotSeg2),
            s3: !/^\d{1,2}$/.test(this.state.lotSeg3),
            s4: !/^\d{1,2}$/.test(this.state.lotSeg4),
            s5: !/^\d{1,2}$/.test(this.state.lotSeg5),
        };
        this.state.lotSegErrors = errs;
        return !Object.values(errs).some(Boolean);
    }

    onSeg1Change(ev) {
        this.state.lotSeg1 = ev.target.value.replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 2);
        this.state.lotSegErrors.s1 = false;
        this._computePreview();
        if (this.state.lotSeg1.length === 2) {
            ev.target.closest('.o_smrp_lot_builder')?.querySelector('[data-seg="2"]')?.focus();
        }
    }
    onSeg2Change(ev) {
        this.state.lotSeg2 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s2 = false;
        this._computePreview();
        if (this.state.lotSeg2.length === 2) ev.target.closest('.o_smrp_lot_builder')?.querySelector('[data-seg="3"]')?.focus();
    }
    onSeg3Change(ev) {
        this.state.lotSeg3 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s3 = false;
        this._computePreview();
        if (this.state.lotSeg3.length === 2) ev.target.closest('.o_smrp_lot_builder')?.querySelector('[data-seg="4"]')?.focus();
    }
    onSeg4Change(ev) {
        this.state.lotSeg4 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s4 = false;
        this._computePreview();
        if (this.state.lotSeg4.length === 2) ev.target.closest('.o_smrp_lot_builder')?.querySelector('[data-seg="5"]')?.focus();
    }
    onSeg5Change(ev) {
        this.state.lotSeg5 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s5 = false;
        this._computePreview();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CONFIG
    // ═══════════════════════════════════════════════════════════════════════
    async loadConfig() {
        try {
            const cfg = await this.orm.call('aq.simplified.mrp.api', 'get_mrp_config', [], {});
            this.state.autoLot = cfg.auto_lot === true;
            this.state.toleranceGreen = cfg.tolerance_green || 2;
            this.state.toleranceYellow = cfg.tolerance_yellow || 10;
            this.state.toleranceOrange = cfg.tolerance_orange || 25;
            this.state.allowConfirmRed = cfg.allow_confirm_red !== false;
            this.state.autoCreateBom = cfg.auto_create_bom !== false;
            this.state.autosave = cfg.autosave !== false;
        } catch (e) {
            console.warn('[SMRP] Config load failed, using defaults', e);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // PERSISTENCE
    // ═══════════════════════════════════════════════════════════════════════
    async checkRecoverableSession() {
        try {
            const res = await this.orm.call('simplified.mrp.session', 'load_session', [], {});
            if (res.found && res.current_step !== 'warehouse') {
                this.state.hasRecoverableSession = true;
            }
        } catch (e) {
            console.warn('[SMRP] Session check failed', e);
        }
    }

    async recoverSession() {
        try {
            const res = await this.orm.call('simplified.mrp.session', 'load_session', [], {});
            if (!res.found) {
                this.notification.add('No hay sesion para recuperar', { type: 'warning' });
                this.state.hasRecoverableSession = false;
                return;
            }
            this.state.warehouseId = res.warehouse_id || null;
            this.state.productId = res.product_id || null;
            this.state.productName = res.product_name || '';
            this.state.productTracking = res.product_tracking || 'none';
            this.state.uomName = res.uom_name || '';
            this.state.qty = res.product_qty || 1.0;
            this.state.bomId = res.bom_id || null;
            this.state.saleOrderQuery = res.sale_order_ref || '';
            this.state.lotSeg1 = res.lot_seg1 || '';
            this.state.lotSeg2 = res.lot_seg2 || '';
            this.state.lotSeg3 = res.lot_seg3 || '';
            this.state.lotSeg4 = res.lot_seg4 || '';
            this.state.lotSeg5 = res.lot_seg5 || '';
            this._computePreview();
            this.state.components = res.components || [];
            this.state.byproducts = res.byproducts || [];
            this.state.assignedLots = res.assigned_lots || {};
            this.state.step = res.current_step || 'warehouse';
            this.state.hasRecoverableSession = false;

            if (this.state.warehouseId) await this.loadDestLocations();

            this.notification.add('Sesion recuperada exitosamente', { type: 'success' });
        } catch (e) {
            this.notifyError('Error recuperando sesion', e);
        }
    }

    async discardSession() {
        try {
            await this.orm.call('simplified.mrp.session', 'discard_session', [], {});
            this.state.hasRecoverableSession = false;
            this.notification.add('Sesion descartada', { type: 'info' });
        } catch (e) {
            console.warn('[SMRP] Discard session error', e);
        }
    }

    async autoSave() {
        if (!this.state.autosave) return;
        if (this.state.step === 'warehouse' || this.state.step === 'done') return;
        try {
            this.state.saving = true;
            await this.orm.call('simplified.mrp.session', 'save_session', [{
                warehouse_id: this.state.warehouseId,
                product_id: this.state.productId,
                product_qty: this.toNum(this.state.qty),
                bom_id: this.state.bomId,
                origin: this.state.saleOrderQuery || '',
                location_dest_id: this.state.selectedDestLocation?.id || false,
                current_step: this.state.step,
                lot_seg1: this.state.lotSeg1,
                lot_seg2: this.state.lotSeg2,
                lot_seg3: this.state.lotSeg3,
                lot_seg4: this.state.lotSeg4,
                lot_seg5: this.state.lotSeg5,
                components: this.state.components,
                byproducts: this.state.byproducts,
                assigned_lots: this.state.assignedLots,
                sale_order_ref: this.state.saleOrderQuery || '',
            }], {});
            this.state.lastSavedAt = new Date().toLocaleTimeString();
            this.state.saving = false;
        } catch (e) {
            this.state.saving = false;
            console.warn('[SMRP] Autosave failed', e);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // DATA LOADERS
    // ═══════════════════════════════════════════════════════════════════════
    async loadWarehouses() {
        try {
            this.state.warehouses = await this.orm.call('aq.simplified.mrp.api', 'get_warehouses', [], {});
        } catch (e) { this.notifyError('Error cargando almacenes', e); }
    }

    async loadDestLocations() {
        if (!this.state.warehouseId) return;
        try {
            this.state.destLocations = await this.orm.call(
                'aq.simplified.mrp.api', 'get_stock_locations', [this.state.warehouseId], {}
            );
        } catch (e) { console.error('Error cargando ubicaciones:', e); }
    }

    async loadMyProductions() {
        try {
            this.state.myProductions = await this.orm.call('aq.simplified.mrp.api', 'get_my_productions', [50], {});
        } catch (e) { this.notifyError('Error cargando mis ordenes', e); }
    }

    async loadMoDetail(moId) {
        try {
            this.state.moDetail = await this.orm.call('aq.simplified.mrp.api', 'get_production_detail', [moId], {});
            this.state.selectedMo = moId;
            this.state.view = 'detail';
        } catch (e) { this.notifyError('Error cargando detalle', e); }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // SEARCHES
    // ═══════════════════════════════════════════════════════════════════════
    async searchProducts() {
        try {
            this.state.products = await this.orm.call(
                'aq.simplified.mrp.api', 'get_finished_products',
                [this.state.productQuery || '', 20], {}
            );
        } catch (e) { this.notifyError('Error buscando productos', e); }
    }

    async searchComponents() {
        if (!this.state.compSearchQuery) { this.state.compSearchResults = []; return; }
        try {
            this.state.compSearchResults = await this.orm.call(
                'aq.simplified.mrp.api', 'search_components',
                [this.state.compSearchQuery, 20], {}
            );
        } catch (e) { this.notifyError('Error buscando ingredientes', e); }
    }

    async searchByproducts() {
        if (!this.state.bpSearchQuery) { this.state.bpSearchResults = []; return; }
        try {
            this.state.bpSearchResults = await this.orm.call(
                'aq.simplified.mrp.api', 'search_byproducts',
                [this.state.bpSearchQuery, 20], {}
            );
        } catch (e) { this.notifyError('Error buscando subproductos', e); }
    }

    async searchSaleOrders() {
        const q = this.state.saleOrderQuery;
        if (!q || q.length < 2) { this.state.saleOrderResults = []; return; }
        try {
            this.state.saleOrderResults = await this.orm.call(
                'aq.simplified.mrp.api', 'get_sale_orders', [q, 10], {}
            );
        } catch (e) { console.error(e); }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP 1: WAREHOUSE
    // ═══════════════════════════════════════════════════════════════════════
    async selectWarehouse(id) {
        this.state.warehouseId = id;
        this.state.step = 'product';
        await this.loadDestLocations();
        await this.autoSave();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP 2: PRODUCT
    // ═══════════════════════════════════════════════════════════════════════
    selectProduct(p) {
        this.state.productId = p.id;
        this.state.productName = p.name;
        this.state.uomName = p.uom_name || '';
        this.state.productTracking = p.tracking || 'none';
        this.state.productHasBom = p.has_bom || false;
        this.state.products = [];
    }

    selectSaleOrder(so) {
        this.state.selectedSaleOrder = so;
        this.state.saleOrderQuery = so.name;
        this.state.saleOrderResults = [];
    }

    async confirmProductAndConfig() {
        if (!this.state.productId) {
            this.notification.add('Selecciona un producto', { type: 'warning' }); return;
        }
        const qty = this.toNum(this.state.qty);
        if (qty <= 0) {
            this.notification.add('Cantidad invalida', { type: 'warning' }); return;
        }
        this.state.qty = qty;

        const needsManualLot = !this.state.autoLot && this.state.productTracking !== 'none';
        if (needsManualLot) {
            this.state.lotSeg1 = ''; this.state.lotSeg2 = ''; this.state.lotSeg3 = '';
            this.state.lotSeg4 = ''; this.state.lotSeg5 = '';
            this.state.lotPreview = '__-__-__-__-__';
            this.state.lotSegErrors = { s1: false, s2: false, s3: false, s4: false, s5: false };
            this.state.step = 'lot_config';
            await this.autoSave();
            return;
        }
        await this._loadComponents();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP LOT_CONFIG
    // ═══════════════════════════════════════════════════════════════════════
    async confirmManualLot() {
        if (!this._validateSegments()) {
            this.notification.add('Completa correctamente todos los segmentos del lote', { type: 'warning' });
            return;
        }
        const name = this._assembleLotName();
        if (!name) {
            this.notification.add('Lote incompleto', { type: 'warning' }); return;
        }
        await this._loadComponents();
    }

    backFromLotConfig() { this.state.step = 'product'; }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP 3: COMPONENTS (with Poka-Yoke)
    // ═══════════════════════════════════════════════════════════════════════
    async _loadComponents() {
        try {
            const res = await this.orm.call(
                'aq.simplified.mrp.api', 'get_bom_components',
                [this.state.productId, this.state.qty], {}
            );
            this.state.bomId = res.bom_id || null;
            this.state.bomExists = res.bom_exists || false;
            this.state.components = (res.components || []).map(c => ({
                ...c,
                qty_formula: this.toNum(c.qty_formula) || 0,
                qty_real: this.toNum(c.qty_real) || this.toNum(c.qty_formula) || 1.0,
            }));
            this.state.assignedLots = {};
            this.state.step = 'components';
            await this.autoSave();
        } catch (e) { this.notifyError('Error obteniendo componentes', e); }
    }

    updateRealQty(idx, ev) {
        const c = this.state.components[idx];
        if (c) {
            c.qty_real = this.toNum(ev.target.value);
            this.state.components = [...this.state.components];
        }
    }

    setFormulaQty(idx) {
        const c = this.state.components[idx];
        if (c) {
            c.qty_real = c.qty_formula;
            this.state.components = [...this.state.components];
        }
    }

    removeComponent(idx) {
        this.state.components.splice(idx, 1);
        this.state.components = [...this.state.components];
    }

    addComponentFromSearch(p) {
        if (!this.state.components.find(c => c.product_id === p.id)) {
            this.state.components.push({
                product_id: p.id, name: p.name, uom_id: p.uom_id, uom_name: p.uom_name,
                qty_formula: 0,
                qty_real: this.toNum(this.state.newCompQty) || 1.0,
                tracking: p.tracking || 'none',
            });
            this.state.components = [...this.state.components];
        }
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
        this.state.newCompQty = 1.0;
    }

    async continueFromComponents() {
        if (!this.state.components.length) {
            this.notification.add('Debes agregar al menos un ingrediente', { type: 'warning' }); return;
        }
        this.state.step = 'byproducts';
        await this.autoSave();
    }

    backToProduct() {
        const needsManualLot = !this.state.autoLot && this.state.productTracking !== 'none';
        this.state.step = needsManualLot ? 'lot_config' : 'product';
    }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP BYPRODUCTS
    // ═══════════════════════════════════════════════════════════════════════
    addByproductFromSearch(p) {
        if (!this.state.byproducts.find(bp => bp.product_id === p.id)) {
            this.state.byproducts.push({
                product_id: p.id, name: p.name, uom_id: p.uom_id, uom_name: p.uom_name,
                qty: this.toNum(this.state.newBpQty) || 1.0,
            });
            this.state.byproducts = [...this.state.byproducts];
        }
        this.state.bpSearchQuery = '';
        this.state.bpSearchResults = [];
        this.state.newBpQty = 1.0;
    }

    updateBpQty(idx, ev) {
        const bp = this.state.byproducts[idx];
        if (bp) {
            bp.qty = this.toNum(ev.target.value);
            this.state.byproducts = [...this.state.byproducts];
        }
    }

    removeByproduct(idx) {
        this.state.byproducts.splice(idx, 1);
        this.state.byproducts = [...this.state.byproducts];
    }

    async continueFromByproducts() {
        this.state.compIndex = 0;
        this.state.step = 'lots';
        await this.loadLotsForCurrent();
        await this.autoSave();
    }

    backToComponents() { this.state.step = 'components'; }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP 4: LOTS
    // ═══════════════════════════════════════════════════════════════════════
    async loadLotsForCurrent() {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        this.state.lotQuery = '';
        try {
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api', 'get_lots',
                [comp.product_id, this.state.warehouseId],
                { limit: 60, query: '' }
            );
            if (!this.state.assignedLots[comp.product_id])
                this.state.assignedLots[comp.product_id] = {};
        } catch (e) { this.notifyError('Error cargando lotes', e); }
    }

    async searchLots() {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        try {
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api', 'get_lots',
                [comp.product_id, this.state.warehouseId],
                { limit: 60, query: this.state.lotQuery || '' }
            );
        } catch (e) { this.notifyError('Error buscando lotes', e); }
    }

    getAssignedTotal(productId) {
        return Object.values(this.state.assignedLots[productId] || {})
            .reduce((s, v) => s + this.toNum(v), 0);
    }

    getRemainingToAssign(productId) {
        const comp = this.state.components.find(c => c.product_id === productId);
        if (!comp) return 0;
        const target = this.toNum(comp.qty_real);
        const assigned = this.getAssignedTotal(productId);
        return Math.max(0, target - assigned);
    }

    getLotAssignedValue(productId, lotId) {
        return (this.state.assignedLots[productId] || {})[lotId] || 0;
    }

    getLotStatusClass(productId) {
        const comp = this.state.components.find(c => c.product_id === productId);
        if (!comp) return 'empty';
        const target = this.toNum(comp.qty_real);
        const assigned = this.getAssignedTotal(productId);
        if (assigned === 0) return 'empty';
        const diff = Math.abs(assigned - target);
        const pct = target > 0 ? (diff / target) * 100 : 0;
        if (pct <= this.state.toleranceGreen) return 'ok';
        if (assigned < target) return 'partial';
        return 'over';
    }

    getLotStatusMessage(productId) {
        const comp = this.state.components.find(c => c.product_id === productId);
        if (!comp) return '';
        const target = this.toNum(comp.qty_real);
        const assigned = this.getAssignedTotal(productId);
        if (assigned === 0) return 'Sin asignar — distribuye la cantidad entre los lotes disponibles';
        const diff = Math.abs(assigned - target);
        const pct = target > 0 ? (diff / target) * 100 : 0;
        if (pct <= this.state.toleranceGreen) return '✓ Cantidad completa asignada correctamente';
        if (assigned < target) return `Faltan ${(target - assigned).toFixed(2)} por asignar`;
        return `Exceso de ${(assigned - target).toFixed(2)} sobre lo requerido`;
    }

    updateLotAssignment(lotId, val) {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        const qty = this.toNum(val);
        if (!this.state.assignedLots[comp.product_id])
            this.state.assignedLots[comp.product_id] = {};
        if (qty > 0)
            this.state.assignedLots[comp.product_id][lotId] = qty;
        else
            delete this.state.assignedLots[comp.product_id][lotId];
        this.state.assignedLots = { ...this.state.assignedLots };
    }

    fillRemainingLot(lotId) {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        const remaining = this.getRemainingToAssign(comp.product_id);
        const lot = this.state.lots.find(l => l.id === lotId);
        if (!lot) return;
        const currentlyAssigned = this.getLotAssignedValue(comp.product_id, lotId);
        const maxAvail = lot.qty_available;
        const fillQty = Math.min(remaining, maxAvail - currentlyAssigned);
        if (fillQty <= 0 && remaining <= 0) {
            this.notification.add('Ya se asigno la cantidad completa', { type: 'info' });
            return;
        }
        const newVal = currentlyAssigned + Math.max(0, fillQty);
        this.updateLotAssignment(lotId, newVal);
    }

    async nextLotStep() {
        const comp = this.state.components[this.state.compIndex];
        const assigned = this.getAssignedTotal(comp.product_id);
        if (comp.tracking !== 'none' && assigned <= 0) {
            this.notification.add('No has asignado ninguna cantidad a lotes.', { type: 'danger' }); return;
        }
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1;
            await this.loadLotsForCurrent();
        } else {
            this.state.step = 'review';
            this._buildReviewWarnings();
            await this.autoSave();
        }
    }

    async prevLotStep() {
        if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
            await this.loadLotsForCurrent();
        } else {
            this.state.step = 'byproducts';
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // STEP REVIEW
    // ═══════════════════════════════════════════════════════════════════════
    _buildReviewWarnings() {
        this.state.reviewWarnings = this.globalWarnings;
    }

    get reviewRedCount() {
        return this.state.reviewWarnings.filter(w => w.level === 'red').length;
    }
    get reviewOrangeCount() {
        return this.state.reviewWarnings.filter(w => w.level === 'orange').length;
    }
    get reviewYellowCount() {
        return this.state.reviewWarnings.filter(w => w.level === 'yellow').length;
    }

    backToLots() {
        this.state.compIndex = this.state.components.length - 1;
        this.state.step = 'lots';
        this.loadLotsForCurrent();
    }

    backToComponentsFromReview() {
        this.state.step = 'components';
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CREATE MO
    // ═══════════════════════════════════════════════════════════════════════
    async createMO() {
        if (this.state.submitting) return;

        if (this.hasRedWarnings && !this.state.allowConfirmRed) {
            this.notification.add(
                'No se puede confirmar con desviaciones criticas. Revisa los ingredientes.',
                { type: 'danger' }
            );
            return;
        }

        this.state.submitting = true;
        try {
            const compsPayload = this.state.components.map(c => {
                const lotsMap = this.state.assignedLots[c.product_id] || {};
                const lotsList = Object.entries(lotsMap).map(([lid, qty]) => ({
                    lot_id: parseInt(lid), qty: this.toNum(qty),
                }));
                return { product_id: c.product_id, qty: c.qty_real, selected_lots: lotsList };
            });

            let originVal = null;
            if (this.state.selectedSaleOrder?.name)
                originVal = this.state.selectedSaleOrder.name;
            else if (this.state.saleOrderQuery)
                originVal = this.state.saleOrderQuery;

            let manualLotName = null;
            if (!this.state.autoLot && this.state.productTracking !== 'none') {
                manualLotName = this._assembleLotName();
            }

            const payload = {
                warehouse_id: this.state.warehouseId,
                product_id: this.state.productId,
                product_qty: this.toNum(this.state.qty),
                bom_id: this.state.bomId,
                origin: originVal,
                location_dest_id: this.state.selectedDestLocation?.id || null,
                components: compsPayload,
                byproducts: this.state.byproducts,
                manual_lot_name: manualLotName,
                auto_create_bom: this.state.autoCreateBom,
            };

            const res = await this.orm.call('aq.simplified.mrp.api', 'create_mo', [payload], {});
            this.state.resultMoId = res.mo_id || null;
            this.state.resultMoName = res.name || '';
            this.state.bomMessage = res.bom_message || '';
            this.state.resultMoState = res.state || '';
            this.state.needsForceValidate = res.needs_force_validate || false;
            this.state.completionError = res.completion_error || '';
            this.state.step = 'done';

            if (res.completed) {
                this.notification.add('Orden de produccion creada y validada exitosamente', { type: 'success' });
            } else {
                this.notification.add(
                    `Orden creada pero NO se pudo marcar como hecha (estado: ${res.state}). Usa el boton "Forzar validacion".`,
                    { type: 'warning', sticky: true }
                );
            }

            await this.loadMyProductions();
        } catch (e) {
            this.notifyError('Error creando orden de produccion', e);
        } finally {
            this.state.submitting = false;
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // FORCE VALIDATE (retry)
    // ═══════════════════════════════════════════════════════════════════════
    async forceValidateMO() {
        if (!this.state.resultMoId || this.state.forceValidating) return;
        this.state.forceValidating = true;
        try {
            const res = await this.orm.call(
                'aq.simplified.mrp.api', 'force_validate_mo',
                [this.state.resultMoId], {}
            );
            if (res.success) {
                this.state.needsForceValidate = false;
                this.state.resultMoState = 'done';
                this.state.completionError = '';
                this.notification.add(res.message, { type: 'success' });
                await this.loadMyProductions();
            } else {
                this.state.completionError = res.error_detail || '';
                this.state.resultMoState = res.state || '';
                this.notification.add(res.message, { type: 'danger', sticky: true });
            }
        } catch (e) {
            this.notifyError('Error forzando validacion', e);
        } finally {
            this.state.forceValidating = false;
        }
    }

    openMO() {
        if (!this.state.resultMoId) return;
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'mrp.production',
            res_id: this.state.resultMoId,
            views: [[false, 'form']],
            target: 'current',
        });
    }

    resetWizard() {
        Object.assign(this.state, {
            view: 'create', step: 'warehouse', warehouseId: null,
            productId: null, productName: '', qty: 1.0,
            productTracking: 'none', productHasBom: false,
            saleOrderQuery: '', saleOrderResults: [], selectedSaleOrder: null,
            selectedDestLocation: null, products: [],
            lotSeg1: '', lotSeg2: '', lotSeg3: '', lotSeg4: '', lotSeg5: '',
            lotPreview: '', lotSegErrors: { s1: false, s2: false, s3: false, s4: false, s5: false },
            components: [], byproducts: [], assignedLots: {},
            compIndex: 0, bomId: null, bomExists: false,
            lotQuery: '', resultMoId: null, resultMoName: '', bomMessage: '',
            resultMoState: '', needsForceValidate: false, completionError: '',
            forceValidating: false,
            compSearchQuery: '', compSearchResults: [], newCompQty: 1.0,
            bpSearchQuery: '', bpSearchResults: [], newBpQty: 1.0,
            reviewWarnings: [], submitting: false,
            hasRecoverableSession: false, saving: false, lastSavedAt: null,
        });
    }

    // ═══════════════════════════════════════════════════════════════════════
    // NAV / MISC
    // ═══════════════════════════════════════════════════════════════════════
    notifyError(msg, e) {
        console.error(msg, e);
        this.notification.add(`${msg}: ${e.data?.message || e.message || e}`, { type: 'danger' });
    }

    showCreate() { this.resetWizard(); }
    showList() { this.state.view = 'list'; this.loadMyProductions(); }
    backToList() { this.state.view = 'list'; this.state.selectedMo = null; this.state.moDetail = null; }

    getStateLabel(s) {
        return ({ draft: 'Borrador', confirmed: 'Confirmada', progress: 'En progreso',
            to_close: 'Por cerrar', done: 'Hecha', cancel: 'Cancelada' })[s] || s;
    }
    getStateClass(s) {
        if (s === 'done') return 'success';
        if (s === 'cancel') return 'danger';
        if (s === 'progress') return 'warning';
        return 'info';
    }

    getWarehouseName() {
        const w = this.state.warehouses.find(x => x.id === this.state.warehouseId);
        return w ? w.name : '';
    }
}

SimplifiedMrp.template = 'aq_simplified_mrp.Main';
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);```

## ./static/src/scss/simplified_mrp.scss
```scss
/* ========================================================================
   SMRP - Simplified MRP System v3.0
   Poka-Yoke, BOM, Subproductos, Persistencia, Resumen
   ======================================================================== */

:root {
  --smrp-brand-blue: #2737BE;
  --smrp-brand-blue-2: #3B51E0;
  --smrp-brand-blue-3: #EEF1FF;
  --smrp-brand-green: #3EF24E;
  --smrp-brand-green-2: #BFFFD0;
  --smrp-lot-text-color: #7C3AED;
  --smrp-lot-text-bg: #F5F0FF;
  --smrp-lot-text-border: #C4B5FD;
  --smrp-lot-num-color: #1D4ED8;
  --smrp-lot-num-bg: #EFF6FF;
  --smrp-lot-num-border: #93C5FD;
  --smrp-ink: #0b1426;
  --smrp-bg: #F6F8FF;
  --smrp-card: #fff;
  --smrp-radius: 14px;
  --smrp-shadow: 0 6px 20px rgba(23, 35, 79, .06);
  --smrp-tap: 48px;
  --smrp-gap: 12px;
  --smrp-maxw: 1280px;
  --smrp-fs-base: 15px;
  --smrp-fs-lg: 17px;
  --smrp-fs-xl: 22px;

  /* Poka-Yoke semaphore — STRONG / HIGH CONTRAST */
  --pk-green: #16a34a;
  --pk-green-bg: #dcfce7;
  --pk-green-border: #22c55e;
  --pk-green-text: #052e16;

  --pk-yellow: #ca8a04;
  --pk-yellow-bg: #fef08a;
  --pk-yellow-border: #eab308;
  --pk-yellow-text: #422006;

  --pk-orange: #ea580c;
  --pk-orange-bg: #fed7aa;
  --pk-orange-border: #f97316;
  --pk-orange-text: #431407;

  --pk-red: #dc2626;
  --pk-red-bg: #fecaca;
  --pk-red-border: #ef4444;
  --pk-red-text: #450a0a;
}

/* ========================================================================
   LAYOUT
   ======================================================================== */
.o_smrp {
  background: var(--smrp-bg);
  color: var(--smrp-ink);
  height: 100%;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0;
}

.o_smrp_wrapper {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  padding: 16px;
  padding-bottom: 100px;
  scrollbar-width: thin;
  scrollbar-color: var(--smrp-brand-blue-2) transparent;

  &::-webkit-scrollbar { width: 8px; }
  &::-webkit-scrollbar-track { background: transparent; }
  &::-webkit-scrollbar-thumb {
    background: #cbd5e1; border-radius: 4px;
    &:hover { background: var(--smrp-brand-blue-2); }
  }
}

.o_smrp_container {
  max-width: var(--smrp-maxw);
  margin: 0 auto;
  display: grid;
  gap: var(--smrp-gap);
}

.o_smrp_section { padding: 8px; }

/* ========================================================================
   STEPS
   ======================================================================== */
.o_smrp_steps {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  list-style: none;
  padding: 12px 16px;
  margin: 0 0 12px;
  flex-wrap: wrap;
  background: var(--smrp-bg);
  position: sticky;
  top: 0;
  z-index: 100;
  border-bottom: 1px solid rgba(207, 214, 234, 0.3);

  li {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 12px;
    border: 1px solid #cfd6ea;
    border-radius: 999px;
    background: #fff;
    font-size: 13px;
    font-weight: 500;

    &.active {
      border-color: var(--smrp-brand-blue);
      background: var(--smrp-brand-blue-3);
      color: var(--smrp-brand-blue);
    }
    &.done {
      border-color: var(--smrp-brand-green);
      background: var(--smrp-brand-green-2);
      color: #0c3a15;
    }
  }

  .num {
    width: 24px; height: 24px;
    border-radius: 999px;
    display: inline-grid;
    place-items: center;
    background: #e8ecff;
    color: var(--smrp-brand-blue);
    font-weight: 700;
    font-size: 12px;
  }
}

/* ========================================================================
   HEADINGS
   ======================================================================== */
.o_smrp h2 {
  font-size: var(--smrp-fs-xl);
  color: var(--smrp-brand-blue);
  text-align: center;
  margin: 8px 0 12px;
  font-weight: 600;
}

/* ========================================================================
   INPUTS
   ======================================================================== */
.o_smrp_row {
  display: flex; flex-wrap: wrap; gap: 12px;
  align-items: flex-end; justify-content: center;
}

.o_smrp_field_group {
  display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 200px;
}

.o_smrp_label { font-size: 13px; font-weight: 700; color: #555; margin-left: 2px; }

.o_smrp_input {
  padding: 11px 14px;
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  width: 100%;
  box-sizing: border-box;
  font-size: var(--smrp-fs-base);
  background: #fff;
  transition: all 0.2s ease;

  &--xl { font-size: var(--smrp-fs-lg); padding: 12px 16px; }
  &:focus {
    outline: none;
    border-color: var(--smrp-brand-blue);
    box-shadow: 0 0 0 3px var(--smrp-brand-blue-3);
  }
}

select.o_smrp_input {
  appearance: none;
  background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23007CB2%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E");
  background-repeat: no-repeat;
  background-position: right 12px top 50%;
  background-size: 10px auto;
  padding-right: 30px;
}

.o_smrp_autocomplete { position: relative; width: 100%; }

.o_smrp_autocomplete_list {
  position: absolute; top: 100%; left: 0; right: 0;
  background: #fff; border: 1px solid #d3d8e3; border-top: none;
  border-radius: 0 0 8px 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  z-index: 100; max-height: 200px; overflow-y: auto;

  .item {
    padding: 10px 14px; font-size: 14px; cursor: pointer;
    border-bottom: 1px solid #eee;
    &:last-child { border-bottom: none; }
    &:hover { background: #f6f8ff; color: var(--smrp-brand-blue); }
  }
}

/* ========================================================================
   BUTTONS
   ======================================================================== */
.o_smrp_btn {
  height: var(--smrp-tap);
  padding: 0 18px;
  border-radius: var(--smrp-radius);
  border: 1px solid var(--smrp-brand-blue);
  background: var(--smrp-brand-blue);
  color: #fff;
  font-size: var(--smrp-fs-base);
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--smrp-shadow);
  flex-shrink: 0;
  transition: all 0.2s ease;

  &:hover:not(:disabled) { filter: brightness(1.05); transform: translateY(-1px); }
  &:active:not(:disabled) { transform: translateY(0); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }

  &--ghost { background: #fff; color: var(--smrp-brand-blue); }
  &--xl { min-width: 160px; }
  &--sm { height: 36px; padding: 0 12px; font-size: 13px; min-width: auto; }
  &--danger { background: var(--pk-red); border-color: var(--pk-red); color: #fff; }
  &.confirm { background: var(--smrp-brand-green); border-color: var(--smrp-brand-green); color: #062b10; }

  /* ── Boton NEGRO para confirmar produccion final ── */
  &--black {
    background: #111827;
    border-color: #111827;
    color: #fff;
    font-weight: 800;
    letter-spacing: 0.02em;
    box-shadow: 0 4px 16px rgba(0,0,0,0.3);

    &:hover:not(:disabled) {
      background: #1f2937;
      border-color: #1f2937;
      box-shadow: 0 6px 24px rgba(0,0,0,0.4);
    }

    &:disabled {
      background: #6b7280;
      border-color: #6b7280;
      opacity: 0.7;
    }
  }

  /* Fill remaining lot button */
  &--fill {
    background: #2563eb;
    border-color: #2563eb;
    color: #fff;
    font-size: 12px;
    height: 32px;
    padding: 0 10px;
  }
}

/* ========================================================================
   ACTIONS BAR
   ======================================================================== */
.o_smrp_actions {
  display: flex; gap: 10px; justify-content: center;
  align-items: center; flex-wrap: wrap; padding: 8px 0;

  &--end { justify-content: flex-end; }
  &--center { justify-content: center; }
  &--sticky {
    position: sticky; bottom: 0;
    padding: 12px 16px; margin: 0 -16px -16px;
    background: linear-gradient(180deg,
      rgba(246,248,255,0) 0%, rgba(246,248,255,0.97) 20%, rgba(246,248,255,1) 50%
    );
    backdrop-filter: blur(8px);
    z-index: 50;
    border-top: 1px solid rgba(207,214,234,0.2);
  }
}

/* ========================================================================
   CARDS
   ======================================================================== */
.o_smrp_cards {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  padding: 4px; margin-bottom: 16px;
  &--center { justify-items: center; }
}

.o_smrp_card {
  width: 100%; max-width: 300px;
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  background: var(--smrp-card);
  box-shadow: var(--smrp-shadow);
  padding: 16px; text-align: center;
  position: relative; transition: all 0.2s ease;

  &_icon { font-size: 28px; margin-bottom: 8px; line-height: 1; }
  &_title { font-size: var(--smrp-fs-lg); font-weight: 600; margin-bottom: 4px; line-height: 1.3; }
  &_sub { font-size: 14px; color: #6b7996; }

  &.selectable {
    cursor: pointer;
    &:hover { border-color: var(--smrp-brand-blue-2); box-shadow: 0 8px 24px rgba(39,55,190,0.15); transform: translateY(-2px); }
    &:active { transform: translateY(0); }
  }

  &.selected {
    border-color: var(--smrp-brand-blue); border-width: 2px;
    background: var(--smrp-brand-blue-3); padding: 15px;
    .o_smrp_check { display: grid; }
  }
}

.o_smrp_check {
  position: absolute; top: -10px; right: -10px;
  width: 28px; height: 28px; border-radius: 999px;
  background: var(--smrp-brand-green); color: #062b10;
  display: none; place-items: center;
  font-weight: 700; font-size: 14px;
  box-shadow: 0 4px 12px rgba(62,242,78,0.4);
}

/* ========================================================================
   SELECTED BANNER
   ======================================================================== */
.o_smrp_selected {
  position: sticky; bottom: 0;
  padding: 14px 16px; margin: 16px -16px -16px;
  background: linear-gradient(180deg,
    rgba(246,248,255,0) 0%, rgba(246,248,255,0.97) 20%, rgba(246,248,255,1) 50%
  );
  backdrop-filter: blur(8px);
  border-radius: var(--smrp-radius) var(--smrp-radius) 0 0;
  text-align: center; font-size: var(--smrp-fs-base);
  z-index: 50; border-top: 1px solid rgba(207,214,234,0.3);

  .badge {
    display: inline-block; padding: 4px 10px;
    background: var(--smrp-brand-green-2); color: #062b10;
    border-radius: 8px; font-size: 12px; font-weight: 600; margin-right: 8px;
  }
  strong { font-weight: 600; }
}

/* ========================================================================
   BOXES, WIZARD
   ======================================================================== */
.o_smrp_wizard { display: grid; gap: 16px; }

.o_smrp_counter {
  text-align: center; font-size: var(--smrp-fs-base);
  color: var(--smrp-brand-blue); font-weight: 600;
  padding: 10px; background: #fff;
  border-radius: var(--smrp-radius); box-shadow: var(--smrp-shadow);
}

.o_smrp_box {
  padding: 18px; background: #fff;
  border-radius: var(--smrp-radius); box-shadow: var(--smrp-shadow);
  display: grid; gap: 12px;
  &--big { padding: 22px; }
}

.o_smrp_name { font-size: var(--smrp-fs-lg); font-weight: 600; line-height: 1.3; }
.o_smrp_meta { font-size: 14px; color: #6b7996; strong { font-weight: 600; color: var(--smrp-ink); } }

/* ========================================================================
   POKA-YOKE COMPONENT TABLE — ALIGNED GRID
   ======================================================================== */
$comp-cols: 1fr 110px 130px 1fr 110px;

.o_smrp_comp_table {
  display: grid; gap: 10px;
}

.o_smrp_comp_header {
  display: grid;
  grid-template-columns: $comp-cols;
  gap: 10px;
  padding: 0 16px 8px;
  font-size: 12px;
  font-weight: 700;
  color: #6b7996;
  text-transform: uppercase;
  letter-spacing: 0.04em;

  > div { white-space: nowrap; }
  > div:nth-child(2),
  > div:nth-child(3) { text-align: center; }
}

.o_smrp_comp_row {
  display: grid;
  grid-template-columns: $comp-cols;
  gap: 10px;
  align-items: center;
  padding: 14px 16px;
  background: #fff;
  border-radius: var(--smrp-radius);
  border-left: 5px solid #e2e8f0;
  box-shadow: var(--smrp-shadow);
  transition: all 0.2s ease;

  &--green  { border-left-color: var(--pk-green); background: var(--pk-green-bg); }
  &--yellow { border-left-color: var(--pk-yellow); background: var(--pk-yellow-bg); }
  &--orange { border-left-color: var(--pk-orange); background: var(--pk-orange-bg); }
  &--red    { border-left-color: var(--pk-red); background: var(--pk-red-bg); }
}

.o_smrp_comp_name {
  font-weight: 700; font-size: 15px;
  overflow: hidden; text-overflow: ellipsis;
  .uom { font-weight: 400; color: #6b7996; font-size: 13px; }
}

.o_smrp_comp_formula {
  text-align: center; font-size: 14px; color: #6b7996;
  .val { font-weight: 700; color: var(--smrp-ink); font-size: 16px; display: block; }
}

.o_smrp_comp_real_input {
  padding: 8px 10px;
  border: 2px solid #d0d7de;
  border-radius: 10px;
  font-size: 18px;
  font-weight: 800;
  text-align: center;
  width: 100%;
  box-sizing: border-box;
  color: var(--smrp-brand-blue);
  background: #fff;
  transition: all 0.2s;

  &:focus {
    outline: none;
    border-color: var(--smrp-brand-blue);
    box-shadow: 0 0 0 3px var(--smrp-brand-blue-3);
  }
}

.o_smrp_comp_status {
  display: flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 700;
  min-width: 0;

  .icon { font-size: 20px; flex-shrink: 0; }
  .msg { line-height: 1.3; }

  &--green  { color: var(--pk-green-text); }
  &--yellow { color: var(--pk-yellow-text); }
  &--orange { color: var(--pk-orange-text); }
  &--red    { color: var(--pk-red-text); }
}

.o_smrp_comp_actions {
  display: flex; gap: 4px; justify-content: flex-end;
}

/* BOM badge */
.o_smrp_bom_badge {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 18px; border-radius: 12px;
  font-size: 14px; font-weight: 600;
  margin-bottom: 12px;

  &--exists { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
  &--new    { background: #fef9c3; color: #854d0e; border: 1px solid #fde047; }
}

/* ========================================================================
   BYPRODUCTS
   ======================================================================== */
.o_smrp_bp_list { display: grid; gap: 10px; }

.o_smrp_bp_row {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  background: #fff; border-radius: var(--smrp-radius);
  border: 1px solid #d3d8e3; box-shadow: var(--smrp-shadow);

  .name { flex: 1; font-weight: 600; font-size: 15px; }
  .uom { font-size: 13px; color: #6b7996; }
}

/* ========================================================================
   PROGRESS BAR
   ======================================================================== */
.o_smrp_header_flex {
  display: flex; justify-content: space-between;
  align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;
}

.o_smrp_progress_text {
  font-size: 14px; color: #555; background: #f0f0f0;
  padding: 4px 8px; border-radius: 6px;
  strong { color: var(--smrp-brand-blue); }
}

.o_smrp_progress_bar {
  height: 10px; background: #e2e8f0;
  border-radius: 5px; overflow: hidden; margin-bottom: 20px;
}

.o_smrp_progress_fill {
  height: 100%; background: var(--smrp-brand-green);
  transition: width 0.3s ease;
  box-shadow: 0 0 10px rgba(62,242,78,0.5);
}

/* Lot target banner */
.o_smrp_lot_target {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 14px 20px;
  background: linear-gradient(135deg, #1e40af, #2563eb);
  border-radius: 12px;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
  box-shadow: 0 4px 12px rgba(37,99,235,0.3);

  .target-label { opacity: 0.85; }
  .target-value { font-size: 22px; font-weight: 900; letter-spacing: 0.02em; }
  .target-uom { opacity: 0.85; font-size: 14px; }
}

/* Lot status bar */
.o_smrp_lot_status_bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 8px;

  &--ok {
    background: var(--pk-green-bg);
    border: 2px solid var(--pk-green-border);
    color: var(--pk-green-text);
  }
  &--partial {
    background: var(--pk-yellow-bg);
    border: 2px solid var(--pk-yellow-border);
    color: var(--pk-yellow-text);
  }
  &--over {
    background: var(--pk-orange-bg);
    border: 2px solid var(--pk-orange-border);
    color: var(--pk-orange-text);
  }
  &--empty {
    background: #f1f5f9;
    border: 2px solid #cbd5e1;
    color: #475569;
  }
}

/* ========================================================================
   LOTS GRID
   ======================================================================== */
.o_smrp_lots {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px; padding: 4px; margin-bottom: 16px;
}

.o_smrp_lot_input_card {
  border: 1px solid #d3d8e3; border-radius: var(--smrp-radius);
  padding: 14px; background: #fff;
  transition: all 0.2s ease;
  display: flex; flex-direction: column; gap: 10px;
  box-shadow: var(--smrp-shadow);

  &.active { border-color: var(--smrp-brand-blue); background: #f0f4ff; box-shadow: 0 0 0 2px var(--smrp-brand-blue-3); }

  .head { display: flex; justify-content: space-between; align-items: flex-start;
    .name { font-weight: 700; font-size: 15px; word-break: break-all; }
    .avail { font-size: 12px; color: #444; background: #eef1ff; padding: 3px 8px; border-radius: 99px; white-space: nowrap; flex-shrink: 0; margin-left: 8px; }
  }
  .lot-actions {
    display: flex; align-items: center; gap: 8px; margin-top: auto;
    label { font-size: 13px; font-weight: 600; color: #666; white-space: nowrap; }
  }
}

.o_smrp_input_small {
  padding: 6px 10px; border: 1px solid #d0d7de;
  border-radius: 6px; width: 100%;
  font-weight: 700; font-size: 15px;
  color: var(--smrp-brand-blue); text-align: right; background: #fff;
  &:focus { outline: none; border-color: var(--smrp-brand-blue); box-shadow: 0 0 0 2px var(--smrp-brand-blue-3); }
}

.o_smrp_lot_search { margin-bottom: 4px; }

.o_smrp_empty {
  text-align: center; padding: 32px 16px;
  color: #6b7996; font-size: var(--smrp-fs-base);
  &--block { display: block; padding: 20px; background: #f9fafb; border-radius: var(--smrp-radius); border: 1px dashed #d3d8e3; }
}

/* ========================================================================
   REVIEW / SUMMARY — ALIGNED TABLE
   ======================================================================== */
$review-cols: 1fr 100px 100px 100px 1fr;

.o_smrp_review_grid { display: grid; gap: 14px; }

.o_smrp_review_card {
  padding: 18px; background: #fff;
  border-radius: var(--smrp-radius); box-shadow: var(--smrp-shadow);
  border: 1px solid #e2e8f0;

  .title { font-size: 14px; font-weight: 700; color: #6b7996; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px; }
  .value { font-size: 17px; font-weight: 700; color: var(--smrp-ink); }
}

/* Alerts — STRONG colors */
.o_smrp_review_alerts {
  padding: 16px; border-radius: var(--smrp-radius);
  margin-bottom: 12px;

  &--red    { background: var(--pk-red-bg); border: 3px solid var(--pk-red); color: var(--pk-red-text); }
  &--orange { background: var(--pk-orange-bg); border: 3px solid var(--pk-orange); color: var(--pk-orange-text); }
  &--yellow { background: var(--pk-yellow-bg); border: 3px solid var(--pk-yellow); color: var(--pk-yellow-text); }
  &--clean  { background: var(--pk-green-bg); border: 3px solid var(--pk-green); color: var(--pk-green-text); }
}

.o_smrp_review_alert_title {
  font-size: 16px; font-weight: 800; margin-bottom: 8px;
  display: flex; align-items: center; gap: 8px;
}

.o_smrp_review_alert_item {
  padding: 8px 12px; margin: 4px 0;
  border-radius: 8px; background: rgba(255,255,255,0.8);
  font-size: 14px; display: flex; align-items: center; gap: 8px;

  .icon { font-size: 18px; }
  .name { font-weight: 800; }
  .msg  { color: #333; font-weight: 500; }
}

.o_smrp_review_comp_summary {
  display: grid; gap: 6px;
}

.o_smrp_review_comp_header {
  display: grid;
  grid-template-columns: $review-cols;
  gap: 8px;
  padding: 10px 14px;
  font-weight: 800;
  font-size: 12px;
  color: #6b7996;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 2px solid #e2e8f0;

  > div:nth-child(2),
  > div:nth-child(3),
  > div:nth-child(4) { text-align: center; }
}

.o_smrp_review_comp_line {
  display: grid;
  grid-template-columns: $review-cols;
  gap: 8px; align-items: center;
  padding: 10px 14px;
  background: #fff; border-radius: 10px;
  border-left: 4px solid #e2e8f0;
  font-size: 14px;

  .name { font-weight: 700; }
  .num { text-align: center; font-weight: 600; }
  .diff { text-align: center; font-weight: 800; font-size: 13px; }

  &--green  { border-left-color: var(--pk-green); .diff { color: var(--pk-green); } }
  &--yellow { border-left-color: var(--pk-yellow); .diff { color: var(--pk-yellow); } }
  &--orange { border-left-color: var(--pk-orange); .diff { color: var(--pk-orange); } }
  &--red    { border-left-color: var(--pk-red); .diff { color: var(--pk-red); } }
}

/* ========================================================================
   AUTOSAVE INDICATOR
   ======================================================================== */
.o_smrp_save_indicator {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: #6b7996;
  padding: 4px 10px; border-radius: 8px;
  background: rgba(255,255,255,0.8);

  &--saving { color: var(--smrp-brand-blue); }
  &--saved  { color: #15803d; }
  &--error  { color: var(--pk-red); }
}

/* ========================================================================
   RECOVERY MODAL
   ======================================================================== */
.o_smrp_recovery {
  padding: 20px; background: #fff;
  border-radius: var(--smrp-radius);
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
  border: 2px solid var(--smrp-brand-blue);
  text-align: center;
  max-width: 500px; margin: 20px auto;

  .icon { font-size: 48px; margin-bottom: 12px; }
  .title { font-size: 18px; font-weight: 700; margin-bottom: 8px; }
  .desc { font-size: 14px; color: #6b7996; margin-bottom: 16px; }
}

/* ========================================================================
   DONE STATE
   ======================================================================== */
.o_smrp_done {
  text-align: center; padding: 32px 0;
  &_icon { font-size: 72px; margin-bottom: 16px; animation: bounce 0.6s ease; }
}

@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

/* ========================================================================
   NAV
   ======================================================================== */
.o_smrp_nav {
  display: flex; gap: 8px; justify-content: center;
  padding: 12px; background: var(--smrp-bg);
  z-index: 110; border-bottom: 1px solid #d3d8e3; flex-shrink: 0;
}

.o_smrp_nav_btn {
  padding: 12px 20px;
  border: 1px solid #d3d8e3; border-radius: var(--smrp-radius);
  background: #fff; color: var(--smrp-ink);
  font-size: var(--smrp-fs-base); cursor: pointer; transition: all .2s;
  &:hover { border-color: var(--smrp-brand-blue-2); }
  &.active { background: var(--smrp-brand-blue); color: #fff; border-color: var(--smrp-brand-blue); }
}

/* ========================================================================
   LIST
   ======================================================================== */
.o_smrp_list { display: grid; gap: 12px; padding: 4px; }

.o_smrp_list_item {
  display: flex; align-items: center; gap: 16px;
  padding: 16px; border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius); background: #fff;
  cursor: pointer; box-shadow: var(--smrp-shadow); transition: all .2s;
  &:hover { border-color: var(--smrp-brand-blue-2); box-shadow: 0 0 0 3px var(--smrp-brand-blue-3); }
}

.o_smrp_list_icon { font-size: 32px; flex-shrink: 0; }
.o_smrp_list_content { flex: 1; min-width: 0; }
.o_smrp_list_title { font-weight: 700; font-size: 18px; margin-bottom: 4px; word-break: break-word; }
.o_smrp_list_meta { font-size: 14px; opacity: .8; }

.o_smrp_list_badge {
  padding: 6px 12px; border-radius: 999px;
  font-size: 13px; font-weight: 700; flex-shrink: 0;
  &.success { background: var(--smrp-brand-green-2); color: #062b10; }
  &.danger  { background: #ffe0e0; color: #8b0000; }
  &.warning { background: #fff4e0; color: #8b5a00; }
  &.info    { background: var(--smrp-brand-blue-3); color: var(--smrp-brand-blue); }
}

.o_smrp_detail_row {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 0; border-bottom: 1px solid #f0f0f0;
  &:last-child { border-bottom: none; }
}
.o_smrp_detail_label { font-weight: 700; min-width: 180px; flex-shrink: 0; }

/* ========================================================================
   LOT CONFIG (XX-##-##-##-##)
   ======================================================================== */
.o_smrp_lot_config_card {
  background: #fff; border-radius: 20px;
  box-shadow: 0 8px 32px rgba(39,55,190,0.1), 0 2px 8px rgba(0,0,0,0.04);
  padding: 28px; display: grid; gap: 24px;
  max-width: 860px; margin: 0 auto;
}

.o_smrp_lot_config_header { display: flex; align-items: center; gap: 16px; }
.o_smrp_lot_config_icon { font-size: 40px; line-height: 1; flex-shrink: 0; }
.o_smrp_lot_config_title { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
.o_smrp_lot_config_subtitle { font-size: 14px; color: #6b7996; strong { color: var(--smrp-ink); font-weight: 600; } }

.o_smrp_lot_pattern_ref {
  background: linear-gradient(135deg, #f8faff, #f0f4ff);
  border: 1px solid #dde3f5; border-radius: 12px;
  padding: 16px 20px; display: flex; align-items: center;
  gap: 16px; flex-wrap: wrap;
}

.ref-label { font-size: 13px; font-weight: 700; color: #6b7996; text-transform: uppercase; letter-spacing: 0.05em; }
.ref-chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ref-dash { font-size: 18px; font-weight: 700; color: #9ca3af; }

.chip {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 15px; font-weight: 800; font-family: 'Courier New', monospace;
  letter-spacing: 0.08em; padding: 6px 14px; border-radius: 8px; border: 2px dashed;
  &--text { color: var(--smrp-lot-text-color); background: var(--smrp-lot-text-bg); border-color: var(--smrp-lot-text-border); }
  &--num  { color: var(--smrp-lot-num-color); background: var(--smrp-lot-num-bg); border-color: var(--smrp-lot-num-border); }
}

.ref-example { font-size: 13px; color: #6b7996; margin-left: auto; padding: 6px 12px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
  strong { color: var(--smrp-ink); font-family: 'Courier New', monospace; }
}

.o_smrp_lot_builder {
  display: flex; align-items: flex-start; justify-content: center;
  gap: 10px; flex-wrap: wrap; padding: 8px 0;
}

.o_smrp_lot_dash {
  font-size: 28px; font-weight: 800; color: #9ca3af;
  align-self: center; margin-top: -12px; user-select: none;
}

.o_smrp_lot_seg {
  display: flex; flex-direction: column; align-items: center; gap: 6px; min-width: 90px;

  .seg-badge {
    font-size: 11px; font-weight: 800; letter-spacing: 0.06em;
    padding: 3px 10px; border-radius: 999px;
    &--text { background: var(--smrp-lot-text-bg); color: var(--smrp-lot-text-color); border: 1px solid var(--smrp-lot-text-border); }
    &--num  { background: var(--smrp-lot-num-bg); color: var(--smrp-lot-num-color); border: 1px solid var(--smrp-lot-num-border); }
  }

  .seg-label { font-size: 12px; font-weight: 600; color: #9ca3af; text-transform: uppercase; }

  .seg-input {
    width: 80px; height: 64px; text-align: center;
    font-size: 26px; font-weight: 800; font-family: 'Courier New', monospace;
    border: 2.5px solid #e2e8f0; border-radius: 12px;
    background: #fafbff; transition: all 0.2s; caret-color: var(--smrp-brand-blue);
    &::placeholder { font-size: 22px; color: #d1d5db; }
    &:focus { outline: none; background: #fff; box-shadow: 0 0 0 4px rgba(39,55,190,0.12); }
    &--text { color: var(--smrp-lot-text-color); border-color: var(--smrp-lot-text-border);
      &:focus { border-color: var(--smrp-lot-text-color); box-shadow: 0 0 0 4px rgba(124,58,237,0.12); }
    }
    &--num { color: var(--smrp-lot-num-color); border-color: var(--smrp-lot-num-border);
      &:focus { border-color: var(--smrp-lot-num-color); box-shadow: 0 0 0 4px rgba(29,78,216,0.12); }
    }
  }

  .seg-footer { min-height: 18px; }
  .seg-hint { font-size: 11px; color: #9ca3af; text-align: center; }
  .seg-error { font-size: 11px; color: #ef4444; text-align: center; font-weight: 600; }

  &.filled .seg-input { background: linear-gradient(135deg, #fafbff, #f0f4ff); border-color: var(--smrp-brand-blue-2); }
  &.error .seg-input { border-color: #ef4444 !important; background: #fff5f5; box-shadow: 0 0 0 3px rgba(239,68,68,0.12) !important; animation: smrp-shake 0.35s ease; }
}

@keyframes smrp-shake {
  0%, 100% { transform: translateX(0); }
  20%, 60%  { transform: translateX(-4px); }
  40%, 80%  { transform: translateX(4px); }
}

.o_smrp_lot_preview {
  display: flex; align-items: center; justify-content: center;
  gap: 12px; padding: 16px 24px;
  background: linear-gradient(135deg, #1e2b8a, #2737BE);
  border-radius: 14px; box-shadow: 0 4px 16px rgba(39,55,190,0.3); flex-wrap: wrap;
  .preview-label { font-size: 13px; font-weight: 600; color: rgba(255,255,255,0.7); text-transform: uppercase; }
  .preview-value { font-family: 'Courier New', monospace; font-size: 28px; font-weight: 900; letter-spacing: 0.12em; color: #fff; text-shadow: 0 2px 8px rgba(0,0,0,0.2); }
}

.o_smrp_lot_legend { display: flex; gap: 20px; justify-content: center; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #6b7996; }
.legend-dot { width: 12px; height: 12px; border-radius: 3px;
  &--text { background: var(--smrp-lot-text-color); }
  &--num  { background: var(--smrp-lot-num-color); }
}

/* ========================================================================
   RESPONSIVE
   ======================================================================== */
@media (max-width: 767px) {
  .o_smrp_wrapper { padding: 12px; padding-bottom: 100px; }
  .o_smrp_cards { grid-template-columns: 1fr; }
  .o_smrp_lots  { grid-template-columns: 1fr; }
  .o_smrp_card  { max-width: 100%; }
  .o_smrp_input { min-width: 100%; }
  .o_smrp_actions { flex-direction: column; .o_smrp_btn { width: 100%; } }
  .o_smrp_steps { gap: 6px; padding: 8px;
    li { font-size: 12px; padding: 6px 10px; }
    .num { width: 22px; height: 22px; font-size: 11px; }
  }
  .o_smrp_comp_row { grid-template-columns: 1fr; gap: 6px; }
  .o_smrp_comp_header { display: none; }
  .o_smrp_review_comp_header { display: none; }
  .o_smrp_review_comp_line { grid-template-columns: 1fr; gap: 4px; }
  .o_smrp_lot_config_card { padding: 18px; gap: 18px; }
  .o_smrp_lot_builder { gap: 6px; }
  .o_smrp_lot_seg .seg-input { width: 58px; height: 54px; font-size: 20px; }
  .o_smrp_lot_dash { font-size: 20px; }
  .o_smrp_lot_preview { padding: 14px 16px; }
  .o_smrp_lot_preview .preview-value { font-size: 20px; letter-spacing: 0.08em; }
  .ref-example { margin-left: 0; }
}```

## ./static/src/xml/simplified_mrp_templates.xml
```xml
<templates id="template" xml:space="preserve">
  <t t-name="aq_simplified_mrp.Main">
    <div class="o_smrp">

      <div class="o_smrp_nav">
        <button class="o_smrp_nav_btn" t-att-class="{'active': state.view === 'create'}" t-on-click="() => this.showCreate()">+ Nueva orden</button>
        <button class="o_smrp_nav_btn" t-att-class="{'active': state.view === 'list' || state.view === 'detail'}" t-on-click="() => this.showList()">Mis ordenes</button>
        <!-- Save indicator -->
        <t t-if="state.saving">
          <span class="o_smrp_save_indicator o_smrp_save_indicator--saving">Guardando...</span>
        </t>
        <t t-elif="state.lastSavedAt">
          <span class="o_smrp_save_indicator o_smrp_save_indicator--saved">Guardado <t t-esc="state.lastSavedAt"/></span>
        </t>
      </div>

      <div class="o_smrp_wrapper">

        <!-- ══════════ RECOVERY MODAL ══════════ -->
        <t t-if="state.hasRecoverableSession and state.view === 'create' and state.step === 'warehouse'">
          <div class="o_smrp_recovery">
            <div class="icon">📋</div>
            <div class="title">Encontramos una sesion sin terminar</div>
            <div class="desc">Puedes recuperarla y continuar donde la dejaste, o descartarla para empezar de cero.</div>
            <div class="o_smrp_actions o_smrp_actions--center">
              <button class="o_smrp_btn confirm" t-on-click="() => this.recoverSession()">Recuperar sesion</button>
              <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.discardSession()">Descartar</button>
            </div>
          </div>
        </t>

        <t t-if="state.view === 'create'">

          <!-- Steps Header -->
          <div class="o_smrp_container">
            <ol class="o_smrp_steps" role="list">
              <li t-att-class="{'active': state.step === 'warehouse', 'done': state.step !== 'warehouse'}">
                <span class="num">1</span><span>Almacen</span>
              </li>
              <li t-att-class="{'active': state.step === 'product', 'done': ['lot_config','components','byproducts','lots','review','done'].includes(state.step)}">
                <span class="num">2</span><span>Producto</span>
              </li>
              <t t-if="!state.autoLot">
                <li t-att-class="{'active': state.step === 'lot_config', 'done': ['components','byproducts','lots','review','done'].includes(state.step)}">
                  <span class="num">3</span><span>Lote</span>
                </li>
              </t>
              <li t-att-class="{'active': state.step === 'components', 'done': ['byproducts','lots','review','done'].includes(state.step)}">
                <span class="num" t-esc="state.autoLot ? 3 : 4"/><span>Ingredientes</span>
              </li>
              <li t-att-class="{'active': state.step === 'byproducts', 'done': ['lots','review','done'].includes(state.step)}">
                <span class="num" t-esc="state.autoLot ? 4 : 5"/><span>Subproductos</span>
              </li>
              <li t-att-class="{'active': state.step === 'lots', 'done': ['review','done'].includes(state.step)}">
                <span class="num" t-esc="state.autoLot ? 5 : 6"/><span>Lotes</span>
              </li>
              <li t-att-class="{'active': state.step === 'review', 'done': state.step === 'done'}">
                <span class="num" t-esc="state.autoLot ? 6 : 7"/><span>Resumen</span>
              </li>
              <li t-att-class="{'active': state.step === 'done'}">
                <span class="num" t-esc="state.autoLot ? 7 : 8"/><span>Listo</span>
              </li>
            </ol>
          </div>

          <!-- ══════════ PASO 1: ALMACEN ══════════ -->
          <t t-if="state.step === 'warehouse'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Selecciona almacen</h2>
              <div class="o_smrp_cards o_smrp_cards--center">
                <t t-foreach="state.warehouses" t-as="w" t-key="w.id">
                  <div class="o_smrp_card selectable" t-on-click="() => this.selectWarehouse(w.id)">
                    <div class="o_smrp_card_icon">🏬</div>
                    <div class="o_smrp_card_title"><t t-esc="w.name"/></div>
                    <div class="o_smrp_card_sub" t-if="w.code"><t t-esc="w.code"/></div>
                  </div>
                </t>
              </div>
            </div>
          </t>

          <!-- ══════════ PASO 2: PRODUCTO ══════════ -->
          <t t-if="state.step === 'product'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Producto y Origen</h2>
              <div class="o_smrp_box">
                <div class="o_smrp_row">
                  <div class="o_smrp_field_group">
                    <label class="o_smrp_label">Producto a fabricar</label>
                    <input class="o_smrp_input o_smrp_input--xl" type="text" placeholder="Buscar..."
                           t-model="state.productQuery" t-on-input="() => this.searchProducts()"/>
                  </div>
                  <div class="o_smrp_field_group">
                    <label class="o_smrp_label">Cantidad</label>
                    <input class="o_smrp_input o_smrp_input--xl" type="number" min="0" step="0.01"
                           t-model="state.qty" placeholder="1.0"/>
                  </div>
                </div>
                <div class="o_smrp_row" style="margin-top:10px;">
                  <div class="o_smrp_field_group">
                    <label class="o_smrp_label">Origen (Orden de Venta)</label>
                    <div class="o_smrp_autocomplete">
                      <input class="o_smrp_input" type="text" placeholder="Escribe OP o Referencia..."
                             t-model="state.saleOrderQuery" t-on-input="() => this.searchSaleOrders()"/>
                      <div class="o_smrp_autocomplete_list" t-if="state.saleOrderResults.length">
                        <t t-foreach="state.saleOrderResults" t-as="so" t-key="so.id">
                          <div class="item" t-on-click="() => this.selectSaleOrder(so)"><t t-esc="so.name"/></div>
                        </t>
                      </div>
                    </div>
                  </div>
                  <div class="o_smrp_field_group">
                    <label class="o_smrp_label">Destino (Ubicacion)</label>
                    <select class="o_smrp_input" t-model="state.selectedDestLocation">
                      <option value="">-- Por defecto (Stock) --</option>
                      <t t-foreach="state.destLocations" t-as="loc" t-key="loc.id">
                        <option t-att-value="loc"><t t-esc="loc.name"/></option>
                      </t>
                    </select>
                  </div>
                </div>
                <div class="o_smrp_actions o_smrp_actions--end">
                  <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => { this.state.step = 'warehouse'; }">← Volver</button>
                  <button class="o_smrp_btn confirm o_smrp_btn--xl"
                          t-att-disabled="!(state.productId &amp;&amp; this.toNum(state.qty) > 0)"
                          t-on-click="() => this.confirmProductAndConfig()">Siguiente →</button>
                </div>
              </div>
              <div class="o_smrp_cards o_smrp_cards--center" style="margin-top:16px;">
                <t t-foreach="state.products" t-as="p" t-key="p.id">
                  <div class="o_smrp_card selectable" t-att-class="{'selected': state.productId === p.id}"
                       t-on-click="() => this.selectProduct(p)">
                    <div class="o_smrp_card_icon">📦</div>
                    <div class="o_smrp_card_title"><t t-esc="p.name"/></div>
                    <div class="o_smrp_card_sub"><t t-esc="p.uom_name"/>
                      <t t-if="p.has_bom"> · <strong style="color: var(--smrp-brand-blue);">Con formula</strong></t>
                    </div>
                    <div class="o_smrp_check">✔</div>
                  </div>
                </t>
              </div>
              <div class="o_smrp_selected" t-if="state.productName">
                <span class="badge">Seleccionado</span>
                <strong><t t-esc="state.productName"/></strong>
                <t t-if="state.qty"> · Cant.: <strong><t t-esc="state.qty"/></strong></t>
              </div>
            </div>
          </t>

          <!-- ══════════ PASO LOT_CONFIG ══════════ -->
          <t t-if="state.step === 'lot_config'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Numero de Lote</h2>
              <div class="o_smrp_lot_config_card">
                <div class="o_smrp_lot_config_header">
                  <div class="o_smrp_lot_config_icon">🏷️</div>
                  <div>
                    <div class="o_smrp_lot_config_title">Asigna el lote del producto terminado</div>
                    <div class="o_smrp_lot_config_subtitle">Producto: <strong><t t-esc="state.productName"/></strong></div>
                  </div>
                </div>
                <div class="o_smrp_lot_pattern_ref">
                  <span class="ref-label">Formato:</span>
                  <div class="ref-chips">
                    <span class="chip chip--text">XX</span><span class="ref-dash">–</span>
                    <span class="chip chip--num">##</span><span class="ref-dash">–</span>
                    <span class="chip chip--num">##</span><span class="ref-dash">–</span>
                    <span class="chip chip--num">##</span><span class="ref-dash">–</span>
                    <span class="chip chip--num">##</span>
                  </div>
                  <div class="ref-example">Ejemplo: <strong>AB-01-02-03-04</strong></div>
                </div>
                <div class="o_smrp_lot_builder">
                  <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors.s1, 'filled': state.lotSeg1.length === 2}">
                    <div class="seg-badge seg-badge--text">ABC</div>
                    <div class="seg-label">Letras</div>
                    <input class="seg-input seg-input--text" type="text" maxlength="2" placeholder="AB" data-seg="1"
                           t-att-value="state.lotSeg1" t-on-input="(ev) => this.onSeg1Change(ev)" autocomplete="off" spellcheck="false"/>
                    <div class="seg-footer">
                      <span t-if="!state.lotSegErrors.s1" class="seg-hint">2 letras</span>
                      <span t-if="state.lotSegErrors.s1" class="seg-error">2 letras</span>
                    </div>
                  </div>
                  <div class="o_smrp_lot_dash">—</div>
                  <t t-foreach="[['lotSeg2','s2','2'],['lotSeg3','s3','3'],['lotSeg4','s4','4'],['lotSeg5','s5','5']]" t-as="seg" t-key="seg[2]">
                    <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors[seg[1]], 'filled': state[seg[0]].length > 0}">
                      <div class="seg-badge seg-badge--num">123</div>
                      <div class="seg-label">Numero</div>
                      <input class="seg-input seg-input--num" type="text" inputmode="numeric" maxlength="2" placeholder="01"
                             t-att-data-seg="seg[2]"
                             t-att-value="state[seg[0]]"
                             t-on-input="(ev) => this['onSeg' + seg[2] + 'Change'](ev)"
                             autocomplete="off"/>
                      <div class="seg-footer">
                        <span t-if="!state.lotSegErrors[seg[1]]" class="seg-hint">2 digitos</span>
                        <span t-if="state.lotSegErrors[seg[1]]" class="seg-error">2 digitos</span>
                      </div>
                    </div>
                    <div class="o_smrp_lot_dash" t-if="seg[2] !== '5'">—</div>
                  </t>
                </div>
                <div class="o_smrp_lot_preview">
                  <span class="preview-label">Vista previa:</span>
                  <span class="preview-value" t-esc="state.lotPreview || '__-__-__-__-__'"/>
                </div>
                <div class="o_smrp_lot_legend">
                  <span class="legend-item"><span class="legend-dot legend-dot--text"/> Texto (letras)</span>
                  <span class="legend-item"><span class="legend-dot legend-dot--num"/> Numerico (digitos)</span>
                </div>
              </div>
              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.backFromLotConfig()">← Volver</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.confirmManualLot()">Confirmar lote →</button>
              </div>
            </div>
          </t>

          <!-- ══════════ PASO COMPONENTS (POKA-YOKE) ══════════ -->
          <t t-if="state.step === 'components'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Ingredientes</h2>

              <!-- BOM Badge -->
              <div style="text-align:center;">
                <t t-if="state.bomExists">
                  <div class="o_smrp_bom_badge o_smrp_bom_badge--exists">
                    ✓ Ya existe una lista de materiales para este producto. Las cantidades esperadas se cargaron automaticamente.
                  </div>
                </t>
                <t t-else="">
                  <div class="o_smrp_bom_badge o_smrp_bom_badge--new">
                    ⚡ No existe formula para este producto. Se creara automaticamente al confirmar.
                  </div>
                </t>
              </div>

              <!-- Table header -->
              <div class="o_smrp_comp_header">
                <div>Ingrediente</div>
                <div>Formula</div>
                <div>Real</div>
                <div>Estado</div>
                <div></div>
              </div>

              <!-- Component rows -->
              <div class="o_smrp_comp_table">
                <t t-foreach="state.components" t-as="comp" t-key="comp.product_id">
                  <t t-set="w" t-value="this.getComponentWarning(comp)"/>
                  <div class="o_smrp_comp_row" t-att-class="'o_smrp_comp_row--' + w.level">
                    <div class="o_smrp_comp_name">
                      <t t-esc="comp.name"/>
                      <span class="uom"> (<t t-esc="comp.uom_name"/>)</span>
                    </div>
                    <div class="o_smrp_comp_formula">
                      <span class="val"><t t-esc="(comp.qty_formula || 0).toFixed(2)"/></span>
                      esperado
                    </div>
                    <div>
                      <input type="number" min="0" step="0.01"
                             class="o_smrp_comp_real_input"
                             t-att-value="comp.qty_real"
                             t-on-change="(ev) => this.updateRealQty(comp_index, ev)"/>
                    </div>
                    <div class="o_smrp_comp_status" t-att-class="'o_smrp_comp_status--' + w.level">
                      <span class="icon"><t t-esc="w.icon"/></span>
                      <span class="msg"><t t-esc="w.msg"/></span>
                    </div>
                    <div class="o_smrp_comp_actions">
                      <button class="o_smrp_btn o_smrp_btn--sm o_smrp_btn--ghost" title="Usar cantidad formula"
                              t-on-click="() => this.setFormulaQty(comp_index)">= Formula</button>
                      <button class="o_smrp_btn o_smrp_btn--sm o_smrp_btn--danger" title="Quitar"
                              t-on-click="() => this.removeComponent(comp_index)">✕</button>
                    </div>
                  </div>
                </t>
              </div>

              <!-- Add component -->
              <div class="o_smrp_box" style="margin-top:16px;">
                <div class="o_smrp_name">Agregar ingrediente</div>
                <div class="o_smrp_row">
                  <input class="o_smrp_input" type="text" placeholder="Buscar ingrediente..."
                         t-model="state.compSearchQuery" t-on-input="() => this.searchComponents()"/>
                  <input class="o_smrp_input" type="number" min="0" step="0.01" style="max-width:120px;"
                         t-model="state.newCompQty" placeholder="Cant."/>
                </div>
                <div class="o_smrp_cards o_smrp_cards--center" t-if="state.compSearchResults.length">
                  <t t-foreach="state.compSearchResults" t-as="p" t-key="p.id">
                    <div class="o_smrp_card selectable" t-on-click="() => this.addComponentFromSearch(p)">
                      <div class="o_smrp_card_icon">➕</div>
                      <div class="o_smrp_card_title"><t t-esc="p.name"/></div>
                      <div class="o_smrp_card_sub"><t t-esc="p.uom_name"/></div>
                    </div>
                  </t>
                </div>
              </div>

              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.backToProduct()">← Volver</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl"
                        t-att-disabled="!state.components.length"
                        t-on-click="() => this.continueFromComponents()">Subproductos →</button>
              </div>
            </div>
          </t>

          <!-- ══════════ PASO BYPRODUCTS ══════════ -->
          <t t-if="state.step === 'byproducts'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Subproductos obtenidos</h2>
              <div class="o_smrp_box">
                <div class="o_smrp_meta">Si esta produccion genera subproductos, registralos aqui. Si no aplica, continua al siguiente paso.</div>

                <div class="o_smrp_bp_list" t-if="state.byproducts.length">
                  <t t-foreach="state.byproducts" t-as="bp" t-key="bp.product_id">
                    <div class="o_smrp_bp_row">
                      <span class="name"><t t-esc="bp.name"/></span>
                      <span class="uom"><t t-esc="bp.uom_name"/></span>
                      <input type="number" min="0" step="0.01" class="o_smrp_input_small" style="width:100px;"
                             t-att-value="bp.qty"
                             t-on-input="(ev) => this.updateBpQty(bp_index, ev)"/>
                      <button class="o_smrp_btn o_smrp_btn--sm o_smrp_btn--danger"
                              t-on-click="() => this.removeByproduct(bp_index)">✕</button>
                    </div>
                  </t>
                </div>

                <div style="margin-top:12px;">
                  <div class="o_smrp_row">
                    <input class="o_smrp_input" type="text" placeholder="Buscar subproducto..."
                           t-model="state.bpSearchQuery" t-on-input="() => this.searchByproducts()"/>
                    <input class="o_smrp_input" type="number" min="0" step="0.01" style="max-width:120px;"
                           t-model="state.newBpQty" placeholder="Cant."/>
                  </div>
                  <div class="o_smrp_cards o_smrp_cards--center" t-if="state.bpSearchResults.length" style="margin-top:10px;">
                    <t t-foreach="state.bpSearchResults" t-as="p" t-key="p.id">
                      <div class="o_smrp_card selectable" t-on-click="() => this.addByproductFromSearch(p)">
                        <div class="o_smrp_card_icon">➕</div>
                        <div class="o_smrp_card_title"><t t-esc="p.name"/></div>
                        <div class="o_smrp_card_sub"><t t-esc="p.uom_name"/></div>
                      </div>
                    </t>
                  </div>
                </div>
              </div>
              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.backToComponents()">← Ingredientes</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.continueFromByproducts()">Lotes →</button>
              </div>
            </div>
          </t>

          <!-- ══════════ PASO LOTS ══════════ -->
          <t t-if="state.step === 'lots'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Distribucion de Lotes</h2>
              <t t-if="state.components.length">
                <div class="o_smrp_wizard">
                  <t t-set="c" t-value="state.components[state.compIndex]"/>
                  <div class="o_smrp_counter">Ingrediente <t t-esc="state.compIndex + 1"/> de <t t-esc="state.components.length"/></div>

                  <div class="o_smrp_box o_smrp_box--big">
                    <div class="o_smrp_lot_target">
                      <span class="target-label">Distribuir:</span>
                      <span class="target-value"><t t-esc="(c.qty_real || 0).toFixed(2)"/></span>
                      <span class="target-uom"><t t-esc="c.uom_name"/> de <strong><t t-esc="c.name"/></strong></span>
                    </div>

                    <div class="o_smrp_header_flex">
                      <div class="o_smrp_name" style="font-size:14px;"><t t-esc="c.name"/></div>
                      <div class="o_smrp_progress_text">
                        <t t-set="assigned" t-value="this.getAssignedTotal(c.product_id)"/>
                        Asignado: <strong><t t-esc="assigned.toFixed(2)"/></strong> / <t t-esc="(c.qty_real || 0).toFixed(2)"/> <t t-esc="c.uom_name"/>
                      </div>
                    </div>
                    <div class="o_smrp_progress_bar">
                      <t t-set="assigned" t-value="this.getAssignedTotal(c.product_id)"/>
                      <div class="o_smrp_progress_fill"
                           t-att-style="'width:' + Math.min((assigned / (c.qty_real || 1))*100, 100) + '%'"/>
                    </div>

                    <div class="o_smrp_lot_status_bar" t-att-class="'o_smrp_lot_status_bar--' + this.getLotStatusClass(c.product_id)">
                      <span><t t-esc="this.getLotStatusMessage(c.product_id)"/></span>
                    </div>

                    <div class="o_smrp_lot_search">
                      <input class="o_smrp_input" type="text" placeholder="Buscar lote..."
                             t-model="state.lotQuery" t-on-input="() => this.searchLots()"/>
                    </div>
                    <div class="o_smrp_lots">
                      <t t-foreach="state.lots" t-as="l" t-key="l.id">
                        <div class="o_smrp_lot_input_card" t-att-class="{'active': this.getLotAssignedValue(c.product_id, l.id) > 0}">
                          <div class="head">
                            <div class="name"><t t-esc="l.name"/></div>
                            <div class="avail">Disp: <t t-esc="l.qty_available"/></div>
                          </div>
                          <div class="lot-actions">
                            <label>Usar:</label>
                            <input type="number" min="0" step="0.001" class="o_smrp_input_small"
                                   t-att-value="this.getLotAssignedValue(c.product_id, l.id)"
                                   t-on-change="(ev) => this.updateLotAssignment(l.id, ev.target.value)"/>
                            <button class="o_smrp_btn o_smrp_btn--fill" title="Llenar con lo que falta"
                                    t-on-click="() => this.fillRemainingLot(l.id)">Restante</button>
                          </div>
                        </div>
                      </t>
                      <t t-if="!state.lots.length">
                        <div class="o_smrp_empty o_smrp_empty--block">Sin lotes disponibles.</div>
                      </t>
                    </div>
                  </div>
                  <div class="o_smrp_actions o_smrp_actions--sticky">
                    <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.prevLotStep()">
                      <t t-if="state.compIndex === 0">← Subproductos</t>
                      <t t-else="">← Anterior</t>
                    </button>
                    <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.nextLotStep()">
                      <t t-if="state.compIndex &lt; state.components.length - 1">Siguiente →</t>
                      <t t-else="">Revisar resumen →</t>
                    </button>
                  </div>
                </div>
              </t>
            </div>
          </t>

          <!-- ══════════ PASO REVIEW ══════════ -->
          <t t-if="state.step === 'review'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Resumen de Produccion</h2>

              <!-- Alerts summary -->
              <t t-if="this.hasRedWarnings">
                <div class="o_smrp_review_alerts o_smrp_review_alerts--red">
                  <div class="o_smrp_review_alert_title">
                    ⛔ Desviaciones criticas detectadas (<t t-esc="this.reviewRedCount"/>)
                  </div>
                  <t t-foreach="state.reviewWarnings.filter(w => w.level === 'red')" t-as="rw" t-key="rw.product_id">
                    <div class="o_smrp_review_alert_item">
                      <span class="icon">⛔</span>
                      <span class="name"><t t-esc="rw.name"/>:</span>
                      <span class="msg"><t t-esc="rw.msg"/></span>
                    </div>
                  </t>
                </div>
              </t>
              <t t-if="this.hasOrangeWarnings">
                <div class="o_smrp_review_alerts o_smrp_review_alerts--orange">
                  <div class="o_smrp_review_alert_title">
                    ⚠ Desviaciones importantes (<t t-esc="this.reviewOrangeCount"/>)
                  </div>
                  <t t-foreach="state.reviewWarnings.filter(w => w.level === 'orange')" t-as="ow" t-key="ow.product_id">
                    <div class="o_smrp_review_alert_item">
                      <span class="icon">⚠</span>
                      <span class="name"><t t-esc="ow.name"/>:</span>
                      <span class="msg"><t t-esc="ow.msg"/></span>
                    </div>
                  </t>
                </div>
              </t>
              <t t-if="this.reviewYellowCount > 0">
                <div class="o_smrp_review_alerts o_smrp_review_alerts--yellow">
                  <div class="o_smrp_review_alert_title">
                    ⚠ Desviaciones leves (<t t-esc="this.reviewYellowCount"/>)
                  </div>
                </div>
              </t>
              <t t-if="!state.reviewWarnings.length">
                <div class="o_smrp_review_alerts o_smrp_review_alerts--clean">
                  <div class="o_smrp_review_alert_title">
                    ✓ Sin desviaciones — todo dentro de tolerancia
                  </div>
                </div>
              </t>

              <!-- General info -->
              <div class="o_smrp_review_grid" style="grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); display: grid; gap: 12px;">
                <div class="o_smrp_review_card">
                  <div class="title">Almacen</div>
                  <div class="value"><t t-esc="this.getWarehouseName()"/></div>
                </div>
                <div class="o_smrp_review_card">
                  <div class="title">Producto</div>
                  <div class="value"><t t-esc="state.productName"/></div>
                </div>
                <div class="o_smrp_review_card">
                  <div class="title">Cantidad</div>
                  <div class="value"><t t-esc="state.qty"/> <t t-esc="state.uomName"/></div>
                </div>
                <div class="o_smrp_review_card">
                  <div class="title">BOM</div>
                  <div class="value">
                    <t t-if="state.bomExists">Formula existente</t>
                    <t t-else="">Se creara nueva</t>
                  </div>
                </div>
              </div>

              <!-- Components summary -->
              <div class="o_smrp_box" style="margin-top:14px;">
                <div class="o_smrp_name">Ingredientes</div>
                <div class="o_smrp_review_comp_summary">
                  <div class="o_smrp_review_comp_header">
                    <div>Ingrediente</div>
                    <div>Formula</div>
                    <div>Real</div>
                    <div>Diferencia</div>
                    <div>Estado</div>
                  </div>
                  <t t-foreach="state.components" t-as="comp" t-key="comp.product_id">
                    <t t-set="w" t-value="this.getComponentWarning(comp)"/>
                    <div class="o_smrp_review_comp_line" t-att-class="'o_smrp_review_comp_line--' + w.level">
                      <div class="name"><t t-esc="comp.name"/></div>
                      <div class="num"><t t-esc="(comp.qty_formula || 0).toFixed(2)"/></div>
                      <div class="num"><t t-esc="(comp.qty_real || 0).toFixed(2)"/></div>
                      <div class="diff">
                        <t t-set="d" t-value="(comp.qty_real || 0) - (comp.qty_formula || 0)"/>
                        <t t-if="d > 0">+</t><t t-esc="d.toFixed(2)"/>
                      </div>
                      <div><span class="o_smrp_comp_status" t-att-class="'o_smrp_comp_status--' + w.level"><t t-esc="w.icon"/> <t t-esc="w.msg"/></span></div>
                    </div>
                  </t>
                </div>
              </div>

              <!-- Byproducts summary -->
              <t t-if="state.byproducts.length">
                <div class="o_smrp_box" style="margin-top:14px;">
                  <div class="o_smrp_name">Subproductos</div>
                  <t t-foreach="state.byproducts" t-as="bp" t-key="bp.product_id">
                    <div class="o_smrp_bp_row">
                      <span class="name"><t t-esc="bp.name"/></span>
                      <span><t t-esc="bp.qty"/> <t t-esc="bp.uom_name"/></span>
                    </div>
                  </t>
                </div>
              </t>

              <!-- Actions -->
              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.backToComponentsFromReview()">← Editar ingredientes</button>
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.backToLots()">← Editar lotes</button>
                <button class="o_smrp_btn o_smrp_btn--black o_smrp_btn--xl"
                        t-att-disabled="state.submitting || (this.hasRedWarnings &amp;&amp; !state.allowConfirmRed)"
                        t-on-click="() => this.createMO()">
                  <t t-if="state.submitting">Creando...</t>
                  <t t-elif="this.hasRedWarnings">⛔ Confirmar con alertas</t>
                  <t t-else="">✓ CONFIRMAR PRODUCCION</t>
                </button>
              </div>

              <t t-if="this.hasRedWarnings and state.allowConfirmRed">
                <div style="text-align:center; margin-top:8px; font-size:13px; color: var(--pk-red); font-weight:700;">
                  Detectamos desviaciones criticas. Revisa antes de continuar. Si estas seguro, puedes confirmar.
                </div>
              </t>
            </div>
          </t>

          <!-- ══════════ DONE ══════════ -->
          <t t-if="state.step === 'done'">
            <div class="o_smrp_container o_smrp_section o_smrp_done">

              <!-- ─── Caso exitoso: MO completada ─── -->
              <t t-if="!state.needsForceValidate">
                <div class="o_smrp_done_icon">🎉</div>
                <h2>Orden creada y validada!</h2>
                <div class="o_smrp_box">
                  <div>Orden: <strong><t t-esc="state.resultMoName"/></strong></div>
                  <div style="margin-top:6px;">Estado: <span class="o_smrp_list_badge success">Hecha</span></div>
                  <t t-if="state.bomMessage === 'bom_created'">
                    <div style="margin-top:8px; padding:8px 12px; background:#fef9c3; border-radius:8px; font-size:14px;">
                      ⚡ Se creo una nueva lista de materiales para este producto.
                    </div>
                  </t>
                  <t t-if="state.bomMessage === 'bom_existing'">
                    <div style="margin-top:8px; padding:8px 12px; background:#dbeafe; border-radius:8px; font-size:14px;">
                      ✓ Se uso la lista de materiales existente.
                    </div>
                  </t>
                  <div style="margin-top:8px;">La produccion ha sido registrada correctamente.</div>
                </div>
              </t>

              <!-- ─── Caso con problema: MO creada pero NO completada ─── -->
              <t t-if="state.needsForceValidate">
                <div class="o_smrp_done_icon">⚠️</div>
                <h2>Orden creada — pendiente de validar</h2>
                <div class="o_smrp_box" style="border: 3px solid var(--pk-orange); background: var(--pk-orange-bg);">
                  <div style="font-size:16px; font-weight:800; color: var(--pk-orange-text); margin-bottom:8px;">
                    ⚠ La orden se creo pero no se pudo marcar como "Hecha" automaticamente
                  </div>
                  <div>Orden: <strong><t t-esc="state.resultMoName"/></strong></div>
                  <div style="margin-top:4px;">
                    Estado actual: <span class="o_smrp_list_badge warning"><t t-esc="state.resultMoState"/></span>
                  </div>
                  <t t-if="state.completionError">
                    <div style="margin-top:8px; padding:10px 14px; background:rgba(255,255,255,0.7); border-radius:8px; font-size:13px; color:#333;">
                      <strong>Detalle tecnico:</strong> <t t-esc="state.completionError"/>
                    </div>
                  </t>
                  <div style="margin-top:12px; font-size:14px; color: var(--pk-orange-text);">
                    Presiona el boton para intentar forzar la validacion. Si persiste el problema, abre la orden en Odoo y validala manualmente.
                  </div>
                </div>

                <!-- Botón prominente de forzar validación -->
                <div class="o_smrp_actions o_smrp_actions--center" style="margin-top:16px;">
                  <button class="o_smrp_btn o_smrp_btn--black o_smrp_btn--xl"
                          t-att-disabled="state.forceValidating"
                          t-on-click="() => this.forceValidateMO()"
                          style="min-width:280px; font-size:16px; height:56px;">
                    <t t-if="state.forceValidating">⏳ Validando...</t>
                    <t t-else="">🔄 FORZAR VALIDACION</t>
                  </button>
                </div>
              </t>

              <!-- Acciones comunes -->
              <div class="o_smrp_actions o_smrp_actions--center" style="margin-top:16px;">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.openMO()">Ver orden en Odoo</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.resetWizard()">Crear nueva orden</button>
              </div>
            </div>
          </t>
        </t>

        <!-- ══════════ LISTA ══════════ -->
        <t t-if="state.view === 'list'">
          <div class="o_smrp_container o_smrp_section">
            <h2>Mis ordenes</h2>
            <div class="o_smrp_list">
              <t t-foreach="state.myProductions" t-as="mo" t-key="mo.id">
                <div class="o_smrp_list_item" t-on-click="() => this.loadMoDetail(mo.id)">
                  <div class="o_smrp_list_icon">📦</div>
                  <div class="o_smrp_list_content">
                    <div class="o_smrp_list_title"><t t-esc="mo.name"/></div>
                    <div class="o_smrp_list_meta"><t t-esc="mo.product_name"/> (<t t-esc="mo.product_qty"/> <t t-esc="mo.uom_name"/>)</div>
                  </div>
                  <div class="o_smrp_list_badge" t-att-class="this.getStateClass(mo.state)"><t t-esc="this.getStateLabel(mo.state)"/></div>
                </div>
              </t>
            </div>
            <div class="o_smrp_empty" t-if="!state.myProductions.length">No tienes ordenes.</div>
          </div>
        </t>

        <!-- ══════════ DETALLE ══════════ -->
        <t t-if="state.view === 'detail' and state.moDetail">
          <div class="o_smrp_container o_smrp_section">
            <div class="o_smrp_actions o_smrp_actions--center">
              <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.backToList()">← Volver a lista</button>
            </div>
            <h2><t t-esc="state.moDetail.name"/></h2>
            <div class="o_smrp_box o_smrp_box--big">
              <div class="o_smrp_detail_row"><span class="o_smrp_detail_label">Origen:</span><strong><t t-esc="state.moDetail.origin || 'N/A'"/></strong></div>
              <div class="o_smrp_detail_row">
                <span class="o_smrp_detail_label">Estado:</span>
                <span class="o_smrp_list_badge" t-att-class="this.getStateClass(state.moDetail.state)"><t t-esc="this.getStateLabel(state.moDetail.state)"/></span>
              </div>
              <div class="o_smrp_detail_row"><span class="o_smrp_detail_label">Producto:</span><strong><t t-esc="state.moDetail.product_name"/></strong></div>
              <div class="o_smrp_detail_row">
                <span class="o_smrp_detail_label">Cantidad:</span>
                <strong><t t-esc="state.moDetail.product_qty"/></strong> <t t-esc="state.moDetail.uom_name"/>
              </div>
              <div class="o_smrp_detail_row" t-if="state.moDetail.finished_lot">
                <span class="o_smrp_detail_label">Lote Final:</span><t t-esc="state.moDetail.finished_lot"/>
              </div>
            </div>
            <h2>Ingredientes Utilizados</h2>
            <div class="o_smrp_list">
              <t t-foreach="state.moDetail.components" t-as="comp" t-key="comp_index">
                <div class="o_smrp_list_item">
                  <div class="o_smrp_list_content">
                    <div class="o_smrp_list_title"><t t-esc="comp.product_name"/></div>
                    <div class="o_smrp_list_meta">
                      Req: <strong><t t-esc="comp.qty_required"/></strong> | Usado: <strong><t t-esc="comp.qty_done"/></strong>
                      <br/>Lotes: <span style="color:#2737BE;"><t t-esc="comp.lot_name"/></span>
                    </div>
                  </div>
                </div>
              </t>
            </div>
          </div>
        </t>

      </div>
    </div>
  </t>
</templates>```

## ./views/client_action.xml
```xml
<odoo>
  <data>
    <record id="action_simplified_mrp" model="ir.actions.client">
      <field name="name">Produccion simple</field>
      <field name="tag">aq_simplified_mrp.client_action</field>
      <field name="target">current</field>
    </record>
  </data>
</odoo>```

## ./views/menu.xml
```xml
<odoo>
  <data>
    <menuitem id="menu_simplified_mrp_root"
              name="Carga Produccion"
              parent="mrp.menu_mrp_root"
              sequence="5"
              action="action_simplified_mrp"
              groups="aq_simplified_mrp.group_simplified_mrp_user"/>
  </data>
</odoo>```

## ./views/res_config_settings_view.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
  <data>
    <record id="res_config_settings_view_simplified_mrp" model="ir.ui.view">
      <field name="name">res.config.settings.view.simplified.mrp</field>
      <field name="model">res.config.settings</field>
      <field name="inherit_id" ref="mrp.res_config_settings_view_form"/>
      <field name="arch" type="xml">
        <xpath expr="//form" position="inside">
          <app string="Produccion Simplificada" name="aq_simplified_mrp" groups="base.group_system">
            <block title="Lotes de Producto Terminado">
              <setting
                string="Generacion automatica de lotes"
                help="Activo: el sistema crea el lote al confirmar la orden. Desactivado: el operador captura manualmente.">
                <field name="simplified_mrp_auto_lot"/>
              </setting>
            </block>
            <block title="Poka-Yoke: Tolerancias de desviacion">
              <setting string="Tolerancia verde (%)">
                <field name="smrp_tolerance_green"/>
              </setting>
              <setting string="Tolerancia amarilla (%)">
                <field name="smrp_tolerance_yellow"/>
              </setting>
              <setting string="Tolerancia naranja (%)">
                <field name="smrp_tolerance_orange"/>
              </setting>
              <setting string="Permitir confirmar con alertas rojas">
                <field name="smrp_allow_confirm_red"/>
              </setting>
            </block>
            <block title="Lista de Materiales">
              <setting string="Crear BOM automaticamente si no existe">
                <field name="smrp_auto_create_bom"/>
              </setting>
            </block>
            <block title="Persistencia">
              <setting string="Autoguardado activo">
                <field name="smrp_autosave"/>
              </setting>
            </block>
          </app>
        </xpath>
      </field>
    </record>
  </data>
</odoo>```

