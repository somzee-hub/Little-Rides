from collections import defaultdict

from odoo import _, api, fields, models

CHANNELS = [
    ("offline_bulk", "Offline: Dealer / Bulk"),
    ("offline_single", "Offline: Single Piece"),
    ("amazon", "Online: Amazon"),
    ("flipkart", "Online: Flipkart"),
    ("website", "Online: Website"),
]
TRANSPORT = [
    ("own_truck", "Own Truck"),
    ("customer", "Customer Arranged"),
    ("courier", "Courier / Marketplace Pickup"),
]


class SaleOrder(models.Model):
    _inherit = "sale.order"

    lr_channel = fields.Selection(
        CHANNELS, string="Sales Channel", default="offline_bulk", tracking=True,
        help="Online channels ship from online stock, offline channels from offline stock.")
    lr_transport_mode = fields.Selection(TRANSPORT, string="Transport", default="customer")
    lr_stock_status = fields.Selection(
        [("green", "All in stock"), ("amber", "Waiting on arrivals"), ("red", "Short")],
        string="Stock", compute="_compute_lr_stock_status")

    @api.depends("user_id", "company_id", "lr_channel")
    def _compute_warehouse_id(self):
        super()._compute_warehouse_id()
        for order in self:
            if order.state in ("draft", "sent") or not order.ids:
                warehouse = order.company_id._lr_channel_warehouse(order.lr_channel)
                if warehouse:
                    order.warehouse_id = warehouse

    @api.depends("order_line.lr_stock_signal")
    def _compute_lr_stock_status(self):
        for order in self:
            signals = set(order.order_line.mapped("lr_stock_signal")) - {False}
            order.lr_stock_status = ("red" if "red" in signals else "amber" if "amber" in signals
                                     else "green" if signals else False)

    def action_confirm(self):
        short = {order: ["%s: %s" % (l.product_id.display_name, l.lr_stock_note)
                         for l in order.order_line if l.lr_stock_signal == "red"] for order in self}
        res = super().action_confirm()
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for order, lines in short.items():
            head = order.company_id.lr_production_head_id
            if not lines or not head or not todo:
                continue
            body = "<br/>".join(lines)
            order.sudo().activity_schedule(
                activity_type_id=todo.id, user_id=head.id,
                summary=_("Plan production for %s", order.name), note=body)
        return res


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    lr_stock_signal = fields.Selection(
        [("green", "Available"), ("amber", "Arriving"), ("red", "Short")],
        string="Stock", compute="_compute_lr_stock_signal")
    lr_stock_note = fields.Char("Stock Position", compute="_compute_lr_stock_signal")

    @api.depends("product_id", "product_uom_qty", "product_uom_id", "order_id.warehouse_id", "state")
    def _compute_lr_stock_signal(self):
        self.lr_stock_signal = False
        self.lr_stock_note = False
        lines = self.filtered(lambda l: l.product_id.is_storable and l.state in ("draft", "sent")
                              and l.order_id.warehouse_id)
        by_wh = defaultdict(lambda: self.env["sale.order.line"])
        for line in lines:
            by_wh[line.order_id.warehouse_id] |= line
        for warehouse, wh_lines in by_wh.items():
            products = wh_lines.product_id.with_context(warehouse_id=warehouse.id)
            stock = {p.id: (p.qty_available - p.outgoing_qty, p.virtual_available) for p in products}
            arrivals = wh_lines.product_id._lr_incoming_by_product(warehouse)
            for line in wh_lines:
                free, forecast = stock[line.product_id.id]
                qty = line.product_uom_id._compute_quantity(line.product_uom_qty, line.product_id.uom_id)
                date, incoming = arrivals.get(line.product_id.id, (False, 0.0))
                arrival = _("%(qty)d arriving %(date)s", qty=incoming,
                            date=date.strftime("%d %b") if date else "") if incoming else ""
                if free >= qty:
                    line.lr_stock_signal = "green"
                    line.lr_stock_note = _("Free %d", free)
                elif forecast >= qty:
                    line.lr_stock_signal = "amber"
                    line.lr_stock_note = _("Free %(free)d, %(arr)s", free=max(free, 0), arr=arrival)
                else:
                    line.lr_stock_signal = "red"
                    note = _("Short by %d", qty - max(free, 0))
                    line.lr_stock_note = "%s; %s" % (note, arrival) if arrival else note
