from odoo import api, fields, models
from odoo.exceptions import UserError

from .sale_order import TRANSPORT

CHECKER = "littlerides_erp.group_lr_stock_checker"


def _require_stock_checker(env, action):
    if env.su or env.user.has_group(CHECKER):
        return
    raise UserError(env._(
        "%s needs approval. Your entry is saved; ask a stock checker "
        "(Pranav or Tanav) to approve it.", action))


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def _apply_inventory(self, date=None):
        _require_stock_checker(self.env, self.env._("Applying a stock count or adjustment"))
        return super()._apply_inventory(date)


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    def action_validate(self):
        _require_stock_checker(self.env, self.env._("Scrapping or rejecting stock"))
        return super().action_validate()


class StockPicking(models.Model):
    _inherit = "stock.picking"

    lr_channel = fields.Selection(string="Sales Channel", related="sale_id.lr_channel", store=True)
    lr_transport_mode = fields.Selection(
        TRANSPORT, string="Transport", compute="_compute_lr_transport_mode", store=True, readonly=False)
    lr_vehicle_id = fields.Many2one("fleet.vehicle", string="Own Truck")
    lr_driver_id = fields.Many2one(related="lr_vehicle_id.driver_id", string="Driver")

    @api.depends("sale_id.lr_transport_mode")
    def _compute_lr_transport_mode(self):
        for picking in self:
            if picking.sale_id and not picking.lr_transport_mode:
                picking.lr_transport_mode = picking.sale_id.lr_transport_mode
