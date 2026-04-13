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
registry.category('actions').add('aq_simplified_mrp.client_action', SimplifiedMrp);