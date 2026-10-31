import math
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

PART_SOURCES = [
    ("finished", "Finished ride-on"),
    ("moulded", "Moulded in-house"),
    ("bought", "Bought-out part"),
    ("granule", "Granule / raw material"),
]


class ProductTemplate(models.Model):
    _inherit = "product.template"

    lr_part_source = fields.Selection(PART_SOURCES, string="Part Source", index=True)
    lr_part_weight = fields.Float(
        "Moulded Part Weight (kg)", digits=(12, 3),
        help="Finished weight of one moulded piece. Granule consumption is driven from this.")


class ProductProduct(models.Model):
    _inherit = "product.product"

    # Sales view: what can be promised from each stock
    lr_atp_offline = fields.Float("Free Offline", compute="_compute_lr_atp", digits="Product Unit")
    lr_atp_online = fields.Float("Free Online", compute="_compute_lr_atp", digits="Product Unit")
    lr_incoming_total = fields.Float("Arriving", compute="_compute_lr_atp", digits="Product Unit")
    lr_next_incoming_date = fields.Date("Next Arrival", compute="_compute_lr_atp")

    # Production view: how many units can be built
    lr_build_now = fields.Integer(
        "Build Now", compute="_compute_lr_build",
        help="Units that can be assembled from parts already in stock, after open production needs.")
    lr_build_bought = fields.Integer(
        "Build Limit", compute="_compute_lr_build",
        help="Units the bought-out parts in stock allow, assuming moulded parts are made daily from granules.")
    lr_build_incoming = fields.Integer(
        "Build with Arrivals", compute="_compute_lr_build",
        help="Same as the bought-out limit, adding parts on confirmed purchase orders.")
    lr_bottleneck_id = fields.Many2one(
        "product.product", string="Limiting Part", compute="_compute_lr_build")

    # Purchase view: component cover against lead time
    lr_daily_usage = fields.Float("Daily Use", compute="_compute_lr_cover", digits=(12, 1))
    lr_days_cover = fields.Float("Days Cover", compute="_compute_lr_cover", digits=(12, 1))
    lr_lead_days = fields.Integer("Lead Days", compute="_compute_lr_cover")
    lr_order_now = fields.Boolean("Order Now", compute="_compute_lr_cover", search="_search_lr_order_now")
    lr_suggested_qty = fields.Float("Suggest Order", compute="_compute_lr_cover", digits="Product Unit")

    # ------------------------------------------------------------------
    # Available to promise
    # ------------------------------------------------------------------
    def _lr_incoming_by_product(self, warehouse=None):
        """Earliest date and total quantity of open receipts and production into stock."""
        domain = [
            ("product_id", "in", self.ids),
            ("state", "not in", ("draft", "done", "cancel")),
            ("location_dest_id.usage", "=", "internal"),
            ("location_id.usage", "in", ("supplier", "production")),
        ]
        if warehouse:
            domain.append(("location_dest_id", "child_of", warehouse.view_location_id.id))
        res = {}
        for product, date_min, qty in self.env["stock.move"].sudo()._read_group(
                domain, ["product_id"], ["date:min", "product_qty:sum"]):
            res[product.id] = (date_min and date_min.date(), qty)
        return res

    def _compute_lr_atp(self):
        company = self.env.company
        for product in self:
            product.lr_atp_offline = product.lr_atp_online = product.lr_incoming_total = 0.0
            product.lr_next_incoming_date = False
        storable = self.filtered("is_storable")
        if not storable:
            return
        for wh, fname in ((company.lr_offline_warehouse_id, "lr_atp_offline"),
                          (company.lr_online_warehouse_id, "lr_atp_online")):
            if not wh:
                continue
            qty = {p.id: p.qty_available - p.outgoing_qty for p in storable.with_context(warehouse_id=wh.id)}
            for product in storable:
                product[fname] = qty[product.id]
        incoming = storable._lr_incoming_by_product()
        for product in storable:
            date, qty = incoming.get(product.id, (False, 0.0))
            product.lr_incoming_total = qty
            product.lr_next_incoming_date = date

    # ------------------------------------------------------------------
    # Build capacity
    # ------------------------------------------------------------------
    def _lr_requirements_per_unit(self):
        """{finished product: {component: qty per finished unit}} using the variant-aware BoM explosion."""
        boms = self.env["mrp.bom"]._bom_find(self, bom_type="normal")
        result = {}
        for product in self:
            bom = boms.get(product)
            if not bom:
                continue
            _boms, lines = bom.explode(product, bom.product_qty)
            req = defaultdict(float)
            for line, line_data in lines:
                qty = line.product_uom_id._compute_quantity(line_data["qty"], line.product_id.uom_id, round=False)
                req[line.product_id] += qty / (bom.product_qty or 1.0)
            result[product] = req
        return result

    def _compute_lr_build(self):
        for product in self:
            product.lr_build_now = product.lr_build_bought = product.lr_build_incoming = 0
            product.lr_bottleneck_id = False
        finished = self.filtered(lambda p: p.lr_part_source in ("finished", "moulded"))
        if not finished:
            return
        requirements = finished._lr_requirements_per_unit()
        components = self.env["product.product"].browse(
            {c.id for req in requirements.values() for c in req})
        free, incoming = {}, {}
        for comp in components:
            free[comp.id] = max(comp.qty_available - comp.outgoing_qty, 0.0)
            incoming[comp.id] = max(comp.incoming_qty, 0.0)
        for product, req in requirements.items():
            if not req:
                continue
            now, bought, with_in = [], [], []
            for comp, per_unit in req.items():
                if per_unit <= 0:
                    continue
                can = math.floor(free[comp.id] / per_unit + 1e-9)
                now.append(can)
                if comp.lr_part_source in ("moulded", "granule"):
                    continue
                bought.append((can, comp))
                with_in.append(math.floor((free[comp.id] + incoming[comp.id]) / per_unit + 1e-9))
            product.lr_build_now = min(now) if now else 0
            if bought:
                limit, limiting = min(bought, key=lambda x: x[0])
                product.lr_build_bought = limit
                product.lr_bottleneck_id = limiting
                product.lr_build_incoming = min(with_in)
            else:
                product.lr_build_bought = product.lr_build_incoming = product.lr_build_now

    # ------------------------------------------------------------------
    # Component cover
    # ------------------------------------------------------------------
    def _compute_lr_cover(self):
        safety = self.env.company.lr_cover_safety_days
        since = fields.Datetime.now() - timedelta(days=30)
        usage = {}
        if self.ids:
            for product, qty in self.env["stock.move"].sudo()._read_group(
                    [("product_id", "in", self.ids), ("state", "=", "done"), ("date", ">=", since),
                     ("location_dest_id.usage", "=", "production"), ("location_id.usage", "=", "internal")],
                    ["product_id"], ["product_qty:sum"]):
                usage[product.id] = qty / 30.0
        for product in self:
            daily = usage.get(product.id, 0.0)
            lead = product.seller_ids[:1].delay or 0
            free = max(product.qty_available - product.outgoing_qty, 0.0) if product.is_storable else 0.0
            product.lr_daily_usage = daily
            product.lr_lead_days = lead
            product.lr_days_cover = (free / daily) if daily else 0.0
            # Flag only when stock plus what is already on order will not last the lead time
            product.lr_order_now = bool(daily) and (free + product.incoming_qty) / daily <= (lead + safety)
            target = daily * (lead + safety + 30)
            product.lr_suggested_qty = math.ceil(max(target - free - product.incoming_qty, 0.0)) if daily else 0.0

    def _search_lr_order_now(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            return [("id", "=", 0)]
        candidates = self.search([("lr_part_source", "=", "bought")])
        flagged = candidates.filtered("lr_order_now").ids
        positive = (operator in ("=", "in") and value) or (operator in ("!=", "not in") and not value)
        return [("id", "in" if positive else "not in", flagged)]

    @api.model
    def _cron_lr_component_alert(self):
        for company in self.env["res.company"].search([("lr_purchase_head_id", "!=", False)]):
            env = self.with_company(company).with_context(allowed_company_ids=[company.id])
            parts = env.search([("lr_part_source", "=", "bought")]).filtered("lr_order_now")
            todo = self.env.ref("mail.mail_activity_data_todo")
            for part in parts:
                tmpl = part.product_tmpl_id
                if tmpl.activity_ids.filtered(lambda a: a.summary and a.summary.startswith("Order now")):
                    continue
                tmpl.activity_schedule(
                    activity_type_id=todo.id,
                    user_id=company.lr_purchase_head_id.id,
                    summary=_("Order now: %s", part.display_name),
                    note=_("Stock covers %(cover).0f days against a %(lead)s day lead time. "
                           "Suggested order: %(qty).0f.",
                           cover=part.lr_days_cover, lead=part.lr_lead_days, qty=part.lr_suggested_qty),
                )
