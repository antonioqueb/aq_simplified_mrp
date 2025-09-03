## ./__init__.py
```py
from . import models
```

## ./__manifest__.py
```py
# -*- coding: utf-8 -*-
{
    'name': 'AQ Simplified MRP',
    'version': '18.0.1.0.0',
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
        'views/client_action.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
}```

## ./models/__init__.py
```py
from . import simplified_mrp_api
```

## ./models/simplified_mrp_api.py
```py
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
            
            # Llamar search() SIN pasar kwargs adicionales
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
            
            # Llamar search() SIN pasar kwargs adicionales
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

            # ============== AUTOMATIZACIÓN COMPLETA DE LA MO ==============
            
            # 1. Crear el move line para el producto terminado si no existe
            if not mo.move_finished_ids.move_line_ids:
                # Buscar un lote existente o crear uno nuevo para el producto terminado
                finished_lot = None
                if product.tracking in ['lot', 'serial']:
                    # Buscar si hay un lote disponible o crear uno nuevo
                    Lot = self.env['stock.lot']
                    finished_lot = Lot.search([
                        ('product_id', '=', product.id),
                        ('company_id', '=', mo.company_id.id)
                    ], limit=1)
                    
                    if not finished_lot:
                        # Crear un nuevo lote
                        finished_lot = Lot.create({
                            'name': f"{mo.name}-{product.default_code or product.name}",
                            'product_id': product.id,
                            'company_id': mo.company_id.id,
                        })
                        self._logdbg("Lote creado para producto terminado", finished_lot.id, finished_lot.name)

                # Crear move line para el producto terminado
                finished_move_line = self.env['stock.move.line'].create({
                    'move_id': mo.move_finished_ids[0].id,
                    'company_id': mo.company_id.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'location_id': mo.location_src_id.id,
                    'location_dest_id': mo.location_dest_id.id,
                    'lot_id': finished_lot.id if finished_lot else False,
                    'quantity': qty,  # Odoo 18
                })
                self._logdbg("Move line creado para producto terminado", finished_move_line.id, "lot", finished_lot.id if finished_lot else None)

            # 2. Marcar como iniciado
            try:
                mo.action_toggle_is_locked()  # Desbloquear para permitir modificaciones
                mo.is_locked = False
                mo.action_toggle_is_locked()  # Iniciar producción
                self._logdbg("MO iniciado", mo.state)
            except Exception as start_error:
                self._logdbg("Error iniciando MO", start_error)
                _logger.warning("Could not start production automatically: %s", start_error)

            # 3. Completar automáticamente
            try:
                # Validar todos los movimientos
                for move in mo.move_raw_ids:
                    if move.state not in ['done', 'cancel']:
                        move._action_done()
                        self._logdbg("Move raw validado", move.id, move.product_id.display_name)
                
                for move in mo.move_finished_ids:
                    if move.state not in ['done', 'cancel']:
                        move._action_done()
                        self._logdbg("Move finished validado", move.id, move.product_id.display_name)

                # Marcar MO como hecha
                if mo.state != 'done':
                    mo.action_done()
                    self._logdbg("MO marcada como hecha", mo.state)

            except Exception as complete_error:
                self._logdbg("Error completando MO", complete_error)
                _logger.warning("Could not complete production automatically: %s", complete_error)
                # Si no se puede completar automáticamente, al menos intentar ponerla en progreso
                try:
                    if mo.state == 'confirmed':
                        mo.action_toggle_is_locked()
                        self._logdbg("MO al menos iniciada", mo.state)
                except Exception:
                    pass

            # ============== FIN DE AUTOMATIZACIÓN ==============

            self._toast(_("DBG MO creada y completada: %(n)s | Estado: %(s)s | Líneas: %(c)d",
                          n=mo.name, s=mo.state, c=len(mo.move_raw_ids)), level='success')

            return {'mo_id': mo.id, 'name': mo.name, 'state': mo.state}

        except Exception as e:
            _logger.error("Error creating MO: %s", e)
            self._toast(_("DBG Error creando OP: %s") % e, level='warning', sticky=True)
            raise UserError(_('Error creando orden de producción: %s') % e)```

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

class SimplifiedMrp extends Component {
    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            step: 'warehouse',
            warehouses: [],
            warehouseId: null,

            products: [],
            productQuery: '',
            productId: null,
            productName: '',
            uomName: '',
            qty: 1.0, // puede ser string desde el input; se sanea con toNum()

            bomId: null,
            components: [],              // [{product_id,name,uom_name,qty_required,tracking}]
            compIndex: 0,

            // búsqueda/alta de ingredientes
            compSearchQuery: '',
            compSearchResults: [],
            newCompQty: 1.0,

            lots: [],
            chosenLots: {},              // product_id -> lot_id

            resultMoId: null,
            resultMoName: '',
        });

        onWillStart(async () => { await this.loadWarehouses(); });
    }

    // ---------- utils ----------
    toNum(v) {
        const n = typeof v === 'number' ? v : parseFloat(v);
        return Number.isFinite(n) ? n : 0;
    }

    // ---------- data ----------
    async loadWarehouses() {
        try {
            console.log('Cargando almacenes...');
            this.state.warehouses = await this.orm.call('aq.simplified.mrp.api', 'get_warehouses', [], {});
            console.log('Almacenes cargados:', this.state.warehouses);
        } catch (e) {
            console.error('Error completo cargando almacenes:', e);
            console.error('Error data:', e.data);
            console.error('Error message:', e.message);
            this.notification.add(`Error cargando almacenes: ${e.data?.message || e.message || e}`, { type: 'danger' });
        }
    }

    async searchProducts() {
        try {
            console.log('Buscando productos con query:', this.state.productQuery);
            
            // Asegurar que siempre enviamos string vacío si no hay query
            const query = this.state.productQuery || '';
            
            this.state.products = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_finished_products', 
                [query, 20], 
                {}
            );
            console.log('Productos encontrados:', this.state.products);
        } catch (e) {
            console.error('Error completo buscando productos:', e);
            console.error('Error data:', e.data);
            console.error('Error message:', e.message);
            console.error('Error type:', e.type);
            
            let errorMessage = 'Error desconocido';
            if (e.data && e.data.message) {
                errorMessage = e.data.message;
            } else if (e.message) {
                errorMessage = e.message;
            } else if (typeof e === 'string') {
                errorMessage = e;
            }
            
            this.notification.add(`Error buscando productos: ${errorMessage}`, { type: 'danger' });
        }
    }

    async searchComponents() {
        try {
            if (!this.state.compSearchQuery) {
                this.state.compSearchResults = [];
                return;
            }
            
            console.log('Buscando componentes con query:', this.state.compSearchQuery);
            
            this.state.compSearchResults = await this.orm.call(
                'aq.simplified.mrp.api', 
                'search_components', 
                [this.state.compSearchQuery, 20], 
                {}
            );
            console.log('Componentes encontrados:', this.state.compSearchResults);
        } catch (e) {
            console.error('Error completo buscando ingredientes:', e);
            console.error('Error data:', e.data);
            console.error('Error message:', e.message);
            
            let errorMessage = 'Error desconocido';
            if (e.data && e.data.message) {
                errorMessage = e.data.message;
            } else if (e.message) {
                errorMessage = e.message;
            } else if (typeof e === 'string') {
                errorMessage = e;
            }
            
            this.notification.add(`Error buscando ingredientes: ${errorMessage}`, { type: 'danger' });
        }
    }

    // ---------- flow ----------
    selectWarehouse(id) {
        this.state.warehouseId = id;
        this.state.step = 'product';
    }

    selectProduct(p) {
        this.state.productId = p.id;
        this.state.productName = p.name;
        this.state.uomName = p.uom_name || p.uomName || '';
        this.state.products = [];
    }

    async confirmProductAndQty() {
        if (!this.state.productId) {
            this.notification.add('Selecciona un producto', { type: 'warning' });
            return;
        }
        const qty = this.toNum(this.state.qty);
        if (qty <= 0) {
            this.notification.add('Cantidad inválida', { type: 'warning' });
            return;
        }
        this.state.qty = qty; // normaliza

        try {
            console.log('Obteniendo componentes BOM para producto:', this.state.productId, 'cantidad:', qty);
            
            const res = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_bom_components', 
                [this.state.productId, qty], 
                {}
            );
            
            console.log('Resultado BOM:', res);
            
            this.state.bomId = res.bom_id || null;
            this.state.components = (res.components || []).map(c => ({ 
                ...c, 
                qty_required: this.toNum(c.qty_required) || 1.0 
            }));
            this.state.compIndex = 0;
            this.state.step = 'components';
        } catch (e) {
            console.error('Error completo obteniendo componentes:', e);
            console.error('Error data:', e.data);
            console.error('Error message:', e.message);
            
            let errorMessage = 'Error desconocido';
            if (e.data && e.data.message) {
                errorMessage = e.data.message;
            } else if (e.message) {
                errorMessage = e.message;
            } else if (typeof e === 'string') {
                errorMessage = e;
            }
            
            this.notification.add(`Error obteniendo componentes: ${errorMessage}`, { type: 'danger' });
        }
    }

    updateCurrentQty(ev) {
        const v = this.toNum(ev.target.value);
        const c = this.state.components[this.state.compIndex];
        if (c) c.qty_required = v;
    }

    removeCurrentComponent() {
        if (!this.state.components.length) return;
        this.state.components.splice(this.state.compIndex, 1);
        if (this.state.compIndex > 0) this.state.compIndex -= 1;
    }

    addComponentFromSearch(p) {
        if (!this.state.components.find(c => c.product_id === p.id)) {
            this.state.components.push({
                product_id: p.id,
                name: p.name,
                uom_id: p.uom_id,
                uom_name: p.uom_name,
                qty_required: this.toNum(this.state.newCompQty) || 1.0,
                tracking: 'none',
            });
        }
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
        this.state.newCompQty = 1.0;
        this.state.compIndex = this.state.components.length - 1;
    }

    nextComponent() {
        if (!this.state.components.length) {
            this.notification.add('Debes agregar al menos un ingrediente', { type: 'warning' });
            return;
        }
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1;
        } else {
            this.state.compIndex = 0;
            this.state.step = 'lots';
            this.loadLotsForCurrent();
        }
    }

    prevComponent() {
        if (this.state.compIndex > 0) this.state.compIndex -= 1;
    }

    async loadLotsForCurrent() {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        
        try {
            console.log('Cargando lotes para producto:', comp.product_id, 'en almacén:', this.state.warehouseId);
            
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_lots', 
                [comp.product_id, this.state.warehouseId, 40], 
                {}
            );
            
            console.log('Lotes cargados:', this.state.lots);
        } catch (e) {
            console.error('Error completo cargando lotes:', e);
            console.error('Error data:', e.data);
            console.error('Error message:', e.message);
            
            let errorMessage = 'Error desconocido';
            if (e.data && e.data.message) {
                errorMessage = e.data.message;
            } else if (e.message) {
                errorMessage = e.message;
            } else if (typeof e === 'string') {
                errorMessage = e;
            }
            
            this.notification.add(`Error cargando lotes: ${errorMessage}`, { type: 'danger' });
        }
    }

    chooseLot(lotId) {
        const comp = this.state.components[this.state.compIndex];
        if (comp) this.state.chosenLots[comp.product_id] = lotId;
    }

    async nextLot() {
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1;
            await this.loadLotsForCurrent();
        } else {
            await this.createMO();
        }
    }

    async prevLot() {
        if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
            await this.loadLotsForCurrent();
        } else {
            this.state.step = 'components';
        }
    }

    async createMO() {
        try {
            const comps = this.state.components.map(c => ({
                product_id: c.product_id,
                qty: this.toNum(c.qty_required),
                lot_id: this.state.chosenLots[c.product_id] || false,
            }));
            
            const payload = {
                warehouse_id: this.state.warehouseId,
                product_id: this.state.productId,
                product_qty: this.toNum(this.state.qty),
                bom_id: this.state.bomId,
                components: comps,
            };
            
            console.log('Creando MO con payload:', payload);
            
            const res = await this.orm.call('aq.simplified.mrp.api', 'create_mo', [payload], {});
            
            console.log('MO creada:', res);
            
            this.state.resultMoId = res.mo_id || null;
            this.state.resultMoName = res.name || '';
            this.state.step = 'done';
            this.notification.add('Orden de producción creada exitosamente', { type: 'success' });
        } catch (e) {
            console.error('Error completo creando MO:', e);
            console.error('Error data:', e.data);
            console.error('Error message:', e.message);
            
            let errorMessage = 'Error desconocido';
            if (e.data && e.data.message) {
                errorMessage = e.data.message;
            } else if (e.message) {
                errorMessage = e.message;
            } else if (typeof e === 'string') {
                errorMessage = e;
            }
            
            this.notification.add(`Error creando orden de producción: ${errorMessage}`, { type: 'danger' });
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
        this.state.step = 'warehouse';
        this.state.warehouseId = null;
        this.state.productId = null;
        this.state.productName = '';
        this.state.products = [];
        this.state.components = [];
        this.state.chosenLots = {};
        this.state.compIndex = 0;
        this.state.qty = 1.0;
        this.state.resultMoId = null;
        this.state.resultMoName = '';
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
        this.state.newCompQty = 1.0;
    }
}
SimplifiedMrp.template = 'aq_simplified_mrp.Main';
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);```

## ./static/src/scss/simplified_mrp.scss
```scss
/* static/src/scss/simplified_mrp.scss */
:root{
  --brand-blue:#2737BE; --brand-blue-2:#3B51E0; --brand-blue-3:#EEF1FF;
  --brand-green:#3EF24E; --brand-green-2:#BFFFD0;
  --ink:#0b1426; --bg:#F6F8FF; --card:#fff;
  --radius:18px; --shadow:0 8px 24px rgba(23,35,79,.06);
  --tap:56px; --gap:16px; --maxw:1024px;
  --fs-base:18px; --fs-lg:20px; --fs-xl:28px;
}

.o_smrp{ background:var(--bg); color:var(--ink); min-height:100%; padding:16px; }
.o_smrp_container{ max-width:var(--maxw); margin:0 auto; display:grid; gap:var(--gap); }
.o_smrp_section{ padding:12px; }

/* Steps */
.o_smrp_steps{ display:flex; align-items:center; justify-content:center; gap:10px; list-style:none; padding:0; margin:0 0 8px; flex-wrap:wrap; }
.o_smrp_steps li{ display:flex; align-items:center; gap:8px; padding:10px 14px; border:1px solid #cfd6ea; border-radius:999px; background:#fff; font-size:14px; }
.o_smrp_steps .num{ width:28px; height:28px; border-radius:999px; display:inline-grid; place-items:center; background:#e8ecff; color:#2737BE; font-weight:700; }
.o_smrp_steps li.active{ border-color:var(--brand-blue); background:var(--brand-blue-3); color:var(--brand-blue); }
.o_smrp_steps li.done{ border-color:var(--brand-green); background:var(--brand-green-2); color:#0c3a15; }

/* Headings */
.o_smrp h2{ font-size:var(--fs-xl); color:var(--brand-blue); text-align:center; margin:4px 0 6px; }

/* Inputs */
.o_smrp_row{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:center; }
.o_smrp_label{ font-size:var(--fs-base); min-width:120px; }
.o_smrp_input{ padding:14px 16px; border:1px solid #d3d8e3; border-radius:var(--radius); min-width:220px; font-size:var(--fs-base); background:#fff; }
.o_smrp_input--xl{ font-size:var(--fs-lg); }
.o_smrp_input:focus{ outline:none; border-color:var(--brand-blue); box-shadow:0 0 0 3px var(--brand-blue-3); }

/* Buttons */
.o_smrp_btn{ height:var(--tap); padding:0 20px; border-radius:var(--radius); border:1px solid var(--brand-blue);
  background:var(--brand-blue); color:#fff; font-size:var(--fs-lg); cursor:pointer; box-shadow:var(--shadow); }
.o_smrp_btn:hover{ filter:brightness(1.04); }
.o_smrp_btn:disabled{ opacity:.5; cursor:not-allowed; }
.o_smrp_btn--ghost{ background:#fff; color:var(--brand-blue); }
.o_smrp_btn--xl{ min-width:180px; }
.o_smrp_btn.confirm{ background:var(--brand-green); border-color:var(--brand-green); color:#062b10; }

/* Actions bar */
.o_smrp_actions{ display:flex; gap:12px; justify-content:center; align-items:center; flex-wrap:wrap; }
.o_smrp_actions--end{ justify-content:flex-end; }
.o_smrp_actions--center{ justify-content:center; }
.o_smrp_actions--sticky{ position:sticky; bottom:12px; padding:8px; background:linear-gradient(180deg, rgba(246,248,255,0) 0%, rgba(246,248,255,.95) 40%); }

/* Cards */
.o_smrp_cards{ display:grid; gap:14px; grid-template-columns:repeat(auto-fill, minmax(240px, 1fr)); }
.o_smrp_cards--center{ justify-items:center; }
.o_smrp_card{ width:100%; max-width:320px; border:1px solid #d3d8e3; border-radius:var(--radius); background:var(--card);
  box-shadow:var(--shadow); padding:18px; text-align:center; position:relative; }
.o_smrp_card_icon{ font-size:28px; margin-bottom:6px; }
.o_smrp_card_title{ font-weight:700; font-size:20px; }
.o_smrp_card_sub{ font-size:13px; opacity:.75; }
.o_smrp_card.selectable{ cursor:pointer; }
.o_smrp_card.selectable:hover{ border-color:var(--brand-blue-2); box-shadow:0 0 0 3px var(--brand-blue-3); }
.o_smrp_card.selectable.selected{ border-color:var(--brand-green); box-shadow:0 0 0 3px var(--brand-green-2); }
.o_smrp_check{ position:absolute; right:12px; top:12px; width:28px; height:28px; border-radius:999px; background:var(--brand-green); color:#062b10; font-weight:800; display:none; align-items:center; justify-content:center; }
.o_smrp_card.selectable.selected .o_smrp_check{ display:flex; }

/* Boxes & wizard */
.o_smrp_wizard{ display:grid; gap:14px; }
.o_smrp_box{ border:1px dashed #c4cbe0; border-radius:var(--radius); background:#fff; padding:16px; }
.o_smrp_box--big{ padding:22px; }
.o_smrp_name{ font-weight:800; font-size:20px; margin-bottom:6px; }
.o_smrp_meta{ font-size:14px; opacity:.8; }
.o_smrp_counter{ text-align:center; font-size:14px; opacity:.8; }

/* Lotes */
.o_smrp_lots{ display:grid; gap:12px; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); }
.o_smrp_lot{ border:1px solid #d3d8e3; border-radius:var(--radius); background:#fff; padding:16px;
  display:flex; align-items:center; justify-content:space-between; gap:12px; cursor:pointer; box-shadow:var(--shadow); position:relative; }
.o_smrp_lot:hover{ border-color:var(--brand-blue-2); }
.o_smrp_lot.selected{ border-color:var(--brand-green); box-shadow:inset 0 0 0 3px var(--brand-green-2); }
.o_smrp_lot .name{ font-weight:700; font-size:18px; }
.o_smrp_lot .qty{ font-size:16px; opacity:.9; }
.o_smrp_lot .o_smrp_check{ position:absolute; right:10px; top:10px; width:26px; height:26px; display:none; background:var(--brand-green); color:#062b10; }
.o_smrp_lot.selected .o_smrp_check{ display:flex; }

/* States */
.o_smrp_selected{ text-align:center; margin-top:6px; font-size:16px; }
.badge{ display:inline-block; background:var(--brand-blue-3); color:var(--brand-blue); padding:4px 10px; border-radius:999px; margin-right:6px; font-weight:700; }
.o_smrp_empty{ opacity:.7; font-style:italic; text-align:center; }
.o_smrp_empty--block{ padding:14px; border:1px dashed #cfd6ea; border-radius:var(--radius); background:#fff; }

/* Done */
.o_smrp_done{ text-align:center; }
.o_smrp_done_icon{ font-size:48px; }

/* Tablet tweaks */
@media (min-width:768px){
  :root{ --fs-base:19px; --fs-lg:21px; --fs-xl:32px; }
  .o_smrp_input{ min-width:260px; }
  .o_smrp_cards{ grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); }
}
@media (min-width:1024px){
  :root{ --fs-base:20px; --fs-lg:22px; --fs-xl:34px; }
  .o_smrp_input{ min-width:300px; }
  .o_smrp_cards{ grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); }
}

```

## ./static/src/xml/simplified_mrp_templates.xml
```xml
<!-- static/src/xml/simplified_mrp.xml -->
<templates id="template" xml:space="preserve">
  <t t-name="aq_simplified_mrp.Main">
    <div class="o_smrp o_smrp--tablet">

      <!-- Header + Progreso -->
      <div class="o_smrp_container">
        <ol class="o_smrp_steps" role="list">
          <li t-att-class="{'active': state.step === 'warehouse', 'done': ['product','components','lots','done'].includes(state.step)}" aria-current="step">
            <span class="num">1</span><span>Almacén</span>
          </li>
          <li t-att-class="{'active': state.step === 'product', 'done': ['components','lots','done'].includes(state.step)}">
            <span class="num">2</span><span>Producto</span>
          </li>
          <li t-att-class="{'active': state.step === 'components', 'done': ['lots','done'].includes(state.step)}">
            <span class="num">3</span><span>Ingredientes</span>
          </li>
          <li t-att-class="{'active': state.step === 'lots', 'done': ['done'].includes(state.step)}">
            <span class="num">4</span><span>Lotes</span>
          </li>
          <li t-att-class="{'active': state.step === 'done'}">
            <span class="num">5</span><span>Listo</span>
          </li>
        </ol>
      </div>

      <!-- Paso: Almacén -->
      <t t-if="state.step === 'warehouse'">
        <div class="o_smrp_container o_smrp_section">
          <h2>Selecciona almacén</h2>
          <div class="o_smrp_cards o_smrp_cards--center">
            <t t-foreach="state.warehouses" t-as="w" t-key="w.id">
              <div class="o_smrp_card selectable"
                   t-on-click="() => this.selectWarehouse(w.id)"
                   role="button" tabindex="0" aria-pressed="false">
                <div class="o_smrp_card_icon">🏬</div>
                <div class="o_smrp_card_title"><t t-esc="w.name"/></div>
                <div class="o_smrp_card_sub" t-if="w.code"><t t-esc="w.code"/></div>
              </div>
            </t>
          </div>
          <div class="o_smrp_empty" t-if="!state.warehouses.length">Sin almacenes disponibles.</div>
        </div>
      </t>

      <!-- Paso: Producto -->
      <t t-if="state.step === 'product'">
        <div class="o_smrp_container o_smrp_section">
          <h2>Producto terminado</h2>

          <div class="o_smrp_filters">
            <div class="o_smrp_row">
              <input class="o_smrp_input o_smrp_input--xl" type="text" placeholder="Buscar producto…"
                     t-model="state.productQuery" t-on-input="() => this.searchProducts()" autocomplete="off"/>
              <input class="o_smrp_input o_smrp_input--xl" type="number" inputmode="decimal" min="0" step="0.01"
                     t-model="state.qty" placeholder="Cantidad"/>
              <button class="o_smrp_btn confirm o_smrp_btn--xl"
                      t-att-disabled="!(state.productId &amp;&amp; this.toNum(state.qty) > 0)"
                      t-on-click="() => this.confirmProductAndQty()">Siguiente</button>
            </div>
          </div>

          <div class="o_smrp_cards o_smrp_cards--center">
            <t t-foreach="state.products" t-as="p" t-key="p.id">
              <div class="o_smrp_card selectable"
                   t-att-class="{'selected': state.productId === p.id}"
                   t-on-click="() => this.selectProduct(p)"
                   role="button" tabindex="0"
                   t-att-aria-pressed="state.productId === p.id">
                <div class="o_smrp_card_icon">📦</div>
                <div class="o_smrp_card_title"><t t-esc="p.name"/></div>
                <div class="o_smrp_card_sub"><t t-esc="p.uom_name || p.uomName"/></div>
                <div class="o_smrp_check" aria-hidden="true">✔</div>
              </div>
            </t>
          </div>
          <div class="o_smrp_empty" t-if="!state.products.length">Sin resultados.</div>

          <div class="o_smrp_selected" t-if="state.productName">
            <span class="badge">Seleccionado</span>
            <strong><t t-esc="state.productName"/></strong>
            <t t-if="state.uomName"> — <t t-esc="state.uomName"/></t>
            <t t-if="state.qty"> • Cant.: <strong><t t-esc="state.qty"/></strong></t>
          </div>
        </div>
      </t>

      <!-- Paso: Ingredientes -->
      <t t-if="state.step === 'components'">
        <div class="o_smrp_container o_smrp_section">
          <h2>Ingredientes</h2>

          <t t-if="state.components.length">
            <div class="o_smrp_wizard">
              <div class="o_smrp_counter">Ingrediente <t t-esc="state.compIndex + 1"/> de <t t-esc="state.components.length"/></div>
              <t t-set="c" t-value="state.components[state.compIndex]"/>

              <div class="o_smrp_box o_smrp_box--big">
                <div class="o_smrp_name"><t t-esc="c.name"/></div>
                <div class="o_smrp_meta">UM: <t t-esc="c.uom_name"/></div>
                <div class="o_smrp_row">
                  <label class="o_smrp_label">Cantidad</label>
                  <input class="o_smrp_input o_smrp_input--xl" type="number" inputmode="decimal" min="0" step="0.0001"
                         t-att-value="c.qty_required"
                         t-on-input="(ev) => this.updateCurrentQty(ev)"/>
                </div>
              </div>

              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl"
                        t-on-click="() => this.removeCurrentComponent()">Quitar</button>
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl"
                        t-att-disabled="state.compIndex === 0"
                        t-on-click="() => this.prevComponent()">Atrás</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl"
                        t-on-click="() => this.nextComponent()">Siguiente</button>
              </div>
            </div>
          </t>

          <t t-if="!state.components.length">
            <div class="o_smrp_empty">No hay lista de materiales. Agrega ingredientes antes de continuar.</div>
          </t>

          <div class="o_smrp_box">
            <div class="o_smrp_name">Agregar ingrediente</div>
            <div class="o_smrp_row">
              <input class="o_smrp_input o_smrp_input--xl" type="text" placeholder="Buscar…"
                     t-model="state.compSearchQuery" t-on-input="() => this.searchComponents()" autocomplete="off"/>
              <input class="o_smrp_input o_smrp_input--xl" type="number" inputmode="decimal" min="0" step="0.0001"
                     t-model="state.newCompQty" placeholder="Cantidad"/>
            </div>
            <div class="o_smrp_cards o_smrp_cards--center">
              <t t-foreach="state.compSearchResults" t-as="p" t-key="p.id">
                <div class="o_smrp_card selectable"
                     t-on-click="() => this.addComponentFromSearch(p)"
                     role="button" tabindex="0" aria-pressed="false">
                  <div class="o_smrp_card_icon">➕</div>
                  <div class="o_smrp_card_title"><t t-esc="p.name"/></div>
                  <div class="o_smrp_card_sub"><t t-esc="p.uom_name"/></div>
                </div>
              </t>
            </div>
            <div class="o_smrp_empty" t-if="!state.compSearchResults.length &amp;&amp; state.compSearchQuery">Sin coincidencias.</div>

            <div class="o_smrp_actions o_smrp_actions--end" t-if="state.components.length">
              <button class="o_smrp_btn confirm o_smrp_btn--xl"
                      t-on-click="() => { state.step = 'lots'; this.loadLotsForCurrent(); }">Continuar a lotes</button>
            </div>
          </div>
        </div>
      </t>

      <!-- Paso: Lotes -->
      <t t-if="state.step === 'lots'">
        <div class="o_smrp_container o_smrp_section">
          <h2>Lotes por ingrediente</h2>

          <t t-if="state.components.length">
            <div class="o_smrp_wizard">
              <div class="o_smrp_counter">Ingrediente <t t-esc="state.compIndex + 1"/> de <t t-esc="state.components.length"/></div>
              <t t-set="c" t-value="state.components[state.compIndex]"/>

              <div class="o_smrp_box o_smrp_box--big">
                <div class="o_smrp_name"><t t-esc="c.name"/></div>
                <div class="o_smrp_meta">Requerido: <strong><t t-esc="c.qty_required"/></strong> <t t-esc="c.uom_name"/></div>

                <div class="o_smrp_lots">
                  <t t-foreach="state.lots" t-as="l" t-key="l.id">
                    <div class="o_smrp_lot"
                         t-att-class="{
                           selected: state.chosenLots &amp;&amp; state.chosenLots[c.product_id] === l.id
                         }"
                         t-on-click="() => this.chooseLot(l.id)"
                         role="button" tabindex="0"
                         t-att-aria-pressed="state.chosenLots &amp;&amp; state.chosenLots[c.product_id] === l.id">
                      <div class="name"><t t-esc="l.name"/></div>
                      <div class="qty"><strong><t t-esc="l.qty_available"/></strong> disp.</div>
                      <div class="o_smrp_check">✔</div>
                    </div>
                  </t>
                  <t t-if="!state.lots.length">
                    <div class="o_smrp_empty o_smrp_empty--block">Sin lotes con stock en el almacén.</div>
                  </t>
                </div>
              </div>

              <div class="o_smrp_actions o_smrp_actions--sticky">
                <button class="o_smrp_btn o_smrp_btn--ghost o_smrp_btn--xl" t-on-click="() => this.prevLot()">Atrás</button>
                <button class="o_smrp_btn confirm o_smrp_btn--xl"
                        t-att-disabled="!(state.chosenLots &amp;&amp; state.chosenLots[c.product_id])"
                        t-on-click="() => this.nextLot()">Siguiente</button>
              </div>
            </div>
          </t>
        </div>
      </t>

      <!-- Paso: Done -->
      <t t-if="state.step === 'done'">
        <div class="o_smrp_container o_smrp_section o_smrp_done">
          <div class="o_smrp_done_icon">🎉</div>
          <h2>¡Orden creada!</h2>
          <div class="o_smrp_box">
            El proceso se completó correctamente. Puedes crear una nueva orden de producción.
          </div>
          <div class="o_smrp_actions o_smrp_actions--center">
            <button class="o_smrp_btn confirm o_smrp_btn--xl"
                    t-att-autofocus="true"
                    t-on-click="() => this.resetWizard()">Crear nueva orden</button>
          </div>
        </div>
      </t>

    </div>
  </t>
</templates>
```

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

