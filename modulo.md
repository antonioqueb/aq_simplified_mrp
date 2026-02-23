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
    def get_lots(self, product_id, warehouse_id, limit=60, query=''):
        """Disponibilidad por lote bajo TODAS las ubicaciones internas del almacén.
        Soporta filtrado por número de lote via 'query'.
        """
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

            # Filtrar por número de lote si se proporciona query
            if query and query.strip():
                matching_lots = self.env['stock.lot'].search([
                    ('product_id', '=', product.id),
                    ('name', 'ilike', query.strip()),
                ])
                domain.append(('lot_id', 'in', matching_lots.ids))

            # Leer quants individuales — evita el límite del read_group que cortaba resultados
            quants = Quant.search(domain, limit=int(limit) * 10)

            lot_totals = {}
            for q in quants:
                lot_key = q.lot_id.id if q.lot_id else False
                if lot_key not in lot_totals:
                    lot_totals[lot_key] = {
                        'id': lot_key or -1,
                        'name': q.lot_id.name if q.lot_id else _('Sin lote / General'),
                        'qty': 0.0,
                        'reserved': 0.0,
                    }
                lot_totals[lot_key]['qty'] += q.quantity
                lot_totals[lot_key]['reserved'] += q.reserved_quantity

            out = []
            for data in lot_totals.values():
                available = data['qty'] - data['reserved']
                if available > 0:
                    out.append({
                        'id': data['id'],
                        'name': data['name'],
                        'qty_available': round(available, 4),
                    })

            out.sort(key=lambda x: x['name'] or 'ZZZZ')
            return out[:int(limit)]

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

class SimplifiedMrp extends Component {
    static props = { 
        "*": true 
    };

    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            view: 'create', // 'create' o 'list' o 'detail'
            step: 'warehouse',
            
            // Step 1: Warehouse
            warehouses: [],
            warehouseId: null,

            // Step 2: Product & Config
            products: [],
            productQuery: '',
            productId: null,
            productName: '',
            uomName: '',
            qty: 1.0,

            // Nuevos campos Step 2 (Origen y Destino)
            saleOrderQuery: '',
            saleOrderResults: [],
            selectedSaleOrder: null, // Objeto {id, name}
            
            destLocations: [],
            selectedDestLocation: null, // Objeto {id, name}

            // Step 3: Components
            bomId: null,
            components: [],
            compIndex: 0,
            editingComponent: false,

            compSearchQuery: '',
            compSearchResults: [],
            newCompQty: 1.0,

            // Step 4: Lots (Multi-selección)
            lots: [],
            lotQuery: '', // Búsqueda de lotes
            // Estructura: { [productId]: { [lotId]: qty, ... } }
            assignedLots: {}, 

            // Resultados
            resultMoId: null,
            resultMoName: '',

            // Lista y Detalle
            myProductions: [],
            selectedMo: null,
            moDetail: null,
        });

        onWillStart(async () => { 
            await this.loadWarehouses(); 
            await this.loadMyProductions();
        });
    }

    // ---------- utils ----------
    toNum(v) {
        const n = typeof v === 'number' ? v : parseFloat(v);
        return Number.isFinite(n) ? n : 0;
    }

    // ---------- data loaders ----------
    async loadWarehouses() {
        try {
            this.state.warehouses = await this.orm.call('aq.simplified.mrp.api', 'get_warehouses', [], {});
        } catch (e) {
            this.notifyError('Error cargando almacenes', e);
        }
    }

    async loadDestLocations() {
        if (!this.state.warehouseId) return;
        try {
            this.state.destLocations = await this.orm.call('aq.simplified.mrp.api', 'get_stock_locations', [this.state.warehouseId], {});
        } catch (e) {
            console.error('Error cargando ubicaciones:', e);
        }
    }

    async loadMyProductions() {
        try {
            this.state.myProductions = await this.orm.call('aq.simplified.mrp.api', 'get_my_productions', [50], {});
        } catch (e) {
            this.notifyError('Error cargando mis órdenes', e);
        }
    }

    async loadMoDetail(moId) {
        try {
            this.state.moDetail = await this.orm.call('aq.simplified.mrp.api', 'get_production_detail', [moId], {});
            this.state.selectedMo = moId;
            this.state.view = 'detail';
        } catch (e) {
            this.notifyError('Error cargando detalle', e);
        }
    }

    async searchProducts() {
        try {
            const query = this.state.productQuery || '';
            this.state.products = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_finished_products', 
                [query, 20], 
                {}
            );
        } catch (e) {
            this.notifyError('Error buscando productos', e);
        }
    }

    async searchComponents() {
        try {
            if (!this.state.compSearchQuery) {
                this.state.compSearchResults = [];
                return;
            }
            this.state.compSearchResults = await this.orm.call(
                'aq.simplified.mrp.api', 
                'search_components', 
                [this.state.compSearchQuery, 20], 
                {}
            );
        } catch (e) {
            this.notifyError('Error buscando ingredientes', e);
        }
    }

    // ---------- Flow Step 1: Almacén ----------
    async selectWarehouse(id) {
        this.state.warehouseId = id;
        this.state.step = 'product';
        await this.loadDestLocations();
    }

    // ---------- Flow Step 2: Producto & Config ----------
    selectProduct(p) {
        this.state.productId = p.id;
        this.state.productName = p.name;
        this.state.uomName = p.uom_name || p.uomName || '';
        this.state.products = [];
    }

    async searchSaleOrders() {
        const query = this.state.saleOrderQuery;
        if (!query || query.length < 2) {
            this.state.saleOrderResults = [];
            return;
        }
        try {
            this.state.saleOrderResults = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_sale_orders', 
                [query, 10], 
                {}
            );
        } catch (e) {
            console.error(e);
        }
    }

    selectSaleOrder(so) {
        this.state.selectedSaleOrder = so;
        this.state.saleOrderQuery = so.name;
        this.state.saleOrderResults = [];
    }

    async confirmProductAndConfig() {
        if (!this.state.productId) {
            this.notification.add('Selecciona un producto', { type: 'warning' });
            return;
        }
        const qty = this.toNum(this.state.qty);
        if (qty <= 0) {
            this.notification.add('Cantidad inválida', { type: 'warning' });
            return;
        }
        this.state.qty = qty;

        try {
            const res = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_bom_components', 
                [this.state.productId, qty], 
                {}
            );
            
            this.state.bomId = res.bom_id || null;
            this.state.components = (res.components || []).map(c => ({ 
                ...c, 
                qty_required: this.toNum(c.qty_required) || 1.0 
            }));
            
            this.state.assignedLots = {};
            
            this.state.compIndex = 0;
            this.state.editingComponent = false;
            this.state.step = 'components';
        } catch (e) {
            this.notifyError('Error obteniendo componentes', e);
        }
    }

    // ---------- Flow Step 3: Componentes ----------
    updateCurrentQty(ev) {
        const v = this.toNum(ev.target.value);
        const c = this.state.components[this.state.compIndex];
        if (c) c.qty_required = v;
    }

    removeCurrentComponent() {
        if (!this.state.components.length) return;
        this.state.components.splice(this.state.compIndex, 1);
        
        if (this.state.components.length === 0) {
            this.state.compIndex = 0;
            this.state.editingComponent = false;
        } else if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
            this.state.editingComponent = true;
        } else {
            this.state.editingComponent = true;
        }
    }

    addComponentFromSearch(p) {
        if (!this.state.components.find(c => c.product_id === p.id)) {
            this.state.components.push({
                product_id: p.id,
                name: p.name,
                uom_id: p.uom_id,
                uom_name: p.uom_name,
                qty_required: this.toNum(this.state.newCompQty) || 1.0,
                tracking: p.tracking || 'none',
            });
        }
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
        this.state.newCompQty = 1.0;
        this.state.compIndex = this.state.components.length - 1;
        this.state.editingComponent = true;
    }

    nextComponent() {
        if (!this.state.components.length) {
            this.notification.add('Debes agregar al menos un ingrediente', { type: 'warning' });
            return;
        }
        
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1;
            this.state.editingComponent = true;
        } else {
            this.state.editingComponent = false;
            this.state.compIndex = this.state.components.length;
        }
    }

    prevComponent() {
        if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
            this.state.editingComponent = true;
        }
    }

    backToProduct() {
        this.state.step = 'product';
    }

    reviewComponents() {
        if (this.state.components.length > 0) {
            this.state.compIndex = 0;
            this.state.editingComponent = true;
        } else {
            this.notification.add('No hay ingredientes para revisar', { type: 'warning' });
        }
    }

    continueToLots() {
        if (!this.state.components.length) {
            this.notification.add('Debes agregar al menos un ingrediente', { type: 'warning' });
            return;
        }
        this.state.compIndex = 0;
        this.state.step = 'lots';
        this.loadLotsForCurrent();
    }

    // ---------- Flow Step 4: Lotes (Multi-Select) ----------
    async loadLotsForCurrent() {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;

        // Reset búsqueda al cambiar de ingrediente
        this.state.lotQuery = '';

        try {
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_lots', 
                [comp.product_id, this.state.warehouseId, 60, ''], 
                {}
            );
            
            if (!this.state.assignedLots[comp.product_id]) {
                this.state.assignedLots[comp.product_id] = {};
            }
        } catch (e) {
            this.notifyError('Error cargando lotes', e);
        }
    }

    async searchLots() {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        try {
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api',
                'get_lots',
                [comp.product_id, this.state.warehouseId, 60, this.state.lotQuery || ''],
                {}
            );
        } catch (e) {
            this.notifyError('Error buscando lotes', e);
        }
    }

    getAssignedTotal(productId) {
        const map = this.state.assignedLots[productId] || {};
        return Object.values(map).reduce((sum, val) => sum + this.toNum(val), 0);
    }

    getLotAssignedValue(productId, lotId) {
        const map = this.state.assignedLots[productId] || {};
        return map[lotId] || 0; 
    }

    updateLotAssignment(lotId, val) {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        const qty = this.toNum(val);
        
        if (!this.state.assignedLots[comp.product_id]) {
            this.state.assignedLots[comp.product_id] = {};
        }
        
        if (qty > 0) {
            this.state.assignedLots[comp.product_id][lotId] = qty;
        } else {
            delete this.state.assignedLots[comp.product_id][lotId];
        }
        this.state.assignedLots = { ...this.state.assignedLots };
    }

    async nextLotStep() {
        const comp = this.state.components[this.state.compIndex];
        const assigned = this.getAssignedTotal(comp.product_id);

        if (comp.tracking !== 'none' && assigned <= 0) {
            this.notification.add('No has asignado ninguna cantidad a lotes.', { type: 'danger' });
            return; 
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

    // ---------- Final: Crear MO ----------
    async createMO() {
        try {
            const compsPayload = this.state.components.map(c => {
                const lotsMap = this.state.assignedLots[c.product_id] || {};
                const lotsList = Object.entries(lotsMap).map(([lid, qty]) => ({
                    lot_id: parseInt(lid),
                    qty: this.toNum(qty)
                }));

                return {
                    product_id: c.product_id,
                    qty: c.qty_required, 
                    selected_lots: lotsList
                };
            });
            
            let originVal = null;
            if (this.state.selectedSaleOrder && this.state.selectedSaleOrder.name) {
                originVal = this.state.selectedSaleOrder.name;
            } else if (this.state.saleOrderQuery) {
                originVal = this.state.saleOrderQuery;
            }

            const payload = {
                warehouse_id: this.state.warehouseId,
                product_id: this.state.productId,
                product_qty: this.toNum(this.state.qty),
                bom_id: this.state.bomId,
                origin: originVal,
                location_dest_id: this.state.selectedDestLocation ? this.state.selectedDestLocation.id : null,
                components: compsPayload,
            };
            
            console.log('Creando MO con payload:', payload);
            
            const res = await this.orm.call('aq.simplified.mrp.api', 'create_mo', [payload], {});
            
            this.state.resultMoId = res.mo_id || null;
            this.state.resultMoName = res.name || '';
            this.state.step = 'done';
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
        this.state.view = 'create';
        this.state.step = 'warehouse';
        this.state.warehouseId = null;
        
        this.state.productId = null;
        this.state.productName = '';
        this.state.qty = 1.0;
        
        this.state.saleOrderQuery = '';
        this.state.saleOrderResults = [];
        this.state.selectedSaleOrder = null;
        
        this.state.selectedDestLocation = null;
        
        this.state.products = [];
        this.state.components = [];
        this.state.assignedLots = {};
        
        this.state.compIndex = 0;
        this.state.editingComponent = false;
        
        this.state.lotQuery = '';
        
        this.state.resultMoId = null;
        this.state.resultMoName = '';
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
        this.state.newCompQty = 1.0;
    }

    // ---------- Misc / Nav ----------
    notifyError(msg, e) {
        console.error(msg, e);
        const errData = e.data?.message || e.message || e.toString();
        this.notification.add(`${msg}: ${errData}`, { type: 'danger' });
    }

    showCreate() {
        this.resetWizard();
    }

    showList() {
        this.state.view = 'list';
        this.loadMyProductions();
    }

    backToList() {
        this.state.view = 'list';
        this.state.selectedMo = null;
        this.state.moDetail = null;
    }

    getStateLabel(state) {
        const labels = {
            draft: 'Borrador',
            confirmed: 'Confirmada',
            progress: 'En progreso',
            to_close: 'Por cerrar',
            done: 'Hecha',
            cancel: 'Cancelada',
        };
        return labels[state] || state;
    }

    getStateClass(state) {
        if (state === 'done') return 'success';
        if (state === 'cancel') return 'danger';
        if (state === 'progress') return 'warning';
        return 'info';
    }
}

SimplifiedMrp.template = 'aq_simplified_mrp.Main';
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);```

## ./static/src/scss/simplified_mrp.scss
```scss
/* static/src/scss/simplified_mrp.scss */
/* ========================================================================
   SMRP - Simplified MRP System
   Versión: Odoo 18 - Vertical Scroll Optimized
   ======================================================================== */

:root {
  --smrp-brand-blue: #2737BE;
  --smrp-brand-blue-2: #3B51E0;
  --smrp-brand-blue-3: #EEF1FF;
  --smrp-brand-green: #3EF24E;
  --smrp-brand-green-2: #BFFFD0;
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
  overflow: hidden; /* Bloqueamos scroll en el padre */
  padding: 0;
}

/* Wrapper interno - ESTE ES EL QUE HACE EL SCROLL VERTICAL */
.o_smrp_wrapper {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden; /* Evitamos scroll horizontal general */
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
  align-items: flex-end; /* Alineación para etiquetas arriba */
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

/* Tarjeta de Lote con Input (Sustituye a .o_smrp_lot antigua para este caso) */
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
  &.danger { background: #ffe0e0; color: #8b0000; }
  &.warning { background: #fff4e0; color: #8b5a00; }
  &.info { background: var(--smrp-brand-blue-3); color: var(--smrp-brand-blue); }
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

/* Responsive */
@media (max-width: 767px) {
  .o_smrp_wrapper { padding: 12px; padding-bottom: 100px; }
  .o_smrp_cards { grid-template-columns: 1fr; }
  .o_smrp_lots { grid-template-columns: 1fr; }
  .o_smrp_card { max-width: 100%; }
  .o_smrp_input { min-width: 100%; }
  .o_smrp_filters .o_smrp_row { flex-direction: column; gap: 8px; }
  .o_smrp_actions { flex-direction: column; .o_smrp_btn { width: 100%; } }
  .o_smrp_steps { gap: 6px; padding: 8px; li { font-size: 12px; padding: 6px 10px; } .num { width: 22px; height: 22px; font-size: 11px; } }
}```

## ./static/src/xml/simplified_mrp_templates.xml
```xml
<templates id="template" xml:space="preserve">
  <t t-name="aq_simplified_mrp.Main">
    <div class="o_smrp o_smrp--tablet">

      <!-- Nav superior -->
      <div class="o_smrp_nav">
        <button class="o_smrp_nav_btn" t-att-class="{'active': state.view === 'create'}" t-on-click="() => this.showCreate()">➕ Nueva orden</button>
        <button class="o_smrp_nav_btn" t-att-class="{'active': state.view === 'list' || state.view === 'detail'}" t-on-click="() => this.showList()">📋 Mis órdenes</button>
      </div>

      <!-- WRAPPER PRINCIPAL -->
      <div class="o_smrp_wrapper">

        <!-- ========== VISTA CREAR ========== -->
        <t t-if="state.view === 'create'">
          <!-- Steps Header -->
          <div class="o_smrp_container">
            <ol class="o_smrp_steps" role="list">
              <li t-att-class="{'active': state.step === 'warehouse', 'done': ['product','components','lots','done'].includes(state.step)}">
                <span class="num">1</span><span>Almacén</span>
              </li>
              <li t-att-class="{'active': state.step === 'product', 'done': ['components','lots','done'].includes(state.step)}">
                <span class="num">2</span><span>Configuración</span>
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
                <!-- Buscador Producto -->
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

                <!-- Selectores Adicionales -->
                <div class="o_smrp_row" style="margin-top:10px;">
                    <!-- AUTOCOMPLETE DE ORDEN DE VENTA -->
                    <div class="o_smrp_field_group">
                        <label class="o_smrp_label">Origen (Orden de Venta)</label>
                        <div class="o_smrp_autocomplete">
                            <input class="o_smrp_input" type="text" placeholder="Escribe OP o Referencia..."
                                   t-model="state.saleOrderQuery" t-on-input="() => this.searchSaleOrders()"/>
                            
                            <div class="o_smrp_autocomplete_list" t-if="state.saleOrderResults.length">
                                <t t-foreach="state.saleOrderResults" t-as="so" t-key="so.id">
                                    <div class="item" t-on-click="() => this.selectSaleOrder(so)">
                                        <t t-esc="so.name"/>
                                    </div>
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
                          t-on-click="() => this.confirmProductAndConfig()">Siguiente</button>
                </div>
              </div>

              <!-- Lista Resultados Productos -->
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

          <!-- PASO 3: INGREDIENTES -->
          <t t-if="state.step === 'components'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Ingredientes</h2>
              
              <!-- MODO BÚSQUEDA -->
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

              <!-- MODO EDICIÓN -->
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

              <!-- ACTIONS BAR -->
              <div class="o_smrp_actions o_smrp_actions--sticky" t-if="state.components.length">
                 <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.backToProduct()">← Volver</button>
                 <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.reviewComponents()">📝 Revisar</button>
                 <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.continueToLots()">✓ Ir a Lotes</button>
              </div>
            </div>
          </t>

          <!-- PASO 4: LOTES MULTI-SELECCION -->
          <t t-if="state.step === 'lots'">
            <div class="o_smrp_container o_smrp_section">
              <h2>Selección de Lotes</h2>
              <t t-if="state.components.length">
                <div class="o_smrp_wizard">
                  <t t-set="c" t-value="state.components[state.compIndex]"/>
                  <div class="o_smrp_counter">
                      Ingrediente <t t-esc="state.compIndex + 1"/> de <t t-esc="state.components.length"/>
                  </div>
                  
                  <div class="o_smrp_box o_smrp_box--big">
                    <div class="o_smrp_header_flex">
                        <div class="o_smrp_name"><t t-esc="c.name"/></div>
                        <div class="o_smrp_progress_text">
                            <t t-set="assigned" t-value="this.getAssignedTotal(c.product_id)"/>
                            Asignado: <strong><t t-esc="assigned.toFixed(2)"/></strong> / <t t-esc="c.qty_required"/> <t t-esc="c.uom_name"/>
                        </div>
                    </div>

                    <!-- Barra de progreso -->
                    <div class="o_smrp_progress_bar">
                        <t t-set="assigned" t-value="this.getAssignedTotal(c.product_id)"/>
                        <div class="o_smrp_progress_fill" 
                             t-att-style="'width:' + Math.min((assigned / (c.qty_required || 1))*100, 100) + '%'"></div>
                    </div>

                    <!-- Buscador de lotes -->
                    <div class="o_smrp_lot_search">
                        <input class="o_smrp_input" type="text" placeholder="🔍 Buscar lote por número..."
                               t-model="state.lotQuery" t-on-input="() => this.searchLots()"/>
                    </div>

                    <div class="o_smrp_lots">
                      <t t-foreach="state.lots" t-as="l" t-key="l.id">
                        <!-- Tarjeta con Input -->
                        <div class="o_smrp_lot_input_card" t-att-class="{'active': this.getLotAssignedValue(c.product_id, l.id) > 0}">
                          <div class="head">
                             <div class="name"><t t-esc="l.name"/></div>
                             <div class="avail">Disp: <t t-esc="l.qty_available"/></div>
                          </div>
                          <div class="input-area">
                             <label>Usar:</label>
                             <input type="number" min="0" step="0.001" 
                                    class="o_smrp_input_small"
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
                      <t t-if="state.compIndex === 0">← Volver a Ingredientes</t>
                      <t t-else="">← Anterior</t>
                    </button>
                    <button class="o_smrp_btn confirm o_smrp_btn--xl" t-on-click="() => this.nextLotStep()">
                      <t t-if="state.compIndex &lt; state.components.length - 1">Siguiente Ingrediente →</t>
                      <t t-else="">✓ Crear Orden</t>
                    </button>
                  </div>
                </div>
              </t>
            </div>
          </t>

          <!-- PASO 5: DONE -->
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

        <!-- ========== VISTA LISTA ========== -->
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

        <!-- ========== VISTA DETALLE ========== -->
        <t t-if="state.view === 'detail' &amp;&amp; state.moDetail">
          <div class="o_smrp_container o_smrp_section">
            <div class="o_smrp_actions o_smrp_actions--center">
              <button class="o_smrp_btn o_smrp_btn--ghost" t-on-click="() => this.backToList()">← Volver a lista</button>
            </div>
            <h2><t t-esc="state.moDetail.name"/></h2>
            <div class="o_smrp_box o_smrp_box--big">
                <div class="o_smrp_detail_row">
                    <span class="o_smrp_detail_label">Origen:</span>
                    <strong><t t-esc="state.moDetail.origin || 'N/A'"/></strong>
                </div>
                <div class="o_smrp_detail_row">
                    <span class="o_smrp_detail_label">Estado:</span>
                    <span class="o_smrp_list_badge" t-att-class="this.getStateClass(state.moDetail.state)">
                        <t t-esc="this.getStateLabel(state.moDetail.state)"/>
                    </span>
                </div>
                <div class="o_smrp_detail_row">
                    <span class="o_smrp_detail_label">Producto:</span>
                    <strong><t t-esc="state.moDetail.product_name"/></strong>
                </div>
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

