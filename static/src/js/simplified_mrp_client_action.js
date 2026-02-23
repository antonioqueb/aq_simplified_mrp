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

            // Config del backend — default FALSE: lote manual obligatorio salvo que esté activado en config
            autoLot: false,

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
            // Solo es true si el backend explícitamente devuelve true
            this.state.autoLot = cfg.auto_lot === true;
            console.log('[SMRP] autoLot cargado desde backend:', this.state.autoLot);
        } catch (e) {
            // Si falla la carga de config, el default seguro es lote MANUAL
            console.warn('[SMRP] No se pudo cargar config MRP, asumiendo autoLot=false', e);
            this.state.autoLot = false;
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
        console.log('[SMRP] Producto seleccionado - tracking:', p.tracking, '| autoLot:', this.state.autoLot);
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

        // Requiere lote manual si: autoLot está desactivado Y el producto tiene tracking
        const needsManualLot = !this.state.autoLot && this.state.productTracking !== 'none';
        console.log('[SMRP] confirmProductAndConfig - autoLot:', this.state.autoLot,
                    '| tracking:', this.state.productTracking,
                    '| needsManualLot:', needsManualLot);

        if (needsManualLot) {
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
        console.log('[SMRP] Lote manual confirmado:', name);
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

            // Lote manual: solo si autoLot está desactivado y el producto tiene tracking
            let manualLotName = null;
            if (!this.state.autoLot && this.state.productTracking !== 'none') {
                manualLotName = this._assembleLotName();
                console.log('[SMRP] Enviando lote manual al backend:', manualLotName);
            }

            const payload = {
                warehouse_id:     this.state.warehouseId,
                product_id:       this.state.productId,
                product_qty:      this.toNum(this.state.qty),
                bom_id:           this.state.bomId,
                origin:           originVal,
                location_dest_id: this.state.selectedDestLocation?.id || null,
                components:       compsPayload,
                manual_lot_name:  manualLotName,
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
        // Nota: autoLot NO se resetea — se mantiene el valor cargado desde el backend
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
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);