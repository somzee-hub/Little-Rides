import logging
from collections import defaultdict

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG

from ..demo_data import DemoLoader

_logger = logging.getLogger(__name__)
PARAM = "littlerides_erp.demo_loaded_on"
# Masters go last, in this order, after every document that points at them
UNLOAD_LAST = [
    "crm.lead", "fleet.vehicle.log.contract", "fleet.vehicle", "fleet.vehicle.model",
    "stock.warehouse.orderpoint", "mrp.bom", "product.supplierinfo", "product.pricelist.item",
    "product.product", "product.template", "product.attribute", "product.category",
    "res.partner", "fleet.vehicle.model.brand",
]


def _clean_context(context):
    """Drop UI keys (active_id, default_*) that other models would read as their own defaults."""
    return {k: v for k, v in context.items()
            if not k.startswith(("active_", "default_", "search_default_")) and k not in ("params",)}


class LrDemoRecord(models.Model):
    _name = "lr.demo.record"
    _description = "Little Rides demo record"
    _order = "id"

    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)


class LrDemoManager(models.TransientModel):
    _name = "lr.demo.manager"
    _description = "Little Rides demo data"

    name = fields.Char(default="Demo Data")

    state = fields.Selection([("empty", "Not loaded"), ("loaded", "Loaded")], compute="_compute_status")
    loaded_on = fields.Char(compute="_compute_status")
    record_count = fields.Integer(compute="_compute_status")
    summary = fields.Html(compute="_compute_status")

    def _compute_status(self):
        Rec = self.env["lr.demo.record"].sudo()
        counts = dict((m, c) for m, c in Rec._read_group([], ["res_model"], ["__count"]))
        labels = [
            ("product.template", _("Products and parts")), ("mrp.bom", _("Bills of materials")),
            ("res.partner", _("Customers and vendors")), ("purchase.order", _("Purchase orders")),
            ("mrp.production", _("Manufacturing orders")), ("stock.picking", _("Internal transfers")),
            ("sale.order", _("Sales orders and quotations")), ("crm.lead", _("Leads")),
            ("fleet.vehicle", _("Trucks")),
        ]
        rows = "".join("<tr><td>%s</td><td style='text-align:right'>%s</td></tr>" % (label, counts.get(m, 0))
                       for m, label in labels if counts.get(m))
        for wiz in self:
            wiz.record_count = sum(counts.values())
            wiz.state = "loaded" if counts else "empty"
            wiz.loaded_on = self.env["ir.config_parameter"].sudo().get_param(PARAM) or ""
            wiz.summary = ("<table class='table table-sm'>%s</table>" % rows) if rows else False

    @api.model
    def action_open(self):
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "view_mode": "form",
            "res_id": self.create({}).id, "target": "current", "name": _("Demo Data"),
        }

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def action_load(self):
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only an administrator can load demo data."))
        if self.env["lr.demo.record"].sudo().search_count([], limit=1):
            raise UserError(_("Demo data is already loaded. Remove it first to load a fresh set."))
        env = self.env(su=True, context=dict(
            _clean_context(self.env.context), tracking_disable=True, mail_create_nolog=True, mail_notrack=True,
            mail_auto_subscribe_no_notify=True))
        loader = DemoLoader(env)
        loader.run()
        self.with_env(env)._lr_load_demo_extra(loader)
        loader.flush_tracking()
        env["ir.config_parameter"].set_param(PARAM, fields.Datetime.to_string(fields.Datetime.now()))
        _logger.info("Little Rides demo data loaded: %s records", len(loader.tracked))
        return self._reopen()

    def _lr_load_demo_extra(self, loader):
        """Hook for the Enterprise layer to add its own demo records through loader.track()."""
        return True

    # ------------------------------------------------------------------
    # Unload
    # ------------------------------------------------------------------
    def action_unload(self):
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only an administrator can remove demo data."))
        self._lr_unload_demo()
        return self._reopen()

    def _lr_unload_demo(self):
        ctx = dict(_clean_context(self.env.context), tracking_disable=True, force_delete=True, active_test=False)
        ctx[MODULE_UNINSTALL_FLAG] = True
        env = self.env(su=True, context=ctx)
        Rec = env["lr.demo.record"]
        recs = Rec.search([])
        if not recs:
            raise UserError(_("No demo data is loaded."))
        tracked = defaultdict(list)
        order = []
        for rec in recs:
            if rec.res_model not in tracked:
                order.append(rec.res_model)
            tracked[rec.res_model].append(rec.res_id)

        def browse(model):
            if model not in env:
                return None
            return env[model].browse(tracked.get(model, [])).exists()

        templates = browse("product.template")
        products = browse("product.product") | env["product.product"].search(
            [("product_tmpl_id", "in", templates.ids)])
        partners = browse("res.partner")
        partners |= env["res.partner"].search([("id", "child_of", partners.ids)]) if partners else partners
        pids, rids = products.ids or [0], partners.ids or [0]

        # 1. Accounting: payments, then invoices, bills and vouchers
        payments = env["account.payment"].search(["|", ("partner_id", "in", rids),
                                                  ("id", "in", tracked.get("account.payment", []) or [0])])
        moves = env["account.move"].search([
            "|", "|", "|", ("partner_id", "in", rids), ("line_ids.product_id", "in", pids),
            ("id", "in", tracked.get("account.move", []) or [0]), ("origin_payment_id", "in", payments.ids or [0])])
        # Matching between demo entries goes first, so no exchange-difference reversals get created
        lines = env["account.move.line"].search([("move_id", "in", (moves | payments.move_id).ids or [0])])
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        moves |= partials.exchange_move_id
        moves |= env["account.move"].search([("tax_cash_basis_rec_id", "in", partials.ids or [0])])
        fulls = partials.full_reconcile_id | lines.full_reconcile_id
        env.flush_all()
        self._lr_purge("account_partial_reconcile", partials.ids)
        self._lr_purge("account_full_reconcile", fulls.ids)
        env.invalidate_all()
        if "account.payment.register" in env:
            env["account.payment.register"].search([("partner_id", "in", rids)]).unlink()
        payments.exists().unlink()
        moves.exists().unlink()

        # 2. Anything the Enterprise layer hangs on stock or products (quality checks etc.)
        self.with_env(env)._lr_unload_demo_extra(products, partners)

        # 3. Stock and manufacturing layer, removed at table level so done moves go too
        sales = env["sale.order"].search(["|", ("partner_id", "in", rids),
                                          ("id", "in", tracked.get("sale.order", []) or [0])])
        purchases = env["purchase.order"].search(["|", ("partner_id", "in", rids),
                                                  ("id", "in", tracked.get("purchase.order", []) or [0])])
        productions = env["mrp.production"].search([("product_id", "in", pids)])
        pickings = env["stock.picking"].search([
            "|", "|", "|", ("partner_id", "in", rids), ("move_ids.product_id", "in", pids),
            ("sale_id", "in", sales.ids or [0]), ("id", "in", tracked.get("stock.picking", []) or [0])])
        stock_moves = env["stock.move"].search([
            "|", "|", "|", ("product_id", "in", pids), ("picking_id", "in", pickings.ids or [0]),
            ("production_id", "in", productions.ids or [0]),
            ("raw_material_production_id", "in", productions.ids or [0])])
        cr = env.cr
        env.flush_all()
        self._lr_purge("stock_move_line", env["stock.move.line"].search(
            ["|", ("move_id", "in", stock_moves.ids or [0]), ("product_id", "in", pids)]).ids)
        self._lr_purge("stock_scrap", env["stock.scrap"].search([("product_id", "in", pids)]).ids)
        self._lr_purge("stock_move", stock_moves.ids)
        self._lr_purge("stock_quant", env["stock.quant"].search([("product_id", "in", pids)]).ids)
        self._lr_purge("mrp_workorder", env["mrp.workorder"].search([("production_id", "in", productions.ids or [0])]).ids)
        self._lr_purge("mrp_production", productions.ids)
        self._lr_purge("stock_picking", pickings.ids)
        self._lr_purge("stock_lot", env["stock.lot"].search([("product_id", "in", pids)]).ids)
        self._lr_purge("stock_warehouse_orderpoint", env["stock.warehouse.orderpoint"].search(
            [("product_id", "in", pids)]).ids)
        for model, ids in (("stock.picking", pickings.ids), ("mrp.production", productions.ids),
                           ("stock.scrap", [])):
            if ids:
                cr.execute("DELETE FROM mail_message WHERE model=%s AND res_id IN %s", (model, tuple(ids)))
                cr.execute("DELETE FROM mail_followers WHERE res_model=%s AND res_id IN %s", (model, tuple(ids)))
                cr.execute("DELETE FROM mail_activity WHERE res_model=%s AND res_id IN %s", (model, tuple(ids)))
        env.invalidate_all()

        # 4. Orders, then every tracked record newest first
        sales.exists().unlink()
        purchases.exists().unlink()
        env["crm.lead"].search([("partner_id", "in", rids)]).unlink()
        # Driver history and odometer logs that fleet creates on its own
        for log_model in ("fleet.vehicle.assignation.log", "fleet.vehicle.odometer", "fleet.vehicle.log.services"):
            if log_model in env and tracked.get("fleet.vehicle"):
                env[log_model].search([("vehicle_id", "in", tracked["fleet.vehicle"])]).unlink()
        last = [m for m in UNLOAD_LAST if m in tracked]
        for model in [m for m in reversed(order) if m not in UNLOAD_LAST] + last:
            if model not in env:
                continue
            records = env[model].browse(tracked[model]).exists()
            if not records:
                continue
            try:
                with env.cr.savepoint():
                    records.unlink()
            except (psycopg2.IntegrityError, UserError):
                # Something outside the demo set still points at these rows (logs, auto-created
                # history). Clear those links and delete at table level.
                ids = records.ids
                env.flush_all()
                self._lr_purge(records._table, ids)
                for table, col in (("mail_message", "model"), ("mail_followers", "res_model"),
                                   ("mail_activity", "res_model")):
                    env.cr.execute('DELETE FROM "%s" WHERE "%s" = %%s AND res_id IN %%s' % (table, col),
                                   (model, tuple(ids)))
                env.invalidate_all()
        recs.exists().unlink()
        # Drop queued partner-rank updates for partners that no longer exist
        for field in ("customer_rank", "supplier_rank"):
            data = env.cr.postcommit.data.get("account.res.partner.increase_rank.%s" % field)
            if data:
                for pid in list(data):
                    if pid in partners.ids:
                        data.pop(pid)
        env["ir.config_parameter"].set_param(PARAM, False)
        _logger.info("Little Rides demo data removed")
        return True

    def _lr_unload_demo_extra(self, products, partners):
        """Hook for the Enterprise layer: remove records that point at demo products or partners."""
        return True

    @api.model
    def _lr_purge(self, table, ids):
        """Delete rows of a demo-only set at table level. Foreign keys that would block the delete
        are cleared first (nullable) or their rows purged in turn (required)."""
        ids = list(set(ids))
        if not ids:
            return
        cr = self.env.cr
        cr.execute("""
            SELECT cl.relname, att.attname, con.confdeltype, att.attnotnull
              FROM pg_constraint con
              JOIN pg_class cl ON cl.oid = con.conrelid
              JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
             WHERE con.contype = 'f' AND con.confrelid = %s::regclass
        """, (table,))
        for ref_table, column, deltype, notnull in cr.fetchall():
            if deltype in ("c", "n", "d") or ref_table == table:
                continue
            if notnull:
                cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name='id'",
                           (ref_table,))
                if cr.fetchone():
                    cr.execute('SELECT id FROM "%s" WHERE "%s" IN %%s' % (ref_table, column), (tuple(ids),))
                    self._lr_purge(ref_table, [r[0] for r in cr.fetchall()])
                else:
                    cr.execute('DELETE FROM "%s" WHERE "%s" IN %%s' % (ref_table, column), (tuple(ids),))
            else:
                cr.execute('UPDATE "%s" SET "%s" = NULL WHERE "%s" IN %%s' % (ref_table, column, column),
                           (tuple(ids),))
        cr.execute('DELETE FROM "%s" WHERE id IN %%s' % table, (tuple(ids),))
