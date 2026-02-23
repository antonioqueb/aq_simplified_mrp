## ./__init__.py
```py
from . import models
```

## ./__manifest__.py
```py
# -*- coding: utf-8 -*-
{
    'name': 'AQ Simplified MRP',
    'version': '18.0.1.1.0',
    'summary': 'UI paso a paso para crear Órdenes de Producción',
    'category': 'Manufacturing',
    'author': 'Alphaqueb Consulting SAS',
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
from . import res_config_settings
```

## ./models/res_config_settings.py
```py
# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    simplified_mrp_auto_lot = fields.Boolean(
        string='Generar lote del producto terminado automáticamente',
        help=(
            'Activo: el sistema genera el lote con el patrón XX-##-##-##-## de forma automática.\n'
            'Desactivado: el operador captura los segmentos del lote manualmente al crear la orden.'
        ),
        config_parameter='aq_simplified_mrp.auto_lot',
    )```

## ./models/simplified_mrp_api.py
```py
# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
from datetime import datetime
import re

_logger = logging.getLogger(__name__)

DEBUG_SMRP = True

# Patrón de lote: XX-##-##-##-## (2 letras, 4 grupos de 2 dígitos)
LOT_PATTERN = re.compile(r'^[A-Za-z]{2}-\d{2}-\d{2}-\d{2}-\d{2}$')


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
        _logger.warning("SMRPDBG: %s", " | ".join(str(p) for p in parts))

    # ---------- Config ----------
    @api.model
    def get_mrp_config(self):
        """Devuelve la configuración relevante para el front."""
        param = self.env['ir.config_parameter'].sudo()
        raw = param.get_param('aq_simplified_mrp.auto_lot', default='True')
        auto_lot = str(raw).strip() in ('True', '1', 'true')
        return {'auto_lot': auto_lot}

    # ---------- Helpers ----------
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

    # ---------- Data sources ----------
    @api.model
    def get_warehouses(self):
        try:
            ws = self.env['stock.warehouse'].search([])
            return [{'id': w.id, 'name': w.name, 'code': w.code} for w in ws]
        except Exception as e:
            raise UserError(_('Error obteniendo almacenes: %s') % e)

    @api.model
    def get_sale_orders(self, query='', limit=20):
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
        try:
            wh = self.env['stock.warehouse'].browse(int(warehouse_id))
            if not wh.exists():
                return []
            locs = self.env['stock.location'].search([
                ('usage', '=', 'internal'),
                ('location_id', 'child_of', wh.view_location_id.id),
            ], order='name asc')
            return [{'id': l.id, 'name': l.display_name} for l in locs]
        except Exception as e:
            _logger.error("Error getting locations: %s", e)
            return []

    @api.model
    def get_finished_products(self, query='', limit=20, **kwargs):
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
            raise UserError(_('Error obteniendo componentes BOM: %s') % e)

    @api.model
    def get_lots(self, product_id, warehouse_id, limit=60, query=''):
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
        except Exception as e:
            _logger.error("Error getting lots: %s", e, exc_info=True)
            raise UserError(_('Error obteniendo lotes: %s') % e)

    # ---------- Validar lote manual ----------
    @api.model
    def validate_manual_lot(self, product_id, lot_name):
        """Valida que el nombre de lote cumpla el patrón XX-##-##-##-## y no exista ya."""
        lot_name = (lot_name or '').strip().upper()
        if not LOT_PATTERN.match(lot_name):
            raise UserError(_(
                'El lote "%(n)s" no cumple el patrón requerido XX-##-##-##-## '
                '(ej. AB-01-01-01-01).', n=lot_name
            ))
        existing = self.env['stock.lot'].search([
            ('name', '=', lot_name),
            ('product_id', '=', int(product_id)),
        ], limit=1)
        if existing:
            raise UserError(_('El lote "%s" ya existe para este producto.') % lot_name)
        return True

    # ---------- Create MO ----------
    @api.model
    def create_mo(self, payload):
        try:
            warehouse_id    = payload.get('warehouse_id')
            product_id      = payload.get('product_id')
            product_qty     = payload.get('product_qty', 1.0)
            bom_id          = payload.get('bom_id')
            components_map  = payload.get('components') or []
            origin_ref      = payload.get('origin') or 'Simplified UI'
            custom_dest_loc = payload.get('location_dest_id')
            manual_lot_name = payload.get('manual_lot_name') or None  # None → auto

            # Limpiar componentes
            comps_clean = []
            for c in components_map:
                if not c:
                    continue
                pid       = int(c.get('product_id')) if c.get('product_id') else False
                total_qty = float(c.get('qty', 0.0))
                lots_data = c.get('selected_lots', [])
                if pid and total_qty > 0:
                    comps_clean.append({'product_id': pid, 'qty': total_qty, 'lots': lots_data})

            self._logdbg("create_mo", f"wh={warehouse_id}", f"prod={product_id}",
                         f"qty={product_qty}", f"manual_lot={manual_lot_name}")

            if not warehouse_id or not product_id:
                raise UserError(_('Faltan datos obligatorios'))
            if not comps_clean:
                raise UserError(_('Debes capturar al menos un ingrediente con cantidad mayor a cero.'))

            wh      = self.env['stock.warehouse'].browse(int(warehouse_id))
            product = self.env['product.product'].browse(int(product_id))
            qty     = float(product_qty)
            if not wh.exists():
                raise UserError(_('Almacén inválido'))
            if not product.exists():
                raise UserError(_('Producto inválido'))

            pt = self._find_picking_type(wh)
            if not pt:
                raise UserError(_('No hay tipo de operación de fabricación configurado'))

            if not bom_id:
                bom    = self._find_bom(product)
                bom_id = bom.id if bom else False

            mo_vals = {
                'product_id':     product.id,
                'product_qty':    qty,
                'product_uom_id': product.uom_id.id,
                'bom_id':         bom_id or False,
                'picking_type_id':pt.id,
                'origin':         origin_ref,
            }
            if custom_dest_loc:
                mo_vals['location_dest_id'] = int(custom_dest_loc)

            mo = self.env['mrp.production'].create(mo_vals)
            self._logdbg("MO creado", mo.id, mo.name)

            # ============== LOTE PRODUCTO TERMINADO ==============
            finished_lot = None
            if product.tracking in ['lot', 'serial']:
                Lot = self.env['stock.lot']

                if manual_lot_name:
                    # --- Lote manual: validar patrón y unicidad ---
                    lot_name = manual_lot_name.strip().upper()
                    if not LOT_PATTERN.match(lot_name):
                        raise UserError(_(
                            'El lote "%(n)s" no cumple el patrón XX-##-##-##-##.', n=lot_name
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
                    # --- Lote automático (comportamiento original) ---
                    date_str = datetime.now().strftime('%Y%m%d')
                    ref      = product.default_code or 'PROD'
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

            # ============== COMPONENTES Y LOTES ==============
            existing_by_pid = {m.product_id.id: m for m in mo.move_raw_ids}

            for item in comps_clean:
                pid              = item['product_id']
                req_qty_total    = item['qty']
                lots_distribution = item['lots']
                prod = self.env['product.product'].browse(pid)
                move = existing_by_pid.get(pid)

                if move:
                    move.product_uom_qty = req_qty_total
                else:
                    move = self.env['stock.move'].create({
                        'name':                    prod.display_name,
                        'product_id':              pid,
                        'product_uom_qty':         req_qty_total,
                        'product_uom':             prod.uom_id.id,
                        'raw_material_production_id': mo.id,
                        'company_id':              mo.company_id.id,
                        'location_id':             mo.location_src_id.id,
                        'location_dest_id':        mo.location_dest_id.id,
                    })
                    existing_by_pid[pid] = move

                if move.move_line_ids:
                    move.move_line_ids.unlink()

                if not lots_distribution:
                    self.env['stock.move.line'].create({
                        'move_id':        move.id,
                        'product_id':     pid,
                        'product_uom_id': prod.uom_id.id,
                        'location_id':    move.location_id.id,
                        'location_dest_id': move.location_dest_id.id,
                        'quantity':       req_qty_total,
                    })
                else:
                    for l_data in lots_distribution:
                        l_id  = l_data.get('lot_id')
                        l_qty = float(l_data.get('qty', 0.0))
                        if l_qty <= 0:
                            continue
                        real_lot_id = l_id if (l_id and l_id != -1) else False
                        self.env['stock.move.line'].create({
                            'move_id':        move.id,
                            'product_id':     pid,
                            'product_uom_id': prod.uom_id.id,
                            'location_id':    move.location_id.id,
                            'location_dest_id': move.location_dest_id.id,
                            'lot_id':         real_lot_id,
                            'quantity':       l_qty,
                        })

            try:
                mo.action_assign()
            except Exception as e:
                _logger.warning("Auto assign warning: %s", e)

            # ============== COMPLETAR MO ==============
            if mo.move_finished_ids:
                finished_move = mo.move_finished_ids[0]
                if finished_move.move_line_ids:
                    for ml in finished_move.move_line_ids:
                        if finished_lot:
                            ml.lot_id = finished_lot.id
                        ml.quantity = qty
                else:
                    self.env['stock.move.line'].create({
                        'move_id':        finished_move.id,
                        'product_id':     product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id':    mo.location_src_id.id,
                        'location_dest_id': mo.location_dest_id.id,
                        'lot_id':         finished_lot.id if finished_lot else False,
                        'quantity':       qty,
                    })

            try:
                mo.action_toggle_is_locked()
                mo.is_locked = False
                mo.button_mark_done()
                self._logdbg("MO hecha", mo.state)
            except Exception as complete_error:
                self._logdbg("Fallback al completar MO", complete_error)
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
        try:
            mos = self.env['mrp.production'].search(
                [('user_id', '=', self.env.uid)],
                limit=int(limit), order='date_start desc, id desc',
            )
            return [{
                'id':           mo.id,
                'name':         mo.name,
                'state':        mo.state,
                'product_id':   mo.product_id.id,
                'product_name': mo.product_id.display_name,
                'product_qty':  mo.product_qty,
                'uom_name':     mo.product_uom_id.name,
                'date_start':   mo.date_start.isoformat() if mo.date_start else False,
                'date_finished':mo.date_finished.isoformat() if mo.date_finished else False,
            } for mo in mos]
        except Exception as e:
            raise UserError(_('Error obteniendo mis órdenes: %s') % e)

    @api.model
    def get_production_detail(self, mo_id):
        try:
            mo = self.env['mrp.production'].browse(int(mo_id))
            if not mo.exists():
                raise UserError(_('Orden no encontrada'))
            if mo.user_id.id != self.env.uid:
                raise UserError(_('No tienes permiso para ver esta orden'))
            components = []
            for move in mo.move_raw_ids:
                lots_info = []
                qty_done  = 0.0
                for ml in move.move_line_ids:
                    qty_done += ml.quantity
                    lname = ml.lot_id.name if ml.lot_id else 'General'
                    lots_info.append(f"{lname} ({ml.quantity})")
                components.append({
                    'product_name': move.product_id.display_name,
                    'qty_required': move.product_uom_qty,
                    'qty_done':     qty_done,
                    'uom_name':     move.product_uom.name,
                    'lot_name':     ", ".join(lots_info) if lots_info else "Sin consumo",
                })
            finished_lot = False
            if mo.move_finished_ids and mo.move_finished_ids[0].move_line_ids:
                finished_lot = mo.move_finished_ids[0].move_line_ids[0].lot_id.display_name or False
            return {
                'name':          mo.name,
                'state':         mo.state,
                'origin':        mo.origin,
                'product_name':  mo.product_id.display_name,
                'product_qty':   mo.product_qty,
                'uom_name':      mo.product_uom_id.name,
                'date_start':    mo.date_start.isoformat() if mo.date_start else False,
                'date_finished': mo.date_finished.isoformat() if mo.date_finished else False,
                'finished_lot':  finished_lot or 'Sin lote',
                'components':    components,
            }
        except Exception as e:
            raise UserError(_('Error obteniendo detalle: %s') % e)```

## ./security/security.xml
```xml
<odoo>
  <data noupdate="1">
    <record id="group_simplified_mrp_user" model="res.groups">
      <field name="name">Simplified MRP</field>
      <field name="category_id" ref="base.module_category_manufacturing"/>
      <field name="implied_ids" eval="[(4, ref('mrp.group_mrp_user'))]"/>
    </record>
  </data>
</odoo>
```

## ./static/src/js/simplified_mrp_client_action.js
```js
/** @odoo-module **/
import { registry } from '@web/core/registry';
import { Component, useState, onWillStart } from '@odoo/owl';
import { useService } from '@web/core/utils/hooks';

// Patrón: XX-##-##-##-## (2 letras + 4 grupos de 2 dígitos)
// Ej: AB-01-01-01-01

class SimplifiedMrp extends Component {
    static props = { "*": true };

    setup() {
        this.orm          = useService('orm');
        this.action       = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            view: 'create',
            step: 'warehouse',   // warehouse | product | lot_config | components | lots | done

            // Config del backend
            autoLot: true,

            // Step 1
            warehouses:  [],
            warehouseId: null,

            // Step 2: Producto
            products:         [],
            productQuery:     '',
            productId:        null,
            productName:      '',
            uomName:          '',
            productTracking:  'none',
            qty:              1.0,

            saleOrderQuery:   '',
            saleOrderResults: [],
            selectedSaleOrder: null,

            destLocations:        [],
            selectedDestLocation: null,

            // Step lot_config: 5 segmentos  XX - ## - ## - ## - ##
            lotSeg1: '',   // 2 letras  (XX)
            lotSeg2: '',   // 2 dígitos (##)
            lotSeg3: '',   // 2 dígitos (##)
            lotSeg4: '',   // 2 dígitos (##)
            lotSeg5: '',   // 2 dígitos (##)
            lotPreview: '',
            lotSegErrors: { s1: false, s2: false, s3: false, s4: false, s5: false },

            // Step 3: Componentes
            bomId:            null,
            components:       [],
            compIndex:        0,
            editingComponent: false,
            compSearchQuery:  '',
            compSearchResults:[],
            newCompQty:       1.0,

            // Step 4: Lotes componentes
            lots:         [],
            lotQuery:     '',
            assignedLots: {},

            // Resultado
            resultMoId:   null,
            resultMoName: '',

            // Lista / Detalle
            myProductions: [],
            selectedMo:    null,
            moDetail:      null,
        });

        onWillStart(async () => {
            await this.loadConfig();
            await this.loadWarehouses();
            await this.loadMyProductions();
        });
    }

    // ─── Utilidades ──────────────────────────────────────────────────────────
    toNum(v) {
        const n = typeof v === 'number' ? v : parseFloat(v);
        return Number.isFinite(n) ? n : 0;
    }

    // ─── Segmentos del lote ───────────────────────────────────────────────────
    _padDigits(v) {
        const n = v.replace(/\D/g, '').slice(0, 2);
        return n.padStart(2, '0');
    }

    _computePreview() {
        const { lotSeg1, lotSeg2, lotSeg3, lotSeg4, lotSeg5 } = this.state;
        const s1 = (lotSeg1 || '__').padEnd(2, '_').slice(0, 2).toUpperCase();
        const s2 = (lotSeg2 || '00').padStart(2, '0').slice(0, 2);
        const s3 = (lotSeg3 || '00').padStart(2, '0').slice(0, 2);
        const s4 = (lotSeg4 || '00').padStart(2, '0').slice(0, 2);
        const s5 = (lotSeg5 || '00').padStart(2, '0').slice(0, 2);
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

    // Handlers de los 5 inputs
    onSeg1Change(ev) {
        this.state.lotSeg1 = ev.target.value.replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 2);
        this.state.lotSegErrors.s1 = false;
        this._computePreview();
        // Avanzar foco automáticamente al completar
        if (this.state.lotSeg1.length === 2) {
            ev.target.closest('.o_smrp_lot_builder').querySelector('[data-seg="2"]')?.focus();
        }
    }

    onSeg2Change(ev) {
        this.state.lotSeg2 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s2 = false;
        this._computePreview();
        if (this.state.lotSeg2.length === 2) {
            ev.target.closest('.o_smrp_lot_builder').querySelector('[data-seg="3"]')?.focus();
        }
    }

    onSeg3Change(ev) {
        this.state.lotSeg3 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s3 = false;
        this._computePreview();
        if (this.state.lotSeg3.length === 2) {
            ev.target.closest('.o_smrp_lot_builder').querySelector('[data-seg="4"]')?.focus();
        }
    }

    onSeg4Change(ev) {
        this.state.lotSeg4 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s4 = false;
        this._computePreview();
        if (this.state.lotSeg4.length === 2) {
            ev.target.closest('.o_smrp_lot_builder').querySelector('[data-seg="5"]')?.focus();
        }
    }

    onSeg5Change(ev) {
        this.state.lotSeg5 = ev.target.value.replace(/\D/g, '').slice(0, 2);
        this.state.lotSegErrors.s5 = false;
        this._computePreview();
    }

    // ─── Carga de config ──────────────────────────────────────────────────────
    async loadConfig() {
        try {
            const cfg = await this.orm.call('aq.simplified.mrp.api', 'get_mrp_config', [], {});
            this.state.autoLot = cfg.auto_lot !== false;
        } catch (e) {
            console.warn('No se pudo cargar config MRP, asumiendo autoLot=true', e);
            this.state.autoLot = true;
        }
    }

    // ─── Data loaders ─────────────────────────────────────────────────────────
    async loadWarehouses() {
        try {
            this.state.warehouses = await this.orm.call(
                'aq.simplified.mrp.api', 'get_warehouses', [], {}
            );
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
            this.state.myProductions = await this.orm.call(
                'aq.simplified.mrp.api', 'get_my_productions', [50], {}
            );
        } catch (e) { this.notifyError('Error cargando mis órdenes', e); }
    }

    async loadMoDetail(moId) {
        try {
            this.state.moDetail  = await this.orm.call(
                'aq.simplified.mrp.api', 'get_production_detail', [moId], {}
            );
            this.state.selectedMo = moId;
            this.state.view       = 'detail';
        } catch (e) { this.notifyError('Error cargando detalle', e); }
    }

    // ─── Búsquedas ───────────────────────────────────────────────────────────
    async searchProducts() {
        try {
            this.state.products = await this.orm.call(
                'aq.simplified.mrp.api', 'get_finished_products',
                [this.state.productQuery || '', 20], {}
            );
        } catch (e) { this.notifyError('Error buscando productos', e); }
    }

    async searchComponents() {
        try {
            if (!this.state.compSearchQuery) { this.state.compSearchResults = []; return; }
            this.state.compSearchResults = await this.orm.call(
                'aq.simplified.mrp.api', 'search_components',
                [this.state.compSearchQuery, 20], {}
            );
        } catch (e) { this.notifyError('Error buscando ingredientes', e); }
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

    // ─── Step 1: Almacén ─────────────────────────────────────────────────────
    async selectWarehouse(id) {
        this.state.warehouseId = id;
        this.state.step        = 'product';
        await this.loadDestLocations();
    }

    // ─── Step 2: Producto ─────────────────────────────────────────────────────
    selectProduct(p) {
        this.state.productId       = p.id;
        this.state.productName     = p.name;
        this.state.uomName         = p.uom_name || '';
        this.state.productTracking = p.tracking || 'none';
        this.state.products        = [];
    }

    selectSaleOrder(so) {
        this.state.selectedSaleOrder  = so;
        this.state.saleOrderQuery     = so.name;
        this.state.saleOrderResults   = [];
    }

    async confirmProductAndConfig() {
        if (!this.state.productId) {
            this.notification.add('Selecciona un producto', { type: 'warning' }); return;
        }
        const qty = this.toNum(this.state.qty);
        if (qty <= 0) {
            this.notification.add('Cantidad inválida', { type: 'warning' }); return;
        }
        this.state.qty = qty;

        const needsManualLot = !this.state.autoLot && this.state.productTracking !== 'none';
        if (needsManualLot) {
            // Resetear segmentos y mostrar paso de captura
            this.state.lotSeg1 = '';
            this.state.lotSeg2 = '';
            this.state.lotSeg3 = '';
            this.state.lotSeg4 = '';
            this.state.lotSeg5 = '';
            this.state.lotPreview = '__-__-__-__-__';
            this.state.lotSegErrors = { s1: false, s2: false, s3: false, s4: false, s5: false };
            this.state.step = 'lot_config';
            return;
        }
        await this._loadComponents();
    }

    // ─── Step lot_config: Lote Manual ─────────────────────────────────────────
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

    // ─── Componentes ─────────────────────────────────────────────────────────
    async _loadComponents() {
        try {
            const res = await this.orm.call(
                'aq.simplified.mrp.api', 'get_bom_components',
                [this.state.productId, this.state.qty], {}
            );
            this.state.bomId      = res.bom_id || null;
            this.state.components = (res.components || []).map(c => ({
                ...c, qty_required: this.toNum(c.qty_required) || 1.0,
            }));
            this.state.assignedLots  = {};
            this.state.compIndex     = 0;
            this.state.editingComponent = false;
            this.state.step          = 'components';
        } catch (e) { this.notifyError('Error obteniendo componentes', e); }
    }

    updateCurrentQty(ev) {
        const c = this.state.components[this.state.compIndex];
        if (c) c.qty_required = this.toNum(ev.target.value);
    }

    removeCurrentComponent() {
        if (!this.state.components.length) return;
        this.state.components.splice(this.state.compIndex, 1);
        if (!this.state.components.length) {
            this.state.compIndex = 0; this.state.editingComponent = false;
        } else if (this.state.compIndex > 0) {
            this.state.compIndex -= 1; this.state.editingComponent = true;
        } else {
            this.state.editingComponent = true;
        }
    }

    addComponentFromSearch(p) {
        if (!this.state.components.find(c => c.product_id === p.id)) {
            this.state.components.push({
                product_id: p.id, name: p.name, uom_id: p.uom_id, uom_name: p.uom_name,
                qty_required: this.toNum(this.state.newCompQty) || 1.0,
                tracking: p.tracking || 'none',
            });
        }
        this.state.compSearchQuery   = '';
        this.state.compSearchResults = [];
        this.state.newCompQty        = 1.0;
        this.state.compIndex         = this.state.components.length - 1;
        this.state.editingComponent  = true;
    }

    nextComponent() {
        if (!this.state.components.length) {
            this.notification.add('Debes agregar al menos un ingrediente', { type: 'warning' }); return;
        }
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1; this.state.editingComponent = true;
        } else {
            this.state.editingComponent = false;
            this.state.compIndex = this.state.components.length;
        }
    }

    prevComponent() {
        if (this.state.compIndex > 0) {
            this.state.compIndex -= 1; this.state.editingComponent = true;
        }
    }

    backToProduct()    { this.state.step = 'product'; }
    reviewComponents() {
        if (this.state.components.length) {
            this.state.compIndex = 0; this.state.editingComponent = true;
        } else {
            this.notification.add('No hay ingredientes para revisar', { type: 'warning' });
        }
    }

    continueToLots() {
        if (!this.state.components.length) {
            this.notification.add('Debes agregar al menos un ingrediente', { type: 'warning' }); return;
        }
        this.state.compIndex = 0;
        this.state.step      = 'lots';
        this.loadLotsForCurrent();
    }

    // ─── Step 4: Lotes de componentes ────────────────────────────────────────
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

    getLotAssignedValue(productId, lotId) {
        return (this.state.assignedLots[productId] || {})[lotId] || 0;
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

    async nextLotStep() {
        const comp     = this.state.components[this.state.compIndex];
        const assigned = this.getAssignedTotal(comp.product_id);
        if (comp.tracking !== 'none' && assigned <= 0) {
            this.notification.add('No has asignado ninguna cantidad a lotes.', { type: 'danger' }); return;
        }
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1;
            await this.loadLotsForCurrent();
        } else {
            await this.createMO();
        }
    }

    async prevLotStep() {
        if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
            await this.loadLotsForCurrent();
        } else {
            this.state.step = 'components';
            this.state.editingComponent = false;
        }
    }

    // ─── Crear MO ────────────────────────────────────────────────────────────
    async createMO() {
        try {
            const compsPayload = this.state.components.map(c => {
                const lotsMap  = this.state.assignedLots[c.product_id] || {};
                const lotsList = Object.entries(lotsMap).map(([lid, qty]) => ({
                    lot_id: parseInt(lid), qty: this.toNum(qty),
                }));
                return { product_id: c.product_id, qty: c.qty_required, selected_lots: lotsList };
            });

            let originVal = null;
            if (this.state.selectedSaleOrder?.name)
                originVal = this.state.selectedSaleOrder.name;
            else if (this.state.saleOrderQuery)
                originVal = this.state.saleOrderQuery;

            // Lote manual del producto terminado
            let manualLotName = null;
            if (!this.state.autoLot && this.state.productTracking !== 'none') {
                manualLotName = this._assembleLotName();
            }

            const payload = {
                warehouse_id:    this.state.warehouseId,
                product_id:      this.state.productId,
                product_qty:     this.toNum(this.state.qty),
                bom_id:          this.state.bomId,
                origin:          originVal,
                location_dest_id: this.state.selectedDestLocation?.id || null,
                components:      compsPayload,
                manual_lot_name: manualLotName,
            };

            const res = await this.orm.call('aq.simplified.mrp.api', 'create_mo', [payload], {});
            this.state.resultMoId   = res.mo_id || null;
            this.state.resultMoName = res.name  || '';
            this.state.step         = 'done';
            this.notification.add('Orden de producción creada exitosamente', { type: 'success' });
            await this.loadMyProductions();
        } catch (e) {
            this.notifyError('Error creando orden de producción', e);
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
            productTracking: 'none',
            saleOrderQuery: '', saleOrderResults: [], selectedSaleOrder: null,
            selectedDestLocation: null, products: [],
            lotSeg1: '', lotSeg2: '', lotSeg3: '', lotSeg4: '', lotSeg5: '',
            lotPreview: '', lotSegErrors: { s1: false, s2: false, s3: false, s4: false, s5: false },
            components: [], assignedLots: {},
            compIndex: 0, editingComponent: false,
            lotQuery: '', resultMoId: null, resultMoName: '',
            compSearchQuery: '', compSearchResults: [], newCompQty: 1.0,
        });
    }

    // ─── Nav / Misc ───────────────────────────────────────────────────────────
    notifyError(msg, e) {
        console.error(msg, e);
        this.notification.add(`${msg}: ${e.data?.message || e.message || e}`, { type: 'danger' });
    }

    showCreate() { this.resetWizard(); }
    showList()   { this.state.view = 'list'; this.loadMyProductions(); }
    backToList() { this.state.view = 'list'; this.state.selectedMo = null; this.state.moDetail = null; }

    getStateLabel(s) {
        return ({ draft:'Borrador', confirmed:'Confirmada', progress:'En progreso',
                  to_close:'Por cerrar', done:'Hecha', cancel:'Cancelada' })[s] || s;
    }
    getStateClass(s) {
        if (s === 'done')     return 'success';
        if (s === 'cancel')   return 'danger';
        if (s === 'progress') return 'warning';
        return 'info';
    }

    // Helper para los steps: devuelve si el paso lote_config aplica
    get showLotStep() { return !this.state.autoLot; }
}

SimplifiedMrp.template = 'aq_simplified_mrp.Main';
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);```

## ./static/src/scss/simplified_mrp.scss
```scss
/* static/src/scss/simplified_mrp.scss */
/* ========================================================================
   SMRP - Simplified MRP System
   Versión: Odoo 18 - v1.1 (con captura manual de lote XX-##-##-##-##)
   ======================================================================== */

:root {
  --smrp-brand-blue: #2737BE;
  --smrp-brand-blue-2: #3B51E0;
  --smrp-brand-blue-3: #EEF1FF;
  --smrp-brand-green: #3EF24E;
  --smrp-brand-green-2: #BFFFD0;
  /* Colores específicos para los segmentos del lote */
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
  --smrp-header-height: 56px;
}

/* ========================================================================
   CONTENEDOR PRINCIPAL
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

/* Wrapper interno - ESTE ES EL QUE HACE EL SCROLL VERTICAL */
.o_smrp_wrapper {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
  padding: 16px;
  padding-bottom: 100px;

  &::-webkit-scrollbar {
    width: 8px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }

  &::-webkit-scrollbar-thumb {
    background: #cbd5e1;
    border-radius: 4px;

    &:hover {
      background: var(--smrp-brand-blue-2);
    }
  }

  scrollbar-width: thin;
  scrollbar-color: var(--smrp-brand-blue-2) transparent;
}

.o_smrp_container {
  max-width: var(--smrp-maxw);
  margin: 0 auto;
  display: grid;
  gap: var(--smrp-gap);
}

.o_smrp_section {
  padding: 8px;
}

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
    width: 24px;
    height: 24px;
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
   INPUTS & FORMS
   ======================================================================== */
.o_smrp_row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  justify-content: center;
}

.o_smrp_field_group {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 200px;
}

.o_smrp_label {
  font-size: 13px;
  font-weight: 700;
  color: #555;
  margin-left: 2px;
}

.o_smrp_input {
  padding: 11px 14px;
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  width: 100%;
  box-sizing: border-box;
  font-size: var(--smrp-fs-base);
  background: #fff;
  transition: all 0.2s ease;

  &--xl {
    font-size: var(--smrp-fs-lg);
    padding: 12px 16px;
  }

  &:focus {
    outline: none;
    border-color: var(--smrp-brand-blue);
    box-shadow: 0 0 0 3px var(--smrp-brand-blue-3);
  }
}

/* Selector nativo estilizado */
select.o_smrp_input {
  appearance: none;
  background-image: url("data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23007CB2%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E");
  background-repeat: no-repeat;
  background-position: right 12px top 50%;
  background-size: 10px auto;
  padding-right: 30px;
}

/* Autocomplete Container */
.o_smrp_autocomplete {
  position: relative;
  width: 100%;
}

.o_smrp_autocomplete_list {
  position: absolute;
  top: 100%; left: 0; right: 0;
  background: #fff;
  border: 1px solid #d3d8e3;
  border-top: none;
  border-radius: 0 0 8px 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  z-index: 100;
  max-height: 200px;
  overflow-y: auto;

  .item {
    padding: 10px 14px;
    font-size: 14px;
    cursor: pointer;
    border-bottom: 1px solid #eee;
    &:last-child { border-bottom: none; }
    &:hover {
      background: #f6f8ff;
      color: var(--smrp-brand-blue);
    }
  }
}

/* ========================================================================
   BOTONES
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

  &:hover:not(:disabled) {
    filter: brightness(1.05);
    transform: translateY(-1px);
  }

  &:active:not(:disabled) {
    transform: translateY(0);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  &--ghost {
    background: #fff;
    color: var(--smrp-brand-blue);
  }

  &--xl {
    min-width: 160px;
  }

  &.confirm {
    background: var(--smrp-brand-green);
    border-color: var(--smrp-brand-green);
    color: #062b10;
  }
}

/* ========================================================================
   ACTIONS BAR
   ======================================================================== */
.o_smrp_actions {
  display: flex;
  gap: 10px;
  justify-content: center;
  align-items: center;
  flex-wrap: wrap;
  padding: 8px 0;

  &--end {
    justify-content: flex-end;
  }

  &--center {
    justify-content: center;
  }

  &--sticky {
    position: sticky;
    bottom: 0;
    padding: 12px 16px;
    margin: 0 -16px -16px;
    background: linear-gradient(180deg,
      rgba(246, 248, 255, 0) 0%,
      rgba(246, 248, 255, 0.97) 20%,
      rgba(246, 248, 255, 1) 50%
    );
    backdrop-filter: blur(8px);
    z-index: 50;
    border-top: 1px solid rgba(207, 214, 234, 0.2);
  }
}

/* ========================================================================
   FILTERS
   ======================================================================== */
.o_smrp_filters {
  position: sticky;
  top: 0;
  background: var(--smrp-bg);
  z-index: 90;
  padding: 12px 0;
  margin-bottom: 16px;
  border-bottom: 1px solid rgba(207, 214, 234, 0.3);
}

/* ========================================================================
   CARDS (GRID)
   ======================================================================== */
.o_smrp_cards {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  padding: 4px;
  margin-bottom: 16px;

  &--center {
    justify-items: center;
  }
}

.o_smrp_card {
  width: 100%;
  max-width: 300px;
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  background: var(--smrp-card);
  box-shadow: var(--smrp-shadow);
  padding: 16px;
  text-align: center;
  position: relative;
  transition: all 0.2s ease;

  &_icon {
    font-size: 28px;
    margin-bottom: 8px;
    line-height: 1;
  }

  &_title {
    font-size: var(--smrp-fs-lg);
    font-weight: 600;
    color: var(--smrp-ink);
    margin-bottom: 4px;
    line-height: 1.3;
  }

  &_sub {
    font-size: 14px;
    color: #6b7996;
  }

  &.selectable {
    cursor: pointer;
    &:hover {
      border-color: var(--smrp-brand-blue-2);
      box-shadow: 0 8px 24px rgba(39, 55, 190, 0.15);
      transform: translateY(-2px);
    }
    &:active {
      transform: translateY(0);
    }
  }

  &.selected {
    border-color: var(--smrp-brand-blue);
    border-width: 2px;
    background: var(--smrp-brand-blue-3);
    padding: 15px;
    .o_smrp_check { display: grid; }
  }
}

.o_smrp_check {
  position: absolute;
  top: -10px;
  right: -10px;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: var(--smrp-brand-green);
  color: #062b10;
  display: none;
  place-items: center;
  font-weight: 700;
  font-size: 14px;
  box-shadow: 0 4px 12px rgba(62, 242, 78, 0.4);
}

/* ========================================================================
   SELECTED BANNER
   ======================================================================== */
.o_smrp_selected {
  position: sticky;
  bottom: 0;
  padding: 14px 16px;
  margin: 16px -16px -16px;
  background: linear-gradient(180deg,
    rgba(246, 248, 255, 0) 0%,
    rgba(246, 248, 255, 0.97) 20%,
    rgba(246, 248, 255, 1) 50%
  );
  backdrop-filter: blur(8px);
  border-radius: var(--smrp-radius) var(--smrp-radius) 0 0;
  text-align: center;
  color: var(--smrp-ink);
  font-size: var(--smrp-fs-base);
  z-index: 50;
  border-top: 1px solid rgba(207, 214, 234, 0.3);

  .badge {
    display: inline-block;
    padding: 4px 10px;
    background: var(--smrp-brand-green-2);
    color: #062b10;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    margin-right: 8px;
  }

  strong { font-weight: 600; }
}

/* ========================================================================
   WIZARD, LOTES & PROGRESO
   ======================================================================== */
.o_smrp_wizard {
  display: grid;
  gap: 16px;
}

.o_smrp_counter {
  text-align: center;
  font-size: var(--smrp-fs-base);
  color: var(--smrp-brand-blue);
  font-weight: 600;
  padding: 10px;
  background: #fff;
  border-radius: var(--smrp-radius);
  box-shadow: var(--smrp-shadow);
}

.o_smrp_box {
  padding: 18px;
  background: #fff;
  border-radius: var(--smrp-radius);
  box-shadow: var(--smrp-shadow);
  display: grid;
  gap: 12px;
  &--big { padding: 22px; }
}

.o_smrp_name {
  font-size: var(--smrp-fs-lg);
  font-weight: 600;
  color: var(--smrp-ink);
  line-height: 1.3;
}

.o_smrp_meta {
  font-size: 14px;
  color: #6b7996;
  strong { font-weight: 600; color: var(--smrp-ink); }
}

/* Barra de progreso para lotes */
.o_smrp_header_flex {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  flex-wrap: wrap;
  gap: 8px;
}

.o_smrp_progress_text {
  font-size: 14px;
  color: #555;
  background: #f0f0f0;
  padding: 4px 8px;
  border-radius: 6px;
  strong { color: var(--smrp-brand-blue); }
}

.o_smrp_progress_bar {
  height: 10px;
  background: #e2e8f0;
  border-radius: 5px;
  overflow: hidden;
  margin-bottom: 20px;
  position: relative;
}

.o_smrp_progress_fill {
  height: 100%;
  background: var(--smrp-brand-green);
  transition: width 0.3s ease;
  box-shadow: 0 0 10px rgba(62, 242, 78, 0.5);
}

/* Grid de Lotes con Input */
.o_smrp_lots {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  padding: 4px;
  margin-bottom: 16px;
}

.o_smrp_lot_input_card {
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  padding: 14px;
  background: #fff;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: var(--smrp-shadow);

  &.active {
    border-color: var(--smrp-brand-blue);
    background: #f0f4ff;
    box-shadow: 0 0 0 2px var(--smrp-brand-blue-3);
  }

  .head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;

    .name {
      font-weight: 700;
      font-size: 15px;
      color: var(--smrp-ink);
      word-break: break-all;
    }

    .avail {
      font-size: 12px;
      color: #444;
      background: #eef1ff;
      padding: 3px 8px;
      border-radius: 99px;
      white-space: nowrap;
      flex-shrink: 0;
      margin-left: 8px;
    }
  }

  .input-area {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: auto;

    label {
      font-size: 13px;
      font-weight: 600;
      color: #666;
    }
  }
}

.o_smrp_input_small {
  padding: 6px 10px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  width: 100%;
  font-weight: 700;
  font-size: 15px;
  color: var(--smrp-brand-blue);
  text-align: right;
  background: #fff;

  &:focus {
    outline: none;
    border-color: var(--smrp-brand-blue);
    box-shadow: 0 0 0 2px var(--smrp-brand-blue-3);
  }
}

.o_smrp_lot_search {
  margin-bottom: 4px;
}

.o_smrp_empty {
  text-align: center;
  padding: 32px 16px;
  color: #6b7996;
  font-size: var(--smrp-fs-base);
  &--block {
    display: block;
    padding: 20px;
    background: #f9fafb;
    border-radius: var(--smrp-radius);
    border: 1px dashed #d3d8e3;
  }
}

/* ========================================================================
   DONE STATE
   ======================================================================== */
.o_smrp_done {
  text-align: center;
  padding: 32px 0;
  &_icon {
    font-size: 72px;
    margin-bottom: 16px;
    animation: bounce 0.6s ease;
  }
}

@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

/* ========================================================================
   NAV SUPERIOR
   ======================================================================== */
.o_smrp_nav {
  display: flex;
  gap: 8px;
  justify-content: center;
  padding: 12px;
  background: var(--smrp-bg);
  z-index: 110;
  border-bottom: 1px solid #d3d8e3;
  flex-shrink: 0;
}

.o_smrp_nav_btn {
  padding: 12px 20px;
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  background: #fff;
  color: var(--smrp-ink);
  font-size: var(--smrp-fs-base);
  cursor: pointer;
  transition: all .2s;

  &:hover { border-color: var(--smrp-brand-blue-2); }
  &.active {
    background: var(--smrp-brand-blue);
    color: #fff;
    border-color: var(--smrp-brand-blue);
  }
}

/* ========================================================================
   LISTA DE ÓRDENES
   ======================================================================== */
.o_smrp_list {
  display: grid;
  gap: 12px;
  padding: 4px;
}

.o_smrp_list_item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border: 1px solid #d3d8e3;
  border-radius: var(--smrp-radius);
  background: #fff;
  cursor: pointer;
  box-shadow: var(--smrp-shadow);
  transition: all .2s;

  &:hover {
    border-color: var(--smrp-brand-blue-2);
    box-shadow: 0 0 0 3px var(--smrp-brand-blue-3);
  }
}

.o_smrp_list_icon { font-size: 32px; flex-shrink: 0; }
.o_smrp_list_content { flex: 1; min-width: 0; }
.o_smrp_list_title { font-weight: 700; font-size: 18px; margin-bottom: 4px; word-break: break-word; }
.o_smrp_list_meta { font-size: 14px; opacity: .8; }

.o_smrp_list_badge {
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
  &.success { background: var(--smrp-brand-green-2); color: #062b10; }
  &.danger  { background: #ffe0e0; color: #8b0000; }
  &.warning { background: #fff4e0; color: #8b5a00; }
  &.info    { background: var(--smrp-brand-blue-3); color: var(--smrp-brand-blue); }
}

/* Detalle */
.o_smrp_detail_row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
  &:last-child { border-bottom: none; }
}
.o_smrp_detail_label { font-weight: 700; min-width: 180px; flex-shrink: 0; }

/* ========================================================================
   ★ CAPTURA DE LOTE MANUAL (XX-##-##-##-##)
   ======================================================================== */

/* Tarjeta contenedor principal */
.o_smrp_lot_config_card {
  background: #fff;
  border-radius: 20px;
  box-shadow: 0 8px 32px rgba(39, 55, 190, 0.1), 0 2px 8px rgba(0, 0, 0, 0.04);
  padding: 28px;
  display: grid;
  gap: 24px;
  max-width: 860px;
  margin: 0 auto;
}

/* Cabecera */
.o_smrp_lot_config_header {
  display: flex;
  align-items: center;
  gap: 16px;
}

.o_smrp_lot_config_icon {
  font-size: 40px;
  line-height: 1;
  flex-shrink: 0;
  filter: drop-shadow(0 4px 8px rgba(0, 0, 0, 0.15));
}

.o_smrp_lot_config_title {
  font-size: 18px;
  font-weight: 700;
  color: var(--smrp-ink);
  margin-bottom: 4px;
}

.o_smrp_lot_config_subtitle {
  font-size: 14px;
  color: #6b7996;
  strong { color: var(--smrp-ink); font-weight: 600; }
}

/* Referencia de formato */
.o_smrp_lot_pattern_ref {
  background: linear-gradient(135deg, #f8faff 0%, #f0f4ff 100%);
  border: 1px solid #dde3f5;
  border-radius: 12px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.ref-label {
  font-size: 13px;
  font-weight: 700;
  color: #6b7996;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  flex-shrink: 0;
}

.ref-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ref-dash {
  font-size: 18px;
  font-weight: 700;
  color: #9ca3af;
  line-height: 1;
}

/* Chips del patrón de referencia */
.chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 800;
  font-family: 'Courier New', Courier, monospace;
  letter-spacing: 0.08em;
  padding: 6px 14px;
  border-radius: 8px;
  border: 2px dashed;

  &--text {
    color: var(--smrp-lot-text-color);
    background: var(--smrp-lot-text-bg);
    border-color: var(--smrp-lot-text-border);
  }

  &--num {
    color: var(--smrp-lot-num-color);
    background: var(--smrp-lot-num-bg);
    border-color: var(--smrp-lot-num-border);
  }
}

.ref-example {
  font-size: 13px;
  color: #6b7996;
  margin-left: auto;
  padding: 6px 12px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  strong {
    color: var(--smrp-ink);
    font-family: 'Courier New', Courier, monospace;
  }
}

/* ── Builder segmentado ─────────────────────────────────────── */
.o_smrp_lot_builder {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 0;
}

/* Separador visual entre segmentos */
.o_smrp_lot_dash {
  font-size: 28px;
  font-weight: 800;
  color: #9ca3af;
  align-self: center;
  margin-top: -12px; /* compensa la altura del label+badge */
  user-select: none;
  flex-shrink: 0;
}

/* Tarjeta individual de cada segmento */
.o_smrp_lot_seg {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  min-width: 90px;
  flex-shrink: 0;

  /* Badge de tipo (ABC / 123) */
  .seg-badge {
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.06em;
    padding: 3px 10px;
    border-radius: 999px;

    &--text {
      background: var(--smrp-lot-text-bg);
      color: var(--smrp-lot-text-color);
      border: 1px solid var(--smrp-lot-text-border);
    }

    &--num {
      background: var(--smrp-lot-num-bg);
      color: var(--smrp-lot-num-color);
      border: 1px solid var(--smrp-lot-num-border);
    }
  }

  /* Etiqueta descriptiva */
  .seg-label {
    font-size: 12px;
    font-weight: 600;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  /* Input grande del segmento */
  .seg-input {
    width: 80px;
    height: 64px;
    text-align: center;
    font-size: 26px;
    font-weight: 800;
    font-family: 'Courier New', Courier, monospace;
    letter-spacing: 0.06em;
    border: 2.5px solid #e2e8f0;
    border-radius: 12px;
    background: #fafbff;
    transition: all 0.2s ease;
    caret-color: var(--smrp-brand-blue);

    &::placeholder {
      font-size: 22px;
      color: #d1d5db;
      font-weight: 700;
    }

    &:focus {
      outline: none;
      background: #fff;
      box-shadow: 0 0 0 4px rgba(39, 55, 190, 0.12);
    }

    /* Variante texto (letras) */
    &--text {
      color: var(--smrp-lot-text-color);
      border-color: var(--smrp-lot-text-border);
      &:focus {
        border-color: var(--smrp-lot-text-color);
        box-shadow: 0 0 0 4px rgba(124, 58, 237, 0.12);
      }
    }

    /* Variante numérica */
    &--num {
      color: var(--smrp-lot-num-color);
      border-color: var(--smrp-lot-num-border);
      &:focus {
        border-color: var(--smrp-lot-num-color);
        box-shadow: 0 0 0 4px rgba(29, 78, 216, 0.12);
      }
    }
  }

  /* Pie: hint o error */
  .seg-footer { min-height: 18px; }
  .seg-hint { font-size: 11px; color: #9ca3af; text-align: center; }
  .seg-error { font-size: 11px; color: #ef4444; text-align: center; font-weight: 600; }

  /* Estado: campo completado */
  &.filled .seg-input {
    background: linear-gradient(135deg, #fafbff 0%, #f0f4ff 100%);
    border-color: var(--smrp-brand-blue-2);

    &--text {
      background: var(--smrp-lot-text-bg);
      border-color: var(--smrp-lot-text-color);
    }
    &--num {
      background: var(--smrp-lot-num-bg);
      border-color: var(--smrp-lot-num-color);
    }
  }

  /* Estado: error de validación */
  &.error .seg-input {
    border-color: #ef4444 !important;
    background: #fff5f5;
    box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.12) !important;
    animation: smrp-shake 0.35s ease;
  }
}

@keyframes smrp-shake {
  0%, 100% { transform: translateX(0); }
  20%, 60%  { transform: translateX(-4px); }
  40%, 80%  { transform: translateX(4px); }
}

/* Preview en tiempo real */
.o_smrp_lot_preview {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 16px 24px;
  background: linear-gradient(135deg, #1e2b8a 0%, #2737BE 100%);
  border-radius: 14px;
  box-shadow: 0 4px 16px rgba(39, 55, 190, 0.3);
  flex-wrap: wrap;

  .preview-label {
    font-size: 13px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.7);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    flex-shrink: 0;
  }

  .preview-value {
    font-family: 'Courier New', Courier, monospace;
    font-size: 28px;
    font-weight: 900;
    letter-spacing: 0.12em;
    color: #fff;
    text-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    word-break: break-all;
  }
}

/* Leyenda de colores */
.o_smrp_lot_legend {
  display: flex;
  gap: 20px;
  justify-content: center;
  flex-wrap: wrap;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #6b7996;
}

.legend-dot {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  flex-shrink: 0;

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
  .o_smrp_filters .o_smrp_row { flex-direction: column; gap: 8px; }
  .o_smrp_actions { flex-direction: column; .o_smrp_btn { width: 100%; } }
  .o_smrp_steps {
    gap: 6px; padding: 8px;
    li { font-size: 12px; padding: 6px 10px; }
    .num { width: 22px; height: 22px; font-size: 11px; }
  }

  /* Lot builder en mobile */
  .o_smrp_lot_config_card { padding: 18px; gap: 18px; }
  .o_smrp_lot_builder { gap: 6px; }
  .o_smrp_lot_seg .seg-input { width: 58px; height: 54px; font-size: 20px; }
  .o_smrp_lot_seg .seg-input::placeholder { font-size: 17px; }
  .o_smrp_lot_dash { font-size: 20px; }
  .o_smrp_lot_preview { padding: 14px 16px; }
  .o_smrp_lot_preview .preview-value { font-size: 20px; letter-spacing: 0.08em; }
  .ref-example { margin-left: 0; }
}```

## ./static/src/xml/simplified_mrp_templates.xml
```xml
<templates id="template" xml:space="preserve">
  <t t-name="aq_simplified_mrp.Main">
    <div class="o_smrp o_smrp--tablet">

      <div class="o_smrp_nav">
        <button class="o_smrp_nav_btn" t-att-class="{'active': state.view === 'create'}" t-on-click="() => this.showCreate()">➕ Nueva orden</button>
        <button class="o_smrp_nav_btn" t-att-class="{'active': state.view === 'list' || state.view === 'detail'}" t-on-click="() => this.showList()">📋 Mis órdenes</button>
      </div>

      <div class="o_smrp_wrapper">

        <t t-if="state.view === 'create'">

          <!-- Steps Header -->
          <div class="o_smrp_container">
            <ol class="o_smrp_steps" role="list">
              <li t-att-class="{'active': state.step === 'warehouse', 'done': state.step !== 'warehouse'}">
                <span class="num">1</span><span>Almacén</span>
              </li>
              <li t-att-class="{'active': state.step === 'product', 'done': ['lot_config','components','lots','done'].includes(state.step)}">
                <span class="num">2</span><span>Configuración</span>
              </li>
              <t t-if="!state.autoLot">
                <li t-att-class="{'active': state.step === 'lot_config', 'done': ['components','lots','done'].includes(state.step)}">
                  <span class="num">3</span><span>Lote</span>
                </li>
              </t>
              <li t-att-class="{'active': state.step === 'components', 'done': ['lots','done'].includes(state.step)}">
                <span class="num" t-esc="state.autoLot ? 3 : 4"/><span>Ingredientes</span>
              </li>
              <li t-att-class="{'active': state.step === 'lots', 'done': state.step === 'done'}">
                <span class="num" t-esc="state.autoLot ? 4 : 5"/><span>Lotes</span>
              </li>
              <li t-att-class="{'active': state.step === 'done'}">
                <span class="num" t-esc="state.autoLot ? 5 : 6"/><span>Listo</span>
              </li>
            </ol>
          </div>

          <!-- PASO 1: ALMACÉN -->
          <t t-if="state.step === 'warehouse'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Selecciona almacén</h2>
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

          <!-- PASO 2: PRODUCTO Y CONFIG -->
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
                    <label class="o_smrp_label">Destino (Ubicación)</label>
                    <select class="o_smrp_input" t-model="state.selectedDestLocation">
                      <option value="">-- Por defecto (Stock) --</option>
                      <t t-foreach="state.destLocations" t-as="loc" t-key="loc.id">
                        <option t-att-value="loc"><t t-esc="loc.name"/></option>
                      </t>
                    </select>
                  </div>
                </div>
                <div class="o_smrp_actions o_smrp_actions--end">
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
                    <div class="o_smrp_card_sub"><t t-esc="p.uom_name || p.uomName"/></div>
                    <div class="o_smrp_check">✔</div>
                  </div>
                </t>
              </div>
              <div class="o_smrp_selected" t-if="state.productName">
                <span class="badge">Seleccionado</span>
                <strong><t t-esc="state.productName"/></strong>
                <t t-if="state.qty"> • Cant.: <strong><t t-esc="state.qty"/></strong></t>
              </div>
            </div>
          </t>

          <!-- ================================================================
               PASO LOT_CONFIG — Captura manual XX-##-##-##-##
               ================================================================ -->
          <t t-if="state.step === 'lot_config'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Número de Lote</h2>

              <div class="o_smrp_lot_config_card">

                <!-- Cabecera -->
                <div class="o_smrp_lot_config_header">
                  <div class="o_smrp_lot_config_icon">🏷️</div>
                  <div>
                    <div class="o_smrp_lot_config_title">Asigna el lote del producto terminado</div>
                    <div class="o_smrp_lot_config_subtitle">
                      Producto: <strong><t t-esc="state.productName"/></strong>
                    </div>
                  </div>
                </div>

                <!-- Referencia de formato -->
                <div class="o_smrp_lot_pattern_ref">
                  <span class="ref-label">Formato requerido:</span>
                  <div class="ref-chips">
                    <span class="chip chip--text" title="2 letras mayúsculas">XX</span>
                    <span class="ref-dash">–</span>
                    <span class="chip chip--num" title="2 dígitos">##</span>
                    <span class="ref-dash">–</span>
                    <span class="chip chip--num" title="2 dígitos">##</span>
                    <span class="ref-dash">–</span>
                    <span class="chip chip--num" title="2 dígitos">##</span>
                    <span class="ref-dash">–</span>
                    <span class="chip chip--num" title="2 dígitos">##</span>
                  </div>
                  <div class="ref-example">Ejemplo: <strong>AB-01-02-03-04</strong></div>
                </div>

                <!-- Builder segmentado -->
                <div class="o_smrp_lot_builder">

                  <!-- SEG 1: Letras -->
                  <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors.s1, 'filled': state.lotSeg1.length === 2}">
                    <div class="seg-badge seg-badge--text">ABC</div>
                    <div class="seg-label">Letras</div>
                    <input
                      class="seg-input seg-input--text"
                      type="text" maxlength="2" placeholder="AB"
                      data-seg="1"
                      t-att-value="state.lotSeg1"
                      t-on-input="(ev) => this.onSeg1Change(ev)"
                      autocomplete="off" spellcheck="false" autocorrect="off"
                    />
                    <div class="seg-footer">
                      <span t-if="!state.lotSegErrors.s1" class="seg-hint">2 letras</span>
                      <span t-if="state.lotSegErrors.s1" class="seg-error">⚠ 2 letras</span>
                    </div>
                  </div>

                  <div class="o_smrp_lot_dash">—</div>

                  <!-- SEG 2 -->
                  <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors.s2, 'filled': state.lotSeg2.length > 0}">
                    <div class="seg-badge seg-badge--num">123</div>
                    <div class="seg-label">Número</div>
                    <input
                      class="seg-input seg-input--num"
                      type="text" inputmode="numeric" maxlength="2" placeholder="01"
                      data-seg="2"
                      t-att-value="state.lotSeg2"
                      t-on-input="(ev) => this.onSeg2Change(ev)"
                      autocomplete="off"
                    />
                    <div class="seg-footer">
                      <span t-if="!state.lotSegErrors.s2" class="seg-hint">2 dígitos</span>
                      <span t-if="state.lotSegErrors.s2" class="seg-error">⚠ 2 dígitos</span>
                    </div>
                  </div>

                  <div class="o_smrp_lot_dash">—</div>

                  <!-- SEG 3 -->
                  <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors.s3, 'filled': state.lotSeg3.length > 0}">
                    <div class="seg-badge seg-badge--num">123</div>
                    <div class="seg-label">Número</div>
                    <input
                      class="seg-input seg-input--num"
                      type="text" inputmode="numeric" maxlength="2" placeholder="01"
                      data-seg="3"
                      t-att-value="state.lotSeg3"
                      t-on-input="(ev) => this.onSeg3Change(ev)"
                      autocomplete="off"
                    />
                    <div class="seg-footer">
                      <span t-if="!state.lotSegErrors.s3" class="seg-hint">2 dígitos</span>
                      <span t-if="state.lotSegErrors.s3" class="seg-error">⚠ 2 dígitos</span>
                    </div>
                  </div>

                  <div class="o_smrp_lot_dash">—</div>

                  <!-- SEG 4 -->
                  <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors.s4, 'filled': state.lotSeg4.length > 0}">
                    <div class="seg-badge seg-badge--num">123</div>
                    <div class="seg-label">Número</div>
                    <input
                      class="seg-input seg-input--num"
                      type="text" inputmode="numeric" maxlength="2" placeholder="01"
                      data-seg="4"
                      t-att-value="state.lotSeg4"
                      t-on-input="(ev) => this.onSeg4Change(ev)"
                      autocomplete="off"
                    />
                    <div class="seg-footer">
                      <span t-if="!state.lotSegErrors.s4" class="seg-hint">2 dígitos</span>
                      <span t-if="state.lotSegErrors.s4" class="seg-error">⚠ 2 dígitos</span>
                    </div>
                  </div>

                  <div class="o_smrp_lot_dash">—</div>

                  <!-- SEG 5 -->
                  <div class="o_smrp_lot_seg" t-att-class="{'error': state.lotSegErrors.s5, 'filled': state.lotSeg5.length > 0}">
                    <div class="seg-badge seg-badge--num">123</div>
                    <div class="seg-label">Número</div>
                    <input
                      class="seg-input seg-input--num"
                      type="text" inputmode="numeric" maxlength="2" placeholder="01"
                      data-seg="5"
                      t-att-value="state.lotSeg5"
                      t-on-input="(ev) => this.onSeg5Change(ev)"
                      autocomplete="off"
                    />
                    <div class="seg-footer">
                      <span t-if="!state.lotSegErrors.s5" class="seg-hint">2 dígitos</span>
                      <span t-if="state.lotSegErrors.s5" class="seg-error">⚠ 2 dígitos</span>
                    </div>
                  </div>

                </div><!-- /.o_smrp_lot_builder -->

                <!-- Preview en tiempo real -->
                <div class="o_smrp_lot_preview">
                  <span class="preview-label">Vista previa del lote:</span>
                  <span class="preview-value" t-esc="state.lotPreview || '__-__-__-__-__'"/>
                </div>

                <!-- Leyenda -->
                <div class="o_smrp_lot_legend">
                  <span class="legend-item">
                    <span class="legend-dot legend-dot--text"/>
                    Segmento de texto (letras mayúsculas)
                  </span>
                  <span class="legend-item">
                    <span class="legend-dot legend-dot--num"/>
                    Segmento numérico (dígitos)
                  </span>
                </div>

              </div><!-- /.o_smrp_lot_config_card -->

              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl"
                        t-on-click="() => this.backFromLotConfig()">← Volver</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl"
                        t-on-click="() => this.confirmManualLot()">✓ Confirmar lote</button>
              </div>
            </div>
          </t>

          <!-- PASO 3/4: INGREDIENTES -->
          <t t-if="state.step === 'components'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Ingredientes</h2>
              <t t-if="!state.editingComponent">
                <div class="o_smrp_box">
                  <div class="o_smrp_name">Agregar ingrediente</div>
                  <div class="o_smrp_row">
                    <input class="o_smrp_input o_smrp_input--xl" type="text" placeholder="Buscar…"
                           t-model="state.compSearchQuery" t-on-input="() => this.searchComponents()"/>
                    <input class="o_smrp_input o_smrp_input--xl" type="number" min="0" step="0.0001"
                           t-model="state.newCompQty" placeholder="Cantidad"/>
                  </div>
                  <div class="o_smrp_cards o_smrp_cards--center">
                    <t t-foreach="state.compSearchResults" t-as="p" t-key="p.id">
                      <div class="o_smrp_card selectable" t-on-click="() => this.addComponentFromSearch(p)">
                        <div class="o_smrp_card_icon">➕</div>
                        <div class="o_smrp_card_title"><t t-esc="p.name"/></div>
                        <div class="o_smrp_card_sub"><t t-esc="p.uom_name"/></div>
                      </div>
                    </t>
                  </div>
                </div>
              </t>
              <t t-if="state.editingComponent &amp;&amp; state.components.length">
                <div class="o_smrp_wizard">
                  <div class="o_smrp_counter">Ingrediente <t t-esc="state.compIndex + 1"/> de <t t-esc="state.components.length"/></div>
                  <t t-set="c" t-value="state.components[state.compIndex]"/>
                  <div class="o_smrp_box o_smrp_box--big">
                    <div class="o_smrp_name"><t t-esc="c.name"/></div>
                    <div class="o_smrp_meta">UM: <t t-esc="c.uom_name"/></div>
                    <div class="o_smrp_row">
                      <label class="o_smrp_label">Cantidad Total</label>
                      <input class="o_smrp_input o_smrp_input--xl" type="number" min="0" step="0.0001"
                             t-att-value="c.qty_required" t-on-input="(ev) => this.updateCurrentQty(ev)"/>
                    </div>
                  </div>
                  <div class="o_smrp_actions o_smrp_actions--sticky">
                    <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.removeCurrentComponent()">🗑️ Quitar</button>
                    <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-att-disabled="state.compIndex === 0" t-on-click="() => this.prevComponent()">← Anterior</button>
                    <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.nextComponent()">
                      <t t-if="state.compIndex &lt; state.components.length - 1">Siguiente →</t>
                      <t t-else="">✓ Listo</t>
                    </button>
                  </div>
                </div>
              </t>
              <div class="o_smrp_actions o_smrp_actions--sticky" t-if="state.components.length">
                <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.backToProduct()">← Volver</button>
                <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.reviewComponents()">📝 Revisar</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.continueToLots()">✓ Ir a Lotes</button>
              </div>
            </div>
          </t>

          <!-- PASO 4/5: LOTES COMPONENTES -->
          <t t-if="state.step === 'lots'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Selección de Lotes</h2>
              <t t-if="state.components.length">
                <div class="o_smrp_wizard">
                  <t t-set="c" t-value="state.components[state.compIndex]"/>
                  <div class="o_smrp_counter">Ingrediente <t t-esc="state.compIndex + 1"/> de <t t-esc="state.components.length"/></div>
                  <div class="o_smrp_box o_smrp_box--big">
                    <div class="o_smrp_header_flex">
                      <div class="o_smrp_name"><t t-esc="c.name"/></div>
                      <div class="o_smrp_progress_text">
                        <t t-set="assigned" t-value="this.getAssignedTotal(c.product_id)"/>
                        Asignado: <strong><t t-esc="assigned.toFixed(2)"/></strong> / <t t-esc="c.qty_required"/> <t t-esc="c.uom_name"/>
                      </div>
                    </div>
                    <div class="o_smrp_progress_bar">
                      <t t-set="assigned" t-value="this.getAssignedTotal(c.product_id)"/>
                      <div class="o_smrp_progress_fill"
                           t-att-style="'width:' + Math.min((assigned / (c.qty_required || 1))*100, 100) + '%'"/>
                    </div>
                    <div class="o_smrp_lot_search">
                      <input class="o_smrp_input" type="text" placeholder="🔍 Buscar lote por número..."
                             t-model="state.lotQuery" t-on-input="() => this.searchLots()"/>
                    </div>
                    <div class="o_smrp_lots">
                      <t t-foreach="state.lots" t-as="l" t-key="l.id">
                        <div class="o_smrp_lot_input_card" t-att-class="{'active': this.getLotAssignedValue(c.product_id, l.id) > 0}">
                          <div class="head">
                            <div class="name"><t t-esc="l.name"/></div>
                            <div class="avail">Disp: <t t-esc="l.qty_available"/></div>
                          </div>
                          <div class="input-area">
                            <label>Usar:</label>
                            <input type="number" min="0" step="0.001" class="o_smrp_input_small"
                                   t-att-value="this.getLotAssignedValue(c.product_id, l.id)"
                                   t-on-change="(ev) => this.updateLotAssignment(l.id, ev.target.value)"/>
                          </div>
                        </div>
                      </t>
                      <t t-if="!state.lots.length">
                        <div class="o_smrp_empty o_smrp_empty--block">
                          <t t-if="state.lotQuery">No se encontraron lotes con "<t t-esc="state.lotQuery"/>".</t>
                          <t t-else="">Sin lotes disponibles.</t>
                        </div>
                      </t>
                    </div>
                  </div>
                  <div class="o_smrp_actions o_smrp_actions--sticky">
                    <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.prevLotStep()">
                      <t t-if="state.compIndex === 0">← Ingredientes</t>
                      <t t-else="">← Anterior</t>
                    </button>
                    <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.nextLotStep()">
                      <t t-if="state.compIndex &lt; state.components.length - 1">Siguiente →</t>
                      <t t-else="">✓ Crear Orden</t>
                    </button>
                  </div>
                </div>
              </t>
            </div>
          </t>

          <!-- DONE -->
          <t t-if="state.step === 'done'">
            <div class="o_smrp_container o_smrp_section o_smrp_done">
              <div class="o_smrp_done_icon">🎉</div>
              <h2>¡Orden creada!</h2>
              <div class="o_smrp_box">
                <div>Orden: <strong><t t-esc="state.resultMoName"/></strong></div>
                <div>La producción ha sido registrada correctamente.</div>
              </div>
              <div class="o_smrp_actions o_smrp_actions--center">
                <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.resetWizard()">Crear nueva orden</button>
              </div>
            </div>
          </t>
        </t>

        <!-- LISTA -->
        <t t-if="state.view === 'list'">
          <div class="o_smrp_container o_smrp_section">
            <h2>Mis órdenes</h2>
            <div class="o_smrp_list">
              <t t-foreach="state.myProductions" t-as="mo" t-key="mo.id">
                <div class="o_smrp_list_item" t-on-click="() => this.loadMoDetail(mo.id)">
                  <div class="o_smrp_list_icon">📦</div>
                  <div class="o_smrp_list_content">
                    <div class="o_smrp_list_title"><t t-esc="mo.name"/></div>
                    <div class="o_smrp_list_meta">
                      <t t-esc="mo.product_name"/> (<t t-esc="mo.product_qty"/> <t t-esc="mo.uom_name"/>)
                    </div>
                  </div>
                  <div class="o_smrp_list_badge" t-att-class="this.getStateClass(mo.state)">
                    <t t-esc="this.getStateLabel(mo.state)"/>
                  </div>
                </div>
              </t>
            </div>
            <div class="o_smrp_empty" t-if="!state.myProductions.length">No tienes órdenes.</div>
          </div>
        </t>

        <!-- DETALLE -->
        <t t-if="state.view === 'detail' &amp;&amp; state.moDetail">
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
                <span class="o_smrp_detail_label">Lote Final:</span>
                <t t-esc="state.moDetail.finished_lot"/>
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
      <field name="name">Producción simple</field>
      <field name="tag">aq_simplified_mrp.client_action</field>
      <field name="target">current</field>
    </record>
  </data>
</odoo>
```

## ./views/menu.xml
```xml
<odoo>
  <data>
    <menuitem id="menu_simplified_mrp_root"
              name="Carga Producción"
              parent="mrp.menu_mrp_root"
              sequence="5"
              action="action_simplified_mrp"
              groups="aq_simplified_mrp.group_simplified_mrp_user"/>
  </data>
</odoo>
```

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
          <app string="Producción Simplificada" name="aq_simplified_mrp" groups="base.group_system">
            <block title="Lotes de Producto Terminado">
              <setting
                string="Generación automática de lotes"
                help="Activo: el sistema crea el lote al confirmar la orden. Desactivado: el operador captura manualmente los segmentos del lote con el patrón XX-##-##-##-##.">
                <field name="simplified_mrp_auto_lot"/>
              </setting>
            </block>
          </app>
        </xpath>
      </field>
    </record>
  </data>
</odoo>```

