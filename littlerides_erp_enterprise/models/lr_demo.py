import logging

from odoo import Command, models

_logger = logging.getLogger(__name__)


def _vals(model, vals):
    return {k: v for k, v in vals.items() if k in model._fields}


class LrDemoManager(models.TransientModel):
    _inherit = "lr.demo.manager"

    def _lr_load_demo_extra(self, loader):
        res = super()._lr_load_demo_extra(loader)
        for label, fn in (("quality points", self._lr_demo_quality), ("complaints", self._lr_demo_tickets)):
            try:
                with self.env.cr.savepoint():
                    fn(loader)
            except Exception as e:  # noqa: BLE001
                _logger.warning("Little Rides enterprise demo: %s skipped (%s)", label, e)
        return res

    def _lr_test_type(self, technical_name):
        TT = self.env["quality.point.test_type"]
        return TT.search([("technical_name", "=", technical_name)], limit=1)

    def _lr_demo_quality(self, loader):
        QP = self.env["quality.point"]
        passfail = self._lr_test_type("passfail")
        instructions = self._lr_test_type("instructions") or passfail
        wh = loader.wh
        moulded = self.env["product.product"].search([("lr_part_source", "=", "moulded")])
        finished = self.env["product.product"].search([("lr_part_source", "=", "finished")])
        points = [
            {"title": "Granule receipt: grade, colour and weight against delivery challan",
             "picking_type_ids": [Command.set(wh["FAC"].in_type_id.ids)],
             "product_ids": [Command.set((loader.p["G-PPCP"] | loader.p["G-VIRGIN"]).ids)],
             "test_type_id": passfail.id, "measure_on": "operation"},
            {"title": "First-article check after mould change: flash, short shot, weight",
             "picking_type_ids": [Command.set(wh["FAC"].manu_type_id.ids)],
             "product_ids": [Command.set(moulded.ids)],
             "test_type_id": passfail.id, "measure_on": "operation",
             "note": "<p>First shots go to the grinder. Record rejects as scrap for checker approval.</p>"},
            {"title": "China container receipt: carton count and transit damage",
             "picking_type_ids": [Command.set(wh["ALP"].in_type_id.ids)],
             "product_ids": [Command.set((loader.p["B-BAT12"] | loader.p["B-MUSIC"] | loader.p["B-RMT"]
                                          | loader.p["B-MTR550"]).ids)],
             "test_type_id": passfail.id, "measure_on": "operation"},
            {"title": "Test ride: forward, reverse, remote override, music, lights",
             "picking_type_ids": [Command.set(wh["ALP"].manu_type_id.ids)],
             "product_ids": [Command.set(finished.ids)],
             "test_type_id": passfail.id, "measure_on": "operation"},
            {"title": "Packing: manual, charger and warranty card in carton",
             "picking_type_ids": [Command.set(wh["ALP"].manu_type_id.ids)],
             "product_ids": [Command.set(finished.ids)],
             "test_type_id": instructions.id, "measure_on": "operation"},
        ]
        for vals in points:
            vals.setdefault("name", vals["title"][:40])
            loader.track(QP.create(_vals(QP, vals)))

    def _lr_demo_tickets(self, loader):
        team = self.env["helpdesk.team"].search([("name", "=", "Dealer and Customer Complaints")], limit=1)
        Ticket = self.env["helpdesk.ticket"]
        rows = [
            ("7373 stops after 10 minutes, battery suspected", "sharma", "7373-1", "2"),
            ("Amazon buyer: remote not pairing", "amazon", "7373-1", "1"),
            ("2188 Police face bonnet cracked in transit", "gupta", "2188-PO", "2"),
            ("Need 20 spare wheels for counter sale", "ludhiana", "M-WHL12", "0"),
        ]
        for name, partner, code, prio in rows:
            loader.track(Ticket.create(_vals(Ticket, {
                "name": name, "team_id": team.id, "partner_id": loader.partner[partner].id,
                "product_id": loader.p[code].id, "priority": prio})))

    def _lr_unload_demo_extra(self, products, partners):
        res = super()._lr_unload_demo_extra(products, partners)
        env = self.env
        pids, rids = products.ids or [0], partners.ids or [0]
        for model, domain in (("quality.alert", [("product_id", "in", pids)]),
                              ("quality.check", [("product_id", "in", pids)]),
                              ("helpdesk.ticket", [("partner_id", "in", rids)])):
            if model in env:
                records = env[model].search(domain)
                if records:
                    env.flush_all()
                    self._lr_purge(records._table, records.ids)
                    env.invalidate_all()
        return res
