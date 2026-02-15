/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class ScrapBarcodeDashboard extends Component {
    static template = "mrp_barcode_scrap.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.barcodeInput = useRef("barcodeInput");

        this.state = useState({
            // Scanning state
            lines: [],          // [{product_id, product_name, quantity, uom, unit_price, barcode_type, product_uom_id, image_url, tracking, qty_available}]
            scrap_reason_tag_id: false,
            barcode: "",

            // Reason tags
            reasonTags: [],     // [{id, name}]

            // UI state
            loading: false,
            scanning: true,
            showConfirmDialog: false,
            showResult: false,

            // Result state
            scrapResult: null,  // {scraps, stock_after, scrap_count, skipped}
        });

        onWillStart(async () => {
            await this.loadDefaultConfig();
            await this.loadReasonTags();
        });

        onMounted(() => {
            this._onKeyDown = this._handleKeyDown.bind(this);
            document.addEventListener("keydown", this._onKeyDown);
            this._focusBarcodeInput();
        });

        onWillUnmount(() => {
            document.removeEventListener("keydown", this._onKeyDown);
        });
    }

    // -------------------------------------------------------------------------
    // Lifecycle
    // -------------------------------------------------------------------------

    async loadDefaultConfig() {
        const config = await this.orm.call("scrap.dashboard", "get_default_config", []);
        this.state.scrap_reason_tag_id = config.default_scrap_reason_tag_id || false;
    }

    async loadReasonTags() {
        const tags = await this.orm.call("scrap.dashboard", "get_scrap_reason_tags", []);
        this.state.reasonTags = tags;
    }

    // -------------------------------------------------------------------------
    // Barcode Handling
    // -------------------------------------------------------------------------

    _handleKeyDown(ev) {
        // If confirm dialog is showing, don't capture keys
        if (this.state.showConfirmDialog || this.state.showResult) return;
        // Always keep focus on barcode input if scanning
        if (this.state.scanning && ev.target.tagName !== "INPUT" && ev.target.tagName !== "TEXTAREA") {
            this._focusBarcodeInput();
        }
    }

    _focusBarcodeInput() {
        if (this.barcodeInput.el) {
            this.barcodeInput.el.focus();
        }
    }

    async onBarcodeKeydown(ev) {
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
                this._addOrUpdateLine(result);
                this._playSound("success");
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

    _addOrUpdateLine(productInfo) {
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
            });
        }
    }

    _playSound(type) {
        try {
            const audio = new Audio(
                type === "success"
                    ? "/mrp_barcode_scrap/static/src/sounds/beep.mp3"
                    : "/mrp_barcode_scrap/static/src/sounds/error.mp3"
            );
            audio.volume = 0.3;
            audio.play().catch(() => {});
        } catch (e) {
            // Sound not essential
        }
    }

    // -------------------------------------------------------------------------
    // Line Management
    // -------------------------------------------------------------------------

    updateQuantity(lineIndex, ev) {
        const val = parseFloat(ev.target.value);
        const line = this.state.lines[lineIndex];
        if (!isNaN(val) && val > 0) {
            // Cap at available stock
            line.quantity = Math.min(val, line.qty_available);
        }
    }

    incrementQuantity(lineIndex) {
        const line = this.state.lines[lineIndex];
        line.quantity = Math.min(line.quantity + 1, line.qty_available);
        this._focusBarcodeInput();
    }

    decrementQuantity(lineIndex) {
        if (this.state.lines[lineIndex].quantity > 1) {
            this.state.lines[lineIndex].quantity -= 1;
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

    // -------------------------------------------------------------------------
    // Scrap Actions
    // -------------------------------------------------------------------------

    onScrapClick() {
        if (!this.hasLines) {
            this.notification.add(_t("No products to scrap"), { type: "warning" });
            return;
        }
        this.state.showConfirmDialog = true;
    }

    cancelConfirm() {
        this.state.showConfirmDialog = false;
        this._focusBarcodeInput();
    }

    async confirmScrap() {
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
                "create_scrap_orders",
                [scrapLines, this.state.scrap_reason_tag_id ? [this.state.scrap_reason_tag_id] : []]
            );

            if (result.success) {
                this.state.scrapResult = result;
                this.state.showResult = true;
                this.state.scanning = false;
                this.notification.add(
                    _t("%s scrap order(s) created and confirmed", result.scrap_count),
                    { type: "success" }
                );
            }
        } catch (error) {
            this.notification.add(
                error.message || _t("Error creating scrap orders"),
                { type: "danger" }
            );
        }

        this.state.loading = false;
    }

    // -------------------------------------------------------------------------
    // Post-scrap Actions
    // -------------------------------------------------------------------------

    openScrapOrder(scrapId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.scrap",
            res_id: scrapId,
            views: [[false, "form"]],
            target: "new",
        });
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

    async openInventoryAdjustment(productId) {
        const action = await this.orm.call(
            "product.product",
            "action_update_quantity_on_hand",
            [[productId]]
        );
        action.target = "new";
        this.action.doAction(action);
    }

    startNewSession() {
        this.state.lines = [];
        this.state.barcode = "";
        this.state.showResult = false;
        this.state.scrapResult = null;
        this.state.scanning = true;
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
