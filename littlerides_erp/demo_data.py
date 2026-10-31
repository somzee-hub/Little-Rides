"""Demo data for Little Rides India, shaped on the 19 Oct 2026 discovery call.

Story in the data:
  granules arrive daily (PPCP and virgin, by weight) -> parts moulded at the factory, first shots and
  runners ground back to regrind -> moulded parts moved to Alipur -> bought-out parts (batteries,
  motors, music chips, remotes) from China and local vendors -> assembly of B8, 7373 (battery and
  motor options) and 2188 (five bonnet faces, rest of the car the same) -> offline dealer orders
  from the WhatsApp group and online orders from Amazon and Flipkart out of separate stock ->
  spare parts sold from component stock -> own trucks for dispatch.

Product names are the client's models. Weights, costs, prices, partners and quantities are illustrative.
Every record created here is tracked so the Demo Data screen can remove it again.
"""
import logging
from datetime import timedelta

from odoo import Command, fields

_logger = logging.getLogger(__name__)
USD = 88.0


def _vals(model, vals):
    return {k: v for k, v in vals.items() if k in model._fields}


class DemoLoader:
    # Moulded today and moved to Alipur: enough for today's assembly plus a buffer for tomorrow
    MOULDED_TODAY = [("M-WHL12", 3600), ("M-STR", 900), ("M-BMP", 900), ("M-DASH", 900), ("M-HLC", 1800),
                     ("M-SEAT", 700), ("M-BODY-B8", 260), ("M-BODY-7373", 400), ("M-BODY-2188", 190),
                     ("M-BON-B8", 260), ("M-BON-7373", 400)] + [
        ("M-BON-2188-%s" % f, 38) for f in ("CL", "SP", "RA", "SA", "PO")]

    def __init__(self, env):
        self.env = env
        self.company = env.company
        self.tracked = []
        self.p = {}      # products by code
        self.t = {}      # templates by code
        self.partner = {}
        self.users = {}
        self.wh = {}

    # -- tracking ----------------------------------------------------------
    def track(self, records):
        for rec in records:
            self.tracked.append((rec._name, rec.id))
        return records

    def flush_tracking(self):
        Rec = self.env["lr.demo.record"]
        existing = set()
        vals = []
        for model, rid in self.tracked:
            if (model, rid) not in existing:
                existing.add((model, rid))
                vals.append({"res_model": model, "res_id": rid})
        Rec.create(vals)
        self.tracked = []

    def create(self, model, vals):
        Model = self.env[model]
        if isinstance(vals, list):
            return self.track(Model.create([_vals(Model, v) for v in vals]))
        return self.track(Model.create(_vals(Model, vals)))

    # -- run ---------------------------------------------------------------
    def run(self):
        c = self.company
        self.wh = {"FAC": c.lr_factory_warehouse_id, "ALP": c.lr_offline_warehouse_id, "ONL": c.lr_online_warehouse_id}
        if not all(self.wh.values()):
            raise ValueError("Little Rides warehouses are not configured on the company.")
        for key, login in (("director", "m.nijhawan"), ("pranav", "pranav"), ("tanav", "tanav"),
                           ("shop", "shop.sales"), ("online", "online"), ("accounts", "accounts"),
                           ("store", "store"), ("moulding", "moulding")):
            self.users[key] = self.env["res.users"].search([("login", "=", login)], limit=1) or self.env.user
        self.taxes = self._taxes()
        self._partners()
        self._products()
        self._boms()
        self._opening_stock()
        self._purchases()
        self._moulding()
        self._transfer_to_alipur()
        self._assembly()
        self._transfer_to_online()
        self._sales()
        self._crm()
        self._fleet()
        self._pending_approvals()
        self.env["product.product"]._cron_lr_component_alert()

    # -- helpers -----------------------------------------------------------
    def _taxes(self):
        tmpl = self.env["account.chart.template"].with_company(self.company)

        def ref(xml):
            return tmpl.ref(xml, raise_if_not_found=False) or self.env["account.tax"]
        return {"s18": ref("sgst_sale_18"), "p18": ref("sgst_purchase_18")}

    def _state(self, code):
        return self.env["res.country.state"].search([("country_id.code", "=", "IN"), ("code", "=", code)], limit=1)

    def _done(self, picking):
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking._action_done()
        return picking

    def _complete_mo(self, mo):
        mo.action_assign()
        mo.qty_producing = mo.product_qty
        mo.set_qty_producing()
        for move in mo.move_raw_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        for wo in mo.workorder_ids:
            wo.button_start()
            wo.button_finish()
        res = mo.button_mark_done()
        if mo.state != "done":
            _logger.warning("Demo MO %s not closed: %s", mo.name, res)

    # -- masters -----------------------------------------------------------
    def _partners(self):
        india, china = self.env.ref("base.in"), self.env.ref("base.cn")
        usd = self.env.ref("base.USD")
        Partner = self.env["res.partner"]
        has_treat = "l10n_in_gst_treatment" in Partner._fields
        pl = {k: self.env.ref("littlerides_erp.pricelist_%s" % k) for k in ("cat_a", "cat_b", "cat_c", "online")}

        def mk(key, name, city, state=None, country=india, vendor=False, treat="unregistered",
               pricelist=None, user=None, currency=None):
            vals = {"name": name, "is_company": True, "city": city, "country_id": country.id,
                    "supplier_rank": 1 if vendor else 0, "customer_rank": 0 if vendor else 1}
            if state:
                vals["state_id"] = self._state(state).id
            if has_treat:
                vals["l10n_in_gst_treatment"] = treat
            if user:
                vals["user_id"] = user.id
            partner = self.create("res.partner", vals)
            if pricelist:
                partner.property_product_pricelist = pricelist.id
            if currency and "property_purchase_currency_id" in partner._fields:
                partner.property_purchase_currency_id = currency.id
            self.partner[key] = partner

        u = self.users
        mk("granules", "Shree Balaji Polymers", "Bawana, Delhi", "DL", vendor=True)
        mk("china_elec", "Shantou Kidstar Electronics Co., Ltd.", "Shantou, Guangdong", country=china,
           vendor=True, treat="overseas", currency=usd)
        mk("china_motor", "Ningbo Haotian Micro Motor Co., Ltd.", "Ningbo, Zhejiang", country=china,
           vendor=True, treat="overseas", currency=usd)
        mk("battery_local", "Sun Power Batteries", "Narela, Delhi", "DL", vendor=True)
        mk("hardware", "Alipur Hardware and Packaging", "Alipur, Delhi", "DL", vendor=True)
        mk("transport", "Ganpati Truck Hire Services", "Azadpur, Delhi", "DL", vendor=True)
        mk("sharma", "Sharma Toys, Sadar Bazar", "New Delhi", "DL", pricelist=pl["cat_b"], user=u["tanav"])
        mk("gupta", "Gupta Kids World", "Meerut", "UP", pricelist=pl["cat_a"], user=u["director"])
        mk("raj", "Raj Toy Centre, Karol Bagh", "New Delhi", "DL", pricelist=pl["cat_c"], user=u["shop"])
        mk("ludhiana", "Punjab Khilona Bhandar", "Ludhiana", "PB", pricelist=pl["cat_b"], user=u["tanav"])
        mk("jaipur", "Pink City Toys", "Jaipur", "RJ", pricelist=pl["cat_b"], user=u["shop"])
        mk("amazon", "Amazon Marketplace Orders", "Online", "DL", pricelist=pl["online"], user=u["online"],
           treat="consumer")
        mk("flipkart", "Flipkart Marketplace Orders", "Online", "DL", pricelist=pl["online"], user=u["online"],
           treat="consumer")

    def _products(self):
        cat = {}
        root = self.create("product.category", {"name": "Little Rides"})
        for key, name in (("finished", "Ride-Ons"), ("moulded", "Moulded Parts"),
                          ("bought", "Bought-out Parts"), ("granule", "Granules")):
            cat[key] = self.create("product.category", {"name": name, "parent_id": root.id})
        kg = self.env.ref("uom.product_uom_kgm")
        buy = self.env.ref("purchase_stock.route_warehouse0_buy", raise_if_not_found=False)
        make = self.env.ref("mrp.route_warehouse0_manufacture", raise_if_not_found=False)
        s18, p18 = self.taxes["s18"], self.taxes["p18"]
        T = self.env["product.template"]

        def product(code, name, source, cost, price=0.0, hsn="95030040", weight=0.0, uom=None,
                    sale=False, route=None, vendor=None, vendor_price=0.0, currency="INR", lead=3, extra=None):
            vals = {
                "name": name, "default_code": code, "categ_id": cat[source].id, "type": "consu",
                "is_storable": True, "standard_price": cost, "list_price": price or cost * 1.6,
                "sale_ok": sale, "purchase_ok": source in ("bought", "granule"), "lr_part_source": source,
                "lr_part_weight": weight, "l10n_in_hsn_code": hsn,
                "taxes_id": [Command.set(s18.ids)],
                "supplier_taxes_id": [Command.set(p18.ids if currency == "INR" else [])],
            }
            if uom:
                vals["uom_id"] = uom.id
            if route:
                vals["route_ids"] = [Command.set(route.ids)]
            vals.update(extra or {})
            tmpl = self.track(T.create(_vals(T, vals)))
            self.t[code] = tmpl
            self.p[code] = tmpl.product_variant_ids[:1]
            if vendor:
                self.create("product.supplierinfo", {
                    "partner_id": self.partner[vendor].id, "product_tmpl_id": tmpl.id, "price": vendor_price,
                    "currency_id": self.env.ref("base.%s" % currency).id, "delay": lead, "min_qty": 1})
            return tmpl

        # Granules, bought by weight every day
        product("G-PPCP", "PPCP Granules (black)", "granule", 95, hsn="39021000", uom=kg, route=buy,
                vendor="granules", vendor_price=95)
        product("G-VIRGIN", "Virgin PP Granules (colour)", "granule", 165, hsn="39021000", uom=kg, route=buy,
                vendor="granules", vendor_price=165)
        product("G-REGRIND", "Regrind (from runners and first shots)", "granule", 40, hsn="39151000", uom=kg)

        # Moulded parts: weight per piece drives granule consumption
        for code, name, wt, sale_price in (
                ("M-WHL12", "Wheel 12 inch (common to B8, 7373, 2188)", 1.0, 350),
                ("M-STR", "Steering Wheel (common)", 0.35, 220),
                ("M-BMP", "Bumper Set (common)", 0.8, 0),
                ("M-DASH", "Dashboard Panel (common)", 0.4, 0),
                ("M-HLC", "Headlight Cover (common)", 0.05, 120),
                ("M-SEAT", "Seat (common to 7373 and 2188)", 0.6, 0),
                ("M-BODY-B8", "Body Shell B8", 3.0, 0),
                ("M-BODY-7373", "Body Shell 7373", 3.4, 0),
                ("M-BODY-2188", "Body Shell 2188", 3.6, 0),
                ("M-BON-B8", "Bonnet B8", 0.6, 0),
                ("M-BON-7373", "Bonnet 7373", 0.65, 0),
                ("M-BON-2188-CL", "Bonnet 2188, Classic face", 0.7, 0),
                ("M-BON-2188-SP", "Bonnet 2188, Sport face", 0.7, 0),
                ("M-BON-2188-RA", "Bonnet 2188, Rally face", 0.7, 0),
                ("M-BON-2188-SA", "Bonnet 2188, Safari face", 0.7, 0),
                ("M-BON-2188-PO", "Bonnet 2188, Police face", 0.7, 0)):
            product(code, name, "moulded", round(wt * 120 + 8, 2), price=sale_price or wt * 250,
                    weight=wt, sale=bool(sale_price), route=make)

        # Bought-out parts
        for code, name, cost, cur, vendor, lead, sale_price, hsn in (
                ("B-MTR550", "Motor 550 with Gearbox", 4.8, "USD", "china_motor", 45, 950, "85013119"),
                ("B-BAT12", "Battery 12V 7Ah (imported)", 7.5, "USD", "china_elec", 45, 1250, "85072000"),
                ("B-BAT12L", "Battery 12V 7Ah (local)", 720, "INR", "battery_local", 3, 1100, "85072000"),
                ("B-MUSIC", "Music Chip and Speaker Module", 1.6, "USD", "china_elec", 45, 0, "85182900"),
                ("B-RMT", "Parental Remote 2.4G with Receiver", 3.0, "USD", "china_elec", 45, 690, "85269190"),
                ("B-CHG", "Charger 12V", 1.9, "USD", "china_elec", 45, 450, "85044010"),
                ("B-AXLE", "Axle Rod Set", 85, "INR", "hardware", 2, 0, "73181500"),
                ("B-WIRE", "Wiring Harness", 60, "INR", "hardware", 2, 0, "85444999"),
                ("B-SCREW", "Screw and Fastener Kit", 25, "INR", "hardware", 2, 0, "73181500"),
                ("B-CTN", "Master Carton, 5-ply", 140, "INR", "hardware", 2, 0, "48191010")):
            inr_cost = cost * USD if cur == "USD" else cost
            product(code, name, "bought", inr_cost, price=sale_price or inr_cost * 1.5, hsn=hsn,
                    sale=bool(sale_price), route=buy, vendor=vendor, vendor_price=cost, currency=cur, lead=lead)

        # Finished models and their options
        Attr = self.env["product.attribute"]
        battery = self.track(Attr.create({"name": "Battery", "create_variant": "always", "value_ids": [
            Command.create({"name": n}) for n in ("Standard 12V", "Local 12V", "No Battery")]}))
        motor = self.track(Attr.create({"name": "Motor", "create_variant": "always", "value_ids": [
            Command.create({"name": n}) for n in ("2 Motor", "4 Motor")]}))
        face = self.track(Attr.create({"name": "Bonnet Face", "create_variant": "always", "value_ids": [
            Command.create({"name": n}) for n in ("Classic", "Sport", "Rally", "Safari", "Police")]}))
        self.attr = {"battery": battery, "motor": motor, "face": face}

        product("B8", "Jeep B8 Battery Operated Ride-On", "finished", 4200, price=7999, sale=True, route=make)
        product("7373", "Jeep 7373 Battery Operated Ride-On", "finished", 3600, price=6499, sale=True, route=make,
                extra={"attribute_line_ids": [
                    Command.create({"attribute_id": battery.id, "value_ids": [Command.set(battery.value_ids.ids)]}),
                    Command.create({"attribute_id": motor.id, "value_ids": [Command.set(motor.value_ids.ids)]})]})
        product("2188", "Jeep 2188 Battery Operated Ride-On", "finished", 5200, price=9999, sale=True, route=make,
                extra={"attribute_line_ids": [
                    Command.create({"attribute_id": face.id, "value_ids": [Command.set(face.value_ids.ids)]})]})

        # Variant codes as the team uses them: 7373-1 ... 7373-6, 2188-CL ...
        t7373 = self.t["7373"]
        extras = {"4 Motor": 1200, "Local 12V": -400, "No Battery": -1100}
        for ptav in t7373.attribute_line_ids.product_template_value_ids:
            ptav.price_extra = extras.get(ptav.name, 0)
        order = [("Standard 12V", "2 Motor"), ("Standard 12V", "4 Motor"), ("Local 12V", "2 Motor"),
                 ("Local 12V", "4 Motor"), ("No Battery", "2 Motor"), ("No Battery", "4 Motor")]
        for variant in t7373.product_variant_ids:
            names = tuple(variant.product_template_attribute_value_ids.sorted(
                lambda v: v.attribute_id == motor).mapped("name"))
            idx = order.index(names) + 1 if names in order else 0
            variant.default_code = "7373-%s" % idx
            self.p["7373-%s" % idx] = variant
        for variant in self.t["2188"].product_variant_ids:
            code = "2188-%s" % variant.product_template_attribute_value_ids.name[:2].upper()
            variant.default_code = code
            self.p[code] = variant
        self.p["B8"].default_code = "B8"

    def _ptav(self, code, value_name):
        return self.t[code].attribute_line_ids.product_template_value_ids.filtered(lambda v: v.name == value_name)

    def _boms(self):
        p = self.p
        WC = self.env["mrp.workcenter"]

        def wc(code):
            return WC.search([("code", "=", code)], limit=1)
        fac_mo = self.wh["FAC"].manu_type_id
        alp_mo = self.wh["ALP"].manu_type_id
        Bom = self.env["mrp.bom"]
        kg = self.env.ref("uom.product_uom_kgm")

        # Moulding BOMs: granules by weight plus 3% runner and first-shot loss returned as regrind
        machine = {"M-WHL12": "IMM2", "M-BODY-B8": "IMM3", "M-BODY-7373": "IMM3", "M-BODY-2188": "IMM3"}
        for code, tmpl in self.t.items():
            if tmpl.lr_part_source != "moulded":
                continue
            wt = tmpl.lr_part_weight
            virgin = code.startswith(("M-BON", "M-SEAT", "M-HLC"))
            lines = []
            if code.startswith("M-BODY"):
                lines = [(p["G-PPCP"], round(wt * 0.93, 3)), (p["G-REGRIND"], round(wt * 0.10, 3))]
            else:
                lines = [(p["G-VIRGIN" if virgin else "G-PPCP"], round(wt * 1.03, 3))]
            self.track(Bom.create({
                "product_tmpl_id": tmpl.id, "product_qty": 1, "type": "normal", "picking_type_id": fac_mo.id,
                "bom_line_ids": [Command.create({"product_id": prod.id, "product_qty": q, "product_uom_id": kg.id})
                                 for prod, q in lines],
                "byproduct_ids": [Command.create({"product_id": p["G-REGRIND"].id, "product_qty": round(wt * 0.03, 3),
                                                  "product_uom_id": kg.id})],
                "operation_ids": [Command.create({
                    "name": "Injection moulding (first shot to grinder)", "workcenter_id": wc(machine.get(code, "IMM1")).id,
                    "time_cycle_manual": round(0.5 + wt * 0.4, 2)})],
            }))

        def ops():
            return [
                Command.create({"name": "Body, bonnet, wheels and wiring", "workcenter_id": wc("ALP-ASM").id,
                                "time_cycle_manual": 18, "sequence": 10}),
                Command.create({"name": "Battery fitting and charge", "workcenter_id": wc("ALP-BAT").id,
                                "time_cycle_manual": 5, "sequence": 20}),
                Command.create({"name": "Inspection and test ride", "workcenter_id": wc("ALP-QC").id,
                                "time_cycle_manual": 6, "sequence": 30}),
                Command.create({"name": "Packing in master carton", "workcenter_id": wc("ALP-PCK").id,
                                "time_cycle_manual": 4, "sequence": 40}),
            ]

        common = [("M-WHL12", 4), ("M-STR", 1), ("M-BMP", 1), ("M-DASH", 1), ("M-HLC", 2),
                  ("B-MUSIC", 1), ("B-AXLE", 1), ("B-WIRE", 1), ("B-SCREW", 1), ("B-CTN", 1)]

        def line(code, qty, ptavs=None):
            vals = {"product_id": p[code].id, "product_qty": qty}
            if ptavs:
                vals["bom_product_template_attribute_value_ids"] = [Command.set(ptavs.ids)]
            return Command.create(vals)

        # B8: single model
        self.track(Bom.create({
            "product_tmpl_id": self.t["B8"].id, "product_qty": 1, "picking_type_id": alp_mo.id,
            "bom_line_ids": [line(c, q) for c, q in common] + [
                line("M-BODY-B8", 1), line("M-BON-B8", 1), line("B-MTR550", 2), line("B-BAT12", 1), line("B-CHG", 1)],
            "operation_ids": ops()}))

        # 7373: battery option and 2 or 4 motors on one BoM
        bat_std, bat_loc = self._ptav("7373", "Standard 12V"), self._ptav("7373", "Local 12V")
        m2, m4 = self._ptav("7373", "2 Motor"), self._ptav("7373", "4 Motor")
        self.track(Bom.create({
            "product_tmpl_id": self.t["7373"].id, "product_qty": 1, "picking_type_id": alp_mo.id,
            "bom_line_ids": [line(c, q) for c, q in common] + [
                line("M-BODY-7373", 1), line("M-BON-7373", 1), line("M-SEAT", 1), line("B-RMT", 1),
                line("B-MTR550", 2, m2), line("B-MTR550", 4, m4),
                line("B-BAT12", 1, bat_std), line("B-BAT12L", 1, bat_loc), line("B-CHG", 1, bat_std | bat_loc)],
            "operation_ids": ops()}))

        # 2188: same car, only the bonnet face differs
        face_lines = [line("M-BON-2188-%s" % f[:2].upper(), 1, self._ptav("2188", f))
                      for f in ("Classic", "Sport", "Rally", "Safari", "Police")]
        self.track(Bom.create({
            "product_tmpl_id": self.t["2188"].id, "product_qty": 1, "picking_type_id": alp_mo.id,
            "bom_line_ids": [line(c, q) for c, q in common] + face_lines + [
                line("M-BODY-2188", 1), line("M-SEAT", 1), line("B-RMT", 1), line("B-MTR550", 2),
                line("B-BAT12", 1), line("B-CHG", 1)],
            "operation_ids": ops()}))

        # Reorder rules: batteries and China parts at Alipur, minimum 2,000
        for code, mn, mx in (("B-BAT12", 2000, 22000), ("B-MUSIC", 2000, 12000), ("B-RMT", 1500, 8000),
                             ("B-MTR550", 4000, 20000)):
            self.create("stock.warehouse.orderpoint", {
                "product_id": p[code].id, "warehouse_id": self.wh["ALP"].id,
                "location_id": self.wh["ALP"].lot_stock_id.id, "product_min_qty": mn, "product_max_qty": mx,
                "trigger": "manual"})

    def _opening_stock(self):
        Quant = self.env["stock.quant"].with_context(inventory_mode=True)
        fac, alp = self.wh["FAC"].lot_stock_id, self.wh["ALP"].lot_stock_id
        quants = Quant
        for code, loc, qty in (("G-PPCP", fac, 2000), ("G-VIRGIN", fac, 600), ("G-REGRIND", fac, 300),
                               ("B-BAT12", alp, 1200), ("B-MUSIC", alp, 1100), ("M-HLC", alp, 3000)):
            quants |= Quant.create({"product_id": self.p[code].id, "location_id": loc.id, "inventory_quantity": qty})
        quants.action_apply_inventory()

    def _po(self, vendor, lines, ref, days_ago=0, planned_in=0, receive=True, bill=False, picking_type=None):
        PO = self.env["purchase.order"]
        vals = {
            "partner_id": self.partner[vendor].id, "partner_ref": ref,
            "date_order": fields.Datetime.now() - timedelta(days=days_ago),
            "order_line": [Command.create({"product_id": self.p[c].id, "product_qty": q}) for c, q in lines],
        }
        if picking_type:
            vals["picking_type_id"] = picking_type.id
        po = self.track(PO.create(vals))
        if planned_in:
            po.order_line.write({"date_planned": fields.Datetime.now() + timedelta(days=planned_in)})
        po.button_confirm()
        if receive:
            for picking in po.picking_ids:
                self._done(picking)
        if bill:
            po.action_create_invoice()
            for inv in po.invoice_ids:
                inv.invoice_date = fields.Date.today() - timedelta(days=max(days_ago - 1, 0))
                inv.ref = "%s-INV" % ref
                inv.action_post()
        return po

    def _purchases(self):
        fac_in = self.wh["FAC"].in_type_id
        alp_in = self.wh["ALP"].in_type_id
        # Daily granule truck, about 5,000 kg PPCP and 800 kg virgin
        self._po("granules", [("G-PPCP", 5000), ("G-VIRGIN", 800)], "SBP/DC/1018", days_ago=2,
                 bill=True, picking_type=fac_in)
        self._po("granules", [("G-PPCP", 5000), ("G-VIRGIN", 800)], "SBP/DC/1019", days_ago=1,
                 picking_type=fac_in)
        self._po("granules", [("G-PPCP", 5000), ("G-VIRGIN", 800)], "SBP/DC/1020", planned_in=1,
                 receive=False, picking_type=fac_in)
        # China shipment received at Alipur and billed in USD
        self._po("china_motor", [("B-MTR550", 9000)], "HT-PI-2026-311", days_ago=20, bill=True,
                 picking_type=alp_in)
        self._po("china_elec", [("B-RMT", 3500), ("B-CHG", 4000)], "KS-PI-2026-208", days_ago=18, bill=True,
                 picking_type=alp_in)
        # Music chips on the water, about 40 days out. Batteries not yet ordered: the cover report flags them.
        self._po("china_elec", [("B-MUSIC", 10000)], "KS-PI-2026-241", days_ago=5,
                 planned_in=40, receive=False, picking_type=alp_in)
        # Local purchases
        self._po("battery_local", [("B-BAT12L", 800)], "SPB/Q/77", days_ago=4, bill=True, picking_type=alp_in)
        self._po("hardware", [("B-AXLE", 6000), ("B-WIRE", 6000), ("B-SCREW", 8000), ("B-CTN", 3000)],
                 "AHP/221", days_ago=6, bill=True, picking_type=alp_in)

    def _moulding(self):
        MO = self.env["mrp.production"]
        fac_mo = self.wh["FAC"].manu_type_id
        # The 12 extra wheels are first-shot rejects waiting at the factory for scrap approval
        plan = [(code, qty + (12 if code == "M-WHL12" else 0)) for code, qty in self.MOULDED_TODAY]
        for code, qty in plan:
            mo = self.track(MO.create({"product_id": self.p[code].id, "product_qty": qty,
                                       "picking_type_id": fac_mo.id, "user_id": self.users["moulding"].id}))
            mo.action_confirm()
            self._complete_mo(mo)
        # Tomorrow's moulding plan
        for code, qty in (("M-WHL12", 2000), ("M-BODY-7373", 300)):
            mo = self.track(MO.create({"product_id": self.p[code].id, "product_qty": qty, "picking_type_id": fac_mo.id,
                                       "date_start": fields.Datetime.now() + timedelta(days=1),
                                       "user_id": self.users["moulding"].id}))
            mo.action_confirm()

    def _transfer(self, picking_type, lines, origin):
        Picking = self.env["stock.picking"]
        picking = self.track(Picking.create({
            "picking_type_id": picking_type.id, "origin": origin,
            "location_id": picking_type.default_location_src_id.id,
            "location_dest_id": picking_type.default_location_dest_id.id,
            "move_ids": [Command.create({
                "product_id": self.p[c].id, "product_uom_qty": q, "product_uom": self.p[c].uom_id.id,
                "location_id": picking_type.default_location_src_id.id,
                "location_dest_id": picking_type.default_location_dest_id.id}) for c, q in lines],
        }))
        return self._done(picking)

    def _transfer_to_alipur(self):
        pt = self.env["stock.picking.type"].search([("sequence_code", "=", "FAC/ALP")], limit=1)
        self._transfer(pt, self.MOULDED_TODAY, "Day shift moulding")

    def _assembly(self):
        MO = self.env["mrp.production"]
        alp_mo = self.wh["ALP"].manu_type_id
        plan = [("B8", 200), ("7373-1", 150), ("7373-2", 50), ("7373-3", 50)] + [
            ("2188-%s" % f, 30) for f in ("CL", "SP", "RA", "SA", "PO")]
        for code, qty in plan:
            mo = self.track(MO.create({"product_id": self.p[code].id, "product_qty": qty,
                                       "picking_type_id": alp_mo.id}))
            mo.action_confirm()
            self._complete_mo(mo)
        # Open order for Diwali dealer stock: short on wheels until tomorrow's moulding is moved
        mo = self.track(MO.create({"product_id": self.p["7373-1"].id, "product_qty": 100,
                                   "picking_type_id": alp_mo.id, "origin": "Diwali dealer stock",
                                   "date_start": fields.Datetime.now() + timedelta(days=2)}))
        mo.action_confirm()

    def _transfer_to_online(self):
        pt = self.env["stock.picking.type"].search([("sequence_code", "=", "ALP/ONL")], limit=1)
        self._transfer(pt, [("B8", 40), ("7373-1", 20), ("2188-SP", 10)], "Weekly online top-up")

    def _so(self, partner, lines, user, channel, transport="customer", source=None, team=None, term=None, days_ago=0):
        SO = self.env["sale.order"]
        vals = {
            "partner_id": self.partner[partner].id, "user_id": self.users[user].id, "lr_channel": channel,
            "lr_transport_mode": transport,
            "date_order": fields.Datetime.now() - timedelta(days=days_ago),
            "order_line": [Command.create({"product_id": self.p[c].id, "product_uom_qty": q}) for c, q in lines],
        }
        if source:
            vals["source_id"] = self.env.ref("littlerides_erp.utm_source_%s" % source).id
        if team:
            vals["team_id"] = team.id
        if term:
            vals["payment_term_id"] = term.id
        so = self.track(SO.create(vals))
        so._compute_warehouse_id()
        return so

    def _deliver_invoice(self, so, vehicle=None, pay=0.0):
        so.action_confirm()
        for picking in so.picking_ids:
            if vehicle:
                picking.lr_vehicle_id = vehicle.id
            self._done(picking)
        inv = so._create_invoices()
        inv.action_post()
        if pay:
            self.env["account.payment.register"].with_context(
                active_model="account.move", active_ids=inv.ids).create({"amount": inv.amount_total * pay})._create_payments()
        return inv

    def _sales(self):
        Team = self.env["crm.team"]
        offline = Team.search([("name", "=", "Offline: Shop and Dealers")], limit=1)
        online = Team.search([("name", "=", "Online: Amazon and Flipkart")], limit=1)
        Term = self.env["account.payment.term"]
        adv = Term.search([("name", "=", "100% Advance")], limit=1)
        part = Term.search([("name", "=", "30% Advance, 70% in 15 Days")], limit=1)
        self.trucks = self._trucks()

        # 1. WhatsApp group order from a Delhi dealer, own truck, delivered and half paid
        so = self._so("sharma", [("B8", 80), ("7373-1", 60)], "tanav", "offline_bulk", "own_truck",
                      "whatsapp_group", offline, part, days_ago=1)
        self._deliver_invoice(so, vehicle=self.trucks[0], pay=0.3)
        # 2. Papa's order for Meerut, confirmed and reserved, customer sends truck
        so = self._so("gupta", [("7373-1", 50), ("2188-CL", 20)], "director", "offline_bulk", "customer",
                      "whatsapp_group", offline, adv)
        so.action_confirm()
        # 3. Shop quotation that runs into the stock already promised: amber and red lines
        self._so("raj", [("7373-1", 60), ("7373-2", 80), ("B8", 20)], "shop", "offline_bulk", "customer",
                 "shop", offline)
        # 4. Punjab dealer quotation (IGST)
        self._so("ludhiana", [("2188-SP", 10), ("2188-RA", 10), ("B8", 30)], "tanav", "offline_bulk", "customer",
                 "indiamart", offline, part)
        # 5. Single-piece orders from Amazon and Flipkart, shipped from online stock
        so = self._so("amazon", [("B8", 1)], "online", "amazon", "courier", "amazon", online)
        self._deliver_invoice(so)
        so = self._so("flipkart", [("2188-SP", 1)], "online", "flipkart", "courier", "flipkart", online)
        self._deliver_invoice(so)
        so = self._so("amazon", [("7373-1", 1)], "online", "amazon", "courier", "amazon", online)
        so.action_confirm()
        # 6. Spare parts from component stock: two headlight covers and a remote
        so = self._so("sharma", [("M-HLC", 2), ("B-RMT", 1)], "tanav", "offline_single", "customer",
                      "whatsapp_group", offline)
        self._deliver_invoice(so)

    def _crm(self):
        u = self.users
        stages = self.env["crm.stage"].search([("is_won", "=", False)], order="sequence")
        rows = [
            ("200 jeeps for Diwali, Agra", "Agra Toy Mart", "whatsapp_group", u["tanav"], 0, 1300000),
            ("Dealer enquiry from Dehradun", "Doon Kids Store", "indiamart", u["shop"], 0, 450000),
            ("Monthly 7373 supply, Jaipur", "Pink City Toys", "indiamart", u["shop"], 1, 800000),
            ("Bulk 2188 Police face for school event", "Little Champs School", "referral", u["tanav"], 1, 180000),
            ("Amazon listing for 2188 faces", "Amazon Marketplace Orders", "amazon", u["online"], 2, 600000),
            ("Spare wheels and remotes, monthly", "Punjab Khilona Bhandar", "referral", u["tanav"], 2, 90000),
            ("Distributor for Bihar", "Magadh Toy House", "indiamart", u["director"], 1, 2500000),
        ]
        for name, company, src, user, stage_idx, rev in rows:
            vals = {"name": name, "partner_name": company, "type": "opportunity", "user_id": user.id,
                    "expected_revenue": rev, "source_id": self.env.ref("littlerides_erp.utm_source_%s" % src).id}
            if stages:
                vals["stage_id"] = stages[min(stage_idx, len(stages) - 1)].id
            self.create("crm.lead", vals)

    def _trucks(self):
        Brand = self.env["fleet.vehicle.model.brand"]
        brand = Brand.search([("name", "=", "Tata Motors")], limit=1) or self.create(
            "fleet.vehicle.model.brand", {"name": "Tata Motors"})
        model = self.create("fleet.vehicle.model", {"name": "407 Gold SFC", "brand_id": brand.id,
                                                    "vehicle_type": "car"})
        trucks = []
        for i, plate in enumerate(("DL 1LAB 4071", "DL 1LAB 4072", "DL 1LAB 4073", "DL 1LAB 4074"), start=1):
            driver = self.create("res.partner", {"name": "Driver, Truck %s" % i})
            truck = self.create("fleet.vehicle", {"model_id": model.id, "license_plate": plate,
                                                  "driver_id": driver.id})
            self.create("fleet.vehicle.log.contract", {
                "vehicle_id": truck.id, "name": "Monthly hire, truck %s" % i,
                "insurer_id": self.partner["transport"].id, "amount": 45000, "cost_generated": 45000,
                "cost_frequency": "monthly", "start_date": fields.Date.today() - timedelta(days=120),
                "expiration_date": fields.Date.today() + timedelta(days=245)})
            trucks.append(truck)
        return trucks

    def _fleet(self):
        return True  # trucks are created with the first sale so its delivery can name one

    def _pending_approvals(self):
        """Maker entries waiting for a checker."""
        # Store keeper counted batteries: 10 less than the system. Not applied until a checker approves.
        quant = self.env["stock.quant"].search([("product_id", "=", self.p["B-BAT12"].id),
                                                ("location_id", "=", self.wh["ALP"].lot_stock_id.id)], limit=1)
        if quant:
            quant.with_context(inventory_mode=True).write({
                "inventory_quantity": quant.quantity - 10, "user_id": self.users["store"].id})
        # First-shot rejects from the wheel mould, waiting for approval to scrap
        self.create("stock.scrap", {
            "product_id": self.p["M-WHL12"].id, "scrap_qty": 12, "product_uom_id": self.p["M-WHL12"].uom_id.id,
            "location_id": self.wh["FAC"].lot_stock_id.id, "origin": "First-shot rejects, IMM2"})
        # Truck hire voucher entered by accounts, to be posted by a checker
        Account = self.env["account.account"].with_company(self.company)
        expense = Account.search([("account_type", "=", "expense"), ("name", "ilike", "freight")], limit=1) or \
            Account.search([("account_type", "=", "expense")], limit=1)
        payable = Account.search([("account_type", "=", "liability_payable")], limit=1)
        journal = self.env["account.journal"].search([("type", "=", "general"),
                                                      ("company_id", "=", self.company.id)], limit=1)
        if expense and payable and journal:
            self.create("account.move", {
                "move_type": "entry", "journal_id": journal.id, "ref": "Truck hire, 4 trucks, this month",
                "line_ids": [
                    Command.create({"account_id": expense.id, "name": "Truck hire", "debit": 180000.0}),
                    Command.create({"account_id": payable.id, "name": "Ganpati Truck Hire Services",
                                    "partner_id": self.partner["transport"].id, "credit": 180000.0})]})
