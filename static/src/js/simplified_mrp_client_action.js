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
            // Sale Order ahora es búsqueda
            saleOrderQuery: '',
            saleOrderResults: [],
            selectedSaleOrder: null, // Objeto {id, name}
            
            destLocations: [],
            selectedDestLocation: null, // Objeto {id, name}

            // Step 3: Components
            bomId: null,
            components: [],
            compIndex: 0,
            editingComponent: false, // Control de modo edición

            compSearchQuery: '',
            compSearchResults: [],
            newCompQty: 1.0,

            // Step 4: Lots (Multi-selección)
            lots: [],
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

    // NOTA: Ya no cargamos todas las SO al inicio, se buscan por demanda.

    async loadDestLocations() {
        if (!this.state.warehouseId) return;
        try {
            // Cargar ubicaciones internas del almacén seleccionado
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
        // Cargar datos dependientes
        await this.loadDestLocations();
    }

    // ---------- Flow Step 2: Producto & Config ----------
    selectProduct(p) {
        this.state.productId = p.id;
        this.state.productName = p.name;
        this.state.uomName = p.uom_name || p.uomName || '';
        this.state.products = [];
    }

    // --- Lógica de Búsqueda de Orden de Venta ---
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
        this.state.saleOrderQuery = so.name; // Poner nombre en el input
        this.state.saleOrderResults = []; // Ocultar lista
    }
    // ---------------------------------------------

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
            
            // Reiniciar asignaciones de lotes
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
        
        try {
            this.state.lots = await this.orm.call(
                'aq.simplified.mrp.api', 
                'get_lots', 
                [comp.product_id, this.state.warehouseId, 60], 
                {}
            );
            
            if (!this.state.assignedLots[comp.product_id]) {
                this.state.assignedLots[comp.product_id] = {};
            }
        } catch (e) {
            this.notifyError('Error cargando lotes', e);
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
            // Preparar componentes
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
            
            // Determinar origen correctamente
            // Prioridad: Objeto seleccionado > Texto escrito manualmente > null
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
                origin: originVal, // Aquí enviamos el string correcto
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
        
        // Reset SO search
        this.state.saleOrderQuery = '';
        this.state.saleOrderResults = [];
        this.state.selectedSaleOrder = null;
        
        this.state.selectedDestLocation = null;
        
        this.state.products = [];
        this.state.components = [];
        this.state.assignedLots = {};
        
        this.state.compIndex = 0;
        this.state.editingComponent = false;
        
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
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);