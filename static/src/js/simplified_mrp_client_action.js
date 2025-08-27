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
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);