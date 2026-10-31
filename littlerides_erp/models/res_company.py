from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    lr_factory_warehouse_id = fields.Many2one("stock.warehouse", string="Moulding Factory Warehouse")
    lr_offline_warehouse_id = fields.Many2one(
        "stock.warehouse", string="Offline Stock Warehouse",
        help="Dealer, bulk and single-piece shop orders ship from here.")
    lr_online_warehouse_id = fields.Many2one(
        "stock.warehouse", string="Online Stock Warehouse",
        help="Amazon, Flipkart and website orders ship from here.")
    lr_production_head_id = fields.Many2one(
        "res.users", string="Production Head",
        help="Gets a to-do when a sales order is confirmed with items short in stock.")
    lr_purchase_head_id = fields.Many2one(
        "res.users", string="Purchase Head",
        help="Gets a daily to-do for bought-out parts that will not last the supplier lead time.")
    lr_cover_safety_days = fields.Integer(
        string="Safety Days over Lead Time", default=15,
        help="A part is flagged 'Order Now' when its stock covers fewer days than supplier lead time plus these days.")

    def _lr_channel_warehouse(self, channel):
        self.ensure_one()
        if channel in ("amazon", "flipkart", "website"):
            return self.lr_online_warehouse_id
        if channel in ("offline_bulk", "offline_single"):
            return self.lr_offline_warehouse_id
        return self.env["stock.warehouse"]
