import { registry } from "@web/core/registry";
import { Component, onMounted, onWillStart, onWillUnmount, proxy, signal, usePlugin, useProps } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { DialogPlugin } from "@web/core/dialog/dialog_plugin";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { _t } from "@web/core/l10n/translation";
import { ActionPlugin, standardActionServiceProps } from "@web/webclient/actions/action_plugin";

/** Rounding of summed quantities (weights are in kg with grams). */
const QTY_DIGITS = 1000;

export class ScrapBarcodeDashboard extends Component {
    static template = "mrp_barcode_scrap.Dashboard";

    // Owl 3: props through useProps (static props would throw), refs are signals
    props = useProps(standardActionServiceProps);
    barcodeInput = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.action = usePlugin(ActionPlugin);
        this.notification = usePlugin(NotificationPlugin);
        this.dialog = usePlugin(DialogPlugin);

        this.state = proxy({
            // Scanning state
            lines: [],          // [{product_id, product_name, quantity, uom, unit_price, barcode_type, product_uom_id, image_url, tracking, qty_available, is_storable}]
            scrap_reason_tag_id: false,
            barcode: "",

            // Reason tags
            reasonTags: [],     // [{id, name}]

            // Inventory mode: scanned quantities add up to the counted stock
            inventoryMode: false,
            canInventory: false,
            warehouseName: "",
            uid: false,

            // UI state
            loading: false,
            applying: false,    // inventory count is being saved: scanning is blocked
            scanning: true,
            showConfirmDialog: false,
            showResult: false,

            // Result state
            scrapResult: null,  // {scraps, stock_after, scrap_count, skipped} (scraps = scrap moves)
            inventoryResult: null,  // {lines, changed_count, skipped, warehouse_name}
        });

        onWillStart(async () => {
            await this.loadDefaultConfig();
            await this.loadReasonTags();
            await this._restoreInventory();
        });

        onMounted(() => {
            this._onKeyDown = this._handleKeyDown.bind(this);
            document.addEventListener("keydown", this._onKeyDown);
            this._focusBarcodeInput();
        });

        onWillUnmount(() => {
            document.removeEventListener("keydown", this._onKeyDown);
            this._audioContext?.close().catch(() => {});
        });
    }

    // -------------------------------------------------------------------------
    // Lifecycle
    // -------------------------------------------------------------------------

    async loadDefaultConfig() {
        const config = await this.orm.call("scrap.dashboard", "get_default_config", []);
        this.state.scrap_reason_tag_id = config.default_scrap_reason_tag_id || false;
        this.state.canInventory = Boolean(config.can_inventory);
        this.state.warehouseName = config.warehouse_name || "";
        this.state.uid = config.uid || false;
    }

    async loadReasonTags() {
        const tags = await this.orm.call("scrap.dashboard", "get_scrap_reason_tags", []);
        this.state.reasonTags = tags;
    }

    // -------------------------------------------------------------------------
    // Inventory mode: switch and unfinished count kept in the browser
    // -------------------------------------------------------------------------

    get _storageKey() {
        return `mrp_barcode_scrap.inventory.${this.state.uid || "0"}`;
    }

    /** Keeps only an unfinished count (inventory mode with scanned lines); anything else starts fresh next time. */
    _saveInventory() {
        try {
            if (this.state.inventoryMode && this.state.lines.length) {
                browser.localStorage.setItem(
                    this._storageKey,
                    JSON.stringify({ lines: this.state.lines })
                );
            } else {
                browser.localStorage.removeItem(this._storageKey);
            }
        } catch {
            // Storage is only a convenience (private window, full quota…)
        }
    }

    /** Back to the inventory mode with the lines of an unfinished count (page reload, other menu). */
    async _restoreInventory() {
        if (!this.state.canInventory) {
            return;
        }
        let saved = null;
        try {
            saved = JSON.parse(browser.localStorage.getItem(this._storageKey) || "null");
        } catch {
            saved = null;
        }
        if (!saved || !Array.isArray(saved.lines)) {
            return;
        }
        this.state.inventoryMode = true;
        this.state.lines = saved.lines;
        if (saved.lines.length) {
            await this._refreshInventoryStock();
            this.notification.add(
                _t("Unfinished inventory count restored (%s products).", saved.lines.length),
                { type: "info" }
            );
        }
    }

    /** Current stock of the counted products (it may have changed since they were scanned). */
    async _refreshInventoryStock() {
        const ids = this.state.lines.map((l) => l.product_id);
        if (!ids.length) {
            return;
        }
        try {
            const stock = await this.orm.call("scrap.dashboard", "get_inventory_stock", [ids]);
            for (const line of this.state.lines) {
                if (line.product_id in stock) {
                    line.qty_available = stock[line.product_id];
                }
            }
        } catch {
            // The count itself does not depend on it
        }
    }

    onToggleInventory(ev) {
        const wanted = ev.target.checked;
        ev.target.checked = this.state.inventoryMode; // follows the state until confirmed
        if (wanted === this.state.inventoryMode) {
            return;
        }
        if (!this.hasLines) {
            this._setMode(wanted);
            return;
        }
        this.dialog.add(ConfirmationDialog, {
            title: wanted ? _t("Switch to inventory count") : _t("Switch to scrapping"),
            body: _t("The scanned products will be removed from the list. Continue?"),
            confirmLabel: _t("Switch"),
            confirm: () => this._setMode(wanted),
            cancel: () => this._focusBarcodeInput(),
        });
    }

    _setMode(inventoryMode) {
        this.state.inventoryMode = inventoryMode;
        this.state.lines = [];
        this.state.barcode = "";
        this._saveInventory();
        this._focusBarcodeInput();
    }

    // -------------------------------------------------------------------------
    // Barcode Handling
    // -------------------------------------------------------------------------

    _handleKeyDown(ev) {
        // If confirm dialog is showing, don't capture keys
        if (this.state.showConfirmDialog || this.state.showResult || this.state.applying) return;
        // An Odoo dialog (mode switch question, Update Stock) keeps the keyboard
        if (ev.target.closest?.(".modal, .o_dialog")) return;
        // Always keep focus on barcode input if scanning
        if (this.state.scanning && ev.target.tagName !== "INPUT" && ev.target.tagName !== "TEXTAREA" && ev.target.tagName !== "SELECT") {
            this._focusBarcodeInput();
        }
    }

    _focusBarcodeInput() {
        const input = this.barcodeInput();
        if (input) {
            input.focus();
        }
    }

    async onBarcodeKeydown(ev) {
        if (this.state.applying) {
            ev.preventDefault();
            return;
        }
        if (ev.key === "Enter" && this.state.barcode.trim() && !this._processingBarcode) {
            ev.preventDefault();
            const barcode = this.state.barcode.trim();
            this.state.barcode = "";
            this._processingBarcode = true;
            try {
                await this.processBarcode(barcode);
            } finally {
                this._processingBarcode = false;
            }
            this._focusBarcodeInput();
        }
    }

    onBarcodeInput(ev) {
        this.state.barcode = ev.target.value;
    }

    async processBarcode(barcode) {
        this.state.loading = true;
        try {
            const result = await this.orm.call("scrap.dashboard", "parse_barcode", [barcode]);
            if (result.success) {
                if (this._addOrUpdateLine(result)) {
                    this._playSound("success");
                } else {
                    this._playSound("error");
                }
            } else {
                this.notification.add(result.error || _t("Product not found"), {
                    type: "danger",
                    title: _t("Barcode Error"),
                });
                this._playSound("error");
            }
        } catch (error) {
            this.notification.add(_t("Error scanning barcode"), {
                type: "danger",
            });
            this._playSound("error");
        }
        this.state.loading = false;
    }

    /** Adds the scanned product; returns false when it cannot be added in this mode. */
    _addOrUpdateLine(productInfo) {
        if (this.state.inventoryMode) {
            return this._addInventoryLine(productInfo);
        }
        const existing = this.state.lines.find(
            (l) => l.product_id === productInfo.product_id
        );
        if (existing) {
            // Cap at available stock
            existing.quantity = Math.min(
                existing.quantity + productInfo.quantity,
                existing.qty_available
            );
        } else {
            this.state.lines.push({
                product_id: productInfo.product_id,
                product_name: productInfo.product_name,
                quantity: Math.min(productInfo.quantity, productInfo.qty_available),
                uom: productInfo.product_uom,
                product_uom_id: productInfo.product_uom_id,
                unit_price: productInfo.unit_price,
                barcode_type: productInfo.barcode_type,
                image_url: productInfo.image_url,
                tracking: productInfo.tracking,
                qty_available: productInfo.qty_available,
                is_storable: productInfo.is_storable,
            });
        }
        return true;
    }

    /** Inventory count: every scan adds to the counted quantity, no cap by the current stock. */
    _addInventoryLine(productInfo) {
        if (!productInfo.is_storable) {
            this.notification.add(
                _t("%s does not track stock, it cannot be counted.", productInfo.product_name),
                { type: "danger", title: _t("Barcode Error") }
            );
            return false;
        }
        if (["lot", "serial"].includes(productInfo.tracking)) { // untracked: false in Odoo 20, "none" in 19
            this.notification.add(
                _t("%s is tracked by lots or serial numbers. Count it in Inventory › Operations › Adjustments › Physical Inventory.", productInfo.product_name),
                { type: "danger", title: _t("Barcode Error") }
            );
            return false;
        }
        const existing = this.state.lines.find((l) => l.product_id === productInfo.product_id);
        if (existing) {
            existing.quantity = this._roundQty(existing.quantity + productInfo.quantity);
            existing.qty_available = productInfo.qty_warehouse;
        } else {
            this.state.lines.push({
                product_id: productInfo.product_id,
                product_name: productInfo.product_name,
                quantity: this._roundQty(productInfo.quantity),
                uom: productInfo.product_uom,
                product_uom_id: productInfo.product_uom_id,
                unit_price: productInfo.unit_price,
                barcode_type: productInfo.barcode_type,
                image_url: productInfo.image_url,
                tracking: productInfo.tracking,
                qty_available: productInfo.qty_warehouse,
                is_storable: productInfo.is_storable,
            });
        }
        this._saveInventory();
        return true;
    }

    _roundQty(value) {
        return Math.round((value || 0) * QTY_DIGITS) / QTY_DIGITS;
    }

    /**
     * Short beep generated in the browser: high and short on success, low and longer on error.
     * (The sound files the module used to point to never existed, every scan ended in a 404.)
     */
    _playSound(type) {
        try {
            const AudioContext = browser.AudioContext || browser.webkitAudioContext;
            const activation = browser.navigator.userActivation;
            // Browsers allow sound only after a user gesture (a scan from the scanner is a key press)
            if (!AudioContext || (activation && !activation.hasBeenActive)) {
                return;
            }
            this._audioContext ||= new AudioContext();
            const ctx = this._audioContext;
            const oscillator = ctx.createOscillator();
            const gain = ctx.createGain();
            oscillator.frequency.value = type === "success" ? 1200 : 220;
            gain.gain.value = 0.1;
            oscillator.connect(gain).connect(ctx.destination);
            oscillator.start();
            oscillator.stop(ctx.currentTime + (type === "success" ? 0.08 : 0.3));
        } catch {
            // Sound not essential
        }
    }

    // -------------------------------------------------------------------------
    // Line Management
    // -------------------------------------------------------------------------

    updateQuantity(lineIndex, ev) {
        const val = parseFloat(ev.target.value);
        const line = this.state.lines[lineIndex];
        if (this.state.inventoryMode) {
            // Counted quantity: 0 is a valid count (nothing found)
            if (!isNaN(val) && val >= 0) {
                line.quantity = this._roundQty(val);
            }
            ev.target.value = line.quantity;
            this._saveInventory();
            return;
        }
        if (!isNaN(val) && val > 0) {
            // Cap at available stock
            line.quantity = Math.min(val, line.qty_available);
        }
    }

    incrementQuantity(lineIndex) {
        const line = this.state.lines[lineIndex];
        if (this.state.inventoryMode) {
            line.quantity = this._roundQty(line.quantity + 1);
            this._saveInventory();
        } else {
            line.quantity = Math.min(line.quantity + 1, line.qty_available);
        }
        this._focusBarcodeInput();
    }

    decrementQuantity(lineIndex) {
        const line = this.state.lines[lineIndex];
        if (this.state.inventoryMode) {
            // Never below 0 and the line stays: 0 counted means the stock goes to zero
            line.quantity = this._roundQty(Math.max(0, line.quantity - 1));
            this._saveInventory();
        } else if (line.quantity > 1) {
            line.quantity -= 1;
        } else {
            this.removeLine(lineIndex);
        }
        this._focusBarcodeInput();
    }

    scrapAllStock(lineIndex) {
        const line = this.state.lines[lineIndex];
        line.quantity = line.qty_available;
        this._focusBarcodeInput();
    }

    removeLine(lineIndex) {
        this.state.lines.splice(lineIndex, 1);
        this._saveInventory();
        this._focusBarcodeInput();
    }

    onReasonTagChange(ev) {
        const val = parseInt(ev.target.value);
        this.state.scrap_reason_tag_id = val || false;
    }

    get selectedReasonTagName() {
        if (!this.state.scrap_reason_tag_id) return false;
        const tag = this.state.reasonTags.find(t => t.id === this.state.scrap_reason_tag_id);
        return tag ? tag.name : false;
    }

    // -------------------------------------------------------------------------
    // Computed
    // -------------------------------------------------------------------------

    get totalValue() {
        return this.state.lines.reduce(
            (sum, line) => sum + line.quantity * line.unit_price,
            0
        );
    }

    get totalItems() {
        return this.state.lines.length;
    }

    get hasLines() {
        return this.state.lines.length > 0;
    }

    lineDifference(line) {
        return this._roundQty(line.quantity - (line.qty_available || 0));
    }

    /** Inventory count: value of the difference between counted and current stock (sale price). */
    get inventoryDifferenceValue() {
        return this.state.lines.reduce(
            (sum, line) => sum + this.lineDifference(line) * line.unit_price,
            0
        );
    }

    formatDifference(value) {
        const rounded = this._roundQty(value);
        return (rounded > 0 ? "+" : "") + this.formatQuantity(rounded);
    }

    differenceClass(value) {
        const rounded = this._roundQty(value);
        return rounded > 0 ? "text-success" : rounded < 0 ? "text-danger" : "text-muted";
    }

    // -------------------------------------------------------------------------
    // Scrap Actions
    // -------------------------------------------------------------------------

    onScrapClick() {
        if (!this.hasLines) {
            this.notification.add(
                this.state.inventoryMode ? _t("No products to count") : _t("No products to scrap"),
                { type: "warning" }
            );
            return;
        }
        this.state.showConfirmDialog = true;
    }

    cancelConfirm() {
        this.state.showConfirmDialog = false;
        this._focusBarcodeInput();
    }

    async confirmScrap() {
        if (this.state.inventoryMode) {
            return this.confirmInventory();
        }
        this.state.showConfirmDialog = false;
        this.state.loading = true;

        try {
            const scrapLines = this.state.lines.map((line) => ({
                product_id: line.product_id,
                quantity: line.quantity,
                product_uom_id: line.product_uom_id,
            }));

            const result = await this.orm.call(
                "scrap.dashboard",
                "create_scrap_moves",
                [scrapLines, this.state.scrap_reason_tag_id ? [this.state.scrap_reason_tag_id] : []]
            );

            if (result.success) {
                this.state.scrapResult = result;
                this.state.showResult = true;
                this.state.scanning = false;
                this.notification.add(
                    _t("%s product(s) scrapped", result.scrap_count),
                    { type: "success" }
                );
            }
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || _t("Error while scrapping"),
                { type: "danger" }
            );
        }

        this.state.loading = false;
    }

    async confirmInventory() {
        if (this.state.applying) {
            return;
        }
        this.state.showConfirmDialog = false;
        this.state.applying = true;
        try {
            const lines = this.state.lines.map((line) => ({
                product_id: line.product_id,
                quantity: line.quantity,
            }));
            const result = await this.orm.call("scrap.dashboard", "apply_inventory", [lines]);
            if (result.success) {
                this.state.inventoryResult = result;
                this.state.showResult = true;
                this.state.scanning = false;
                this.state.lines = [];
                this._saveInventory();
                this.notification.add(
                    _t("Inventory count applied, stock changed for %s product(s)", result.changed_count),
                    { type: "success" }
                );
            }
        } catch (error) {
            // Lines stay (and stay saved in the browser) so the count is not lost
            this.notification.add(
                error.data?.message || error.message || _t("Error applying the inventory count"),
                { type: "danger" }
            );
        } finally {
            this.state.applying = false;
            this._focusBarcodeInput();
        }
    }

    // -------------------------------------------------------------------------
    // Post-scrap Actions
    // -------------------------------------------------------------------------

    /** Scrap move(s) in the Inventory › Scrap views (Odoo 20: a scrap is a stock.move with is_scrap). */
    async openScraps(moveIds) {
        const action = await this.orm.call("scrap.dashboard", "action_open_scraps", [moveIds]);
        this.action.doAction(action);
    }

    openProductForm(productId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.product",
            res_id: productId,
            views: [[false, "form"]],
            target: "new",
        });
    }

    /** Update Stock dialog of the product (the module's own wizard, same as the button on the product form). */
    async openInventoryAdjustment(productId) {
        const action = await this.orm.call(
            "product.product",
            "action_quick_stock_update",
            [[productId]]
        );
        this.action.doAction(action, {
            onClose: () => this._refreshResultStock(productId),
        });
    }

    async _refreshResultStock(productId) {
        try {
            const stock = await this.orm.call("scrap.dashboard", "get_product_stock", [[productId]]);
            const row = stock[productId];
            if (!row) {
                return;
            }
            if (this.state.scrapResult?.stock_after?.[productId]) {
                Object.assign(this.state.scrapResult.stock_after[productId], row);
            }
            const invLine = this.state.inventoryResult?.lines?.find((l) => l.product_id === productId);
            if (invLine) {
                invLine.qty_after = row.qty_available;
                invLine.difference = row.qty_available - invLine.qty_before;
            }
        } catch {
            // Only a refresh of the displayed numbers
        }
    }

    startNewSession() {
        this.state.lines = [];
        this.state.barcode = "";
        this.state.showResult = false;
        this.state.scrapResult = null;
        this.state.inventoryResult = null;
        this.state.scanning = true;
        this._saveInventory();
        this.loadDefaultConfig();
        this.loadReasonTags();
        setTimeout(() => this._focusBarcodeInput(), 100);
    }

    // -------------------------------------------------------------------------
    // Formatting
    // -------------------------------------------------------------------------

    formatCurrency(value) {
        return new Intl.NumberFormat(undefined, {
            style: "currency",
            currency: "CZK",
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(value || 0);
    }

    formatQuantity(value) {
        return parseFloat(value || 0).toFixed(3);
    }
}

registry.category("actions").add("mrp_barcode_scrap_dashboard", ScrapBarcodeDashboard);
