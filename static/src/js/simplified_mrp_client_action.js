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
            view: 'create',
            step: 'warehouse',
            warehouses: [],
            warehouseId: null,
            products: [],
            productQuery: '',
            productId: null,
            productName: '',
            uomName: '',
            qty: 1.0,
            bomId: null,
            components: [],
            compIndex: 0,
            editingComponent: false,
            compSearchQuery: '',
            compSearchResults: [],
            newCompQty: 1.0,
            lots: [],
            chosenLots: {},
            resultMoId: null,
            resultMoName: '',
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

    getProgressWidth() {
        const steps = ['warehouse', 'product', 'components', 'lots', 'done'];
        const index = steps.indexOf(this.state.step);
        const width = ((index + 1) / steps.length) * 100;
        return `width: ${width}%`;
    }

    getCurrentStepTitle() {
        const titles = {
            warehouse: 'Paso 1: Seleccionar almacén',
            product: 'Paso 2: Producto a fabricar',
            components: 'Paso 3: Ingredientes',
            lots: 'Paso 4: Seleccionar lotes',
            done: '¡Completado!'
        };
        return titles[this.state.step] || '';
    }

    // ---------- quantity controls ----------
    increaseQty() {
        this.state.qty = this.toNum(this.state.qty) + 1;
    }

    decreaseQty() {
        const newQty = this.toNum(this.state.qty) - 1;
        this.state.qty = newQty > 0 ? newQty : 1;
    }

    increaseCompQty() {
        const c = this.state.components[this.state.compIndex];
        if (c) c.qty_required = this.toNum(c.qty_required) + 1;
    }

    decreaseCompQty() {
        const c = this.state.components[this.state.compIndex];
        if (c) {
            const newQty = this.toNum(c.qty_required) - 1;
            c.qty_required = newQty > 0 ? newQty : 0.01;
        }
    }

    // ---------- component editing ----------
    editComponent(index) {
        this.state.compIndex = index;
        this.state.editingComponent = true;
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
    }

    // ---------- data loading ----------
    async loadWarehouses() {
        try {
            this.state.warehouses = await this.orm.call('aq.simplified.mrp.api', 'get_warehouses', [], {});
        } catch (e) {
            this.notification.add(`Error cargando almacenes: ${e.data?.message || e.message || e}`, { type: 'danger' });
        }
    }

    async loadMyProductions() {
        try {
            this.state.myProductions = await this.orm.call('aq.simplified.mrp.api', 'get_my_productions', [50], {});
        } catch (e) {
            this.notification.add(`Error cargando mis órdenes: ${e.data?.message || e.message || e}`, { type: 'danger' });
        }
    }

    async loadMoDetail(moId) {
        try {
            this.state.moDetail = await this.orm.call('aq.simplified.mrp.api', 'get_production_detail', [moId], {});
            this.state.selectedMo = moId;
            this.state.view = 'detail';
        } catch (e) {
            this.notification.add(`Error cargando detalle: ${e.data?.message || e.message || e}`, { type: 'danger' });
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
            
            this.state.compSearchResults = await this.orm.call(
                'aq.simplified.mrp.api', 
                'search_components', 
                [this.state.compSearchQuery, 20], 
                {}
            );
        } catch (e) {
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

    // ---------- wizard flow ----------
    selectWarehouse(id) {
        this.state.warehouseId = id;
        this.state.step = 'product';
    }

    selectProduct(p) {
        this.state.productId = p.id;
        this.state.productName = p.name;
        this.state.uomName = p.uom_name || p.uomName || '';
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
            this.state.compIndex = 0;
            this.state.editingComponent = false;
            this.state.step = 'components';
        } catch (e) {
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
        
        if (this.state.components.length === 0) {
            this.state.compIndex = 0;
            this.state.editingComponent = false;
        } else if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
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
            this.notification.add('Agrega al menos un ingrediente', { type: 'warning' });
            return;
        }
        
        if (this.state.compIndex < this.state.components.length - 1) {
            this.state.compIndex += 1;
        } else {
            this.state.editingComponent = false;
        }
    }

    prevComponent() {
        if (this.state.compIndex > 0) {
            this.state.compIndex -= 1;
        }
    }

    backToProduct() {
        this.state.step = 'product';
        this.state.editingComponent = false;
    }

    reviewComponents() {
        this.state.compIndex = 0;
        this.state.editingComponent = true;
    }
    
    continueToLots() {
        if (!this.state.components.length) {
            this.notification.add('Agrega al menos un ingrediente', { type: 'warning' });
            return;
        }
        this.state.compIndex = 0;
        this.state.step = 'lots';
        this.loadLotsForCurrent();
    }

    async loadLotsForCurrent() {
        const comp = this.state.components[this.state.compIndex];
        if (!comp) return;
        
        try {
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_lots', 
                [comp.product_id, this.state.warehouseId, 40], 
                {}
            );
        } catch (e) {
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
            this.state.editingComponent = false;
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
            
            const res = await this.orm.call('aq.simplified.mrp.api', 'create_mo', [payload], {});
            
            this.state.resultMoId = res.mo_id || null;
            this.state.resultMoName = res.name || '';
            this.state.step = 'done';
            this.notification.add('Orden creada exitosamente', { type: 'success' });
            await this.loadMyProductions();
        } catch (e) {
            let errorMessage = 'Error desconocido';
            if (e.data && e.data.message) {
                errorMessage = e.data.message;
            } else if (e.message) {
                errorMessage = e.message;
            } else if (typeof e === 'string') {
                errorMessage = e;
            }
            this.notification.add(`Error creando orden: ${errorMessage}`, { type: 'danger' });
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
        this.state.products = [];
        this.state.components = [];
        this.state.chosenLots = {};
        this.state.compIndex = 0;
        this.state.editingComponent = false;
        this.state.qty = 1.0;
        this.state.resultMoId = null;
        this.state.resultMoName = '';
        this.state.compSearchQuery = '';
        this.state.compSearchResults = [];
        this.state.newCompQty = 1.0;
    }

    // ---------- navigation ----------
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
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);