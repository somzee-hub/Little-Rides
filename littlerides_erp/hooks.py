"""Base configuration for Little Rides India. Runs once at install and stays when demo data is removed:
company and GST, warehouses for factory, Alipur assembly and online stock, transfer types,
work centres, sales teams, payment terms and the ten users discussed on the discovery call."""
import logging
from datetime import timedelta

from odoo import Command, fields

_logger = logging.getLogger(__name__)

USD_TO_INR = 88.0
DEFAULT_PASSWORD = "LittleRides@2026"


def _vals(model, vals):
    return {k: v for k, v in vals.items() if k in model._fields}


def post_init_hook(env):
    env = env(context=dict(env.context, tracking_disable=True, mail_create_nolog=True, no_reset_password=True))
    company = env.company
    _company(env, company)
    env = env(context=dict(env.context, allowed_company_ids=[company.id]))
    _settings(env)
    whs = _warehouses(env, company)
    _work_centres(env, whs)
    _payment_terms(env)
    users = _users(env, whs)
    _teams(env, users)
    company.write({
        "lr_factory_warehouse_id": whs["FAC"].id,
        "lr_offline_warehouse_id": whs["ALP"].id,
        "lr_online_warehouse_id": whs["ONL"].id,
        "lr_production_head_id": users["pranav"].id,
        "lr_purchase_head_id": users["pranav"].id,
    })
    _logger.info("Little Rides base configuration done")


def _company(env, company):
    company.write({
        "name": "Little Rides India Private Limited",
        "city": "New Delhi",
        "state_id": env.ref("base.state_in_dl").id,
        "country_id": env.ref("base.in").id,
        "phone": "+91 80476 32306",
        "company_registry": "U51900DL2012PTC240807",
        "currency_id": env.ref("base.INR").id,
    })
    company.partner_id.vat = "07AACCL3355Q1Z2"
    if company.chart_template != "in" and not env["account.move"].search_count([("company_id", "=", company.id)], limit=1):
        env["account.chart.template"].try_loading("in", company, install_demo=False)
    if hasattr(env.registry, "_auto_install_template"):
        del env.registry._auto_install_template
    # Price lists created by module data before the company switched to INR
    env["product.pricelist"].search([("currency_id", "!=", company.currency_id.id)]).write(
        {"currency_id": company.currency_id.id})
    usd = env.ref("base.USD")
    usd.active = True
    if not env["res.currency.rate"].search_count([("currency_id", "=", usd.id), ("company_id", "=", company.id)]):
        env["res.currency.rate"].create({
            "currency_id": usd.id, "company_id": company.id,
            "name": fields.Date.today() - timedelta(days=90), "rate": 1.0 / USD_TO_INR,
        })


def _settings(env):
    wanted = {
        "group_stock_multi_locations": True,
        "group_product_variant": True,
        "group_product_pricelist": True,
        "group_uom": True,
        "group_mrp_routings": True,
        "group_mrp_byproducts": True,
        "group_discount_per_so_line": True,
        "group_sale_delivery_address": True,
    }
    Settings = env["res.config.settings"]
    Settings.create({k: v for k, v in wanted.items() if k in Settings._fields}).execute()


def _warehouses(env, company):
    WH = env["stock.warehouse"]
    fac = WH.search([("company_id", "=", company.id)], limit=1)
    fac.write({"name": "Factory: Moulding Plant", "code": "FAC"})
    alp = WH.search([("code", "=", "ALP"), ("company_id", "=", company.id)], limit=1) or WH.create({
        "name": "Alipur Assembly Unit (Offline Stock)", "code": "ALP", "company_id": company.id})
    onl = WH.search([("code", "=", "ONL"), ("company_id", "=", company.id)], limit=1) or WH.create({
        "name": "Online Stock (Amazon, Flipkart)", "code": "ONL", "company_id": company.id})
    Loc = env["stock.location"]
    for wh, names in ((fac, ["Granule Store", "Moulded Parts Store", "Grinder and Regrind"]),
                      (alp, ["Bought-out Parts Store", "Bulk Dispatch Stock", "Single-Piece Packed"])):
        for name in names:
            if not Loc.search_count([("name", "=", name), ("location_id", "=", wh.lot_stock_id.id)]):
                Loc.create({"name": name, "location_id": wh.lot_stock_id.id, "usage": "internal"})
    PT = env["stock.picking.type"]
    for wh, name, code, src, dest in (
            (fac, "Moulded Parts to Alipur", "FAC/ALP", fac.lot_stock_id, alp.lot_stock_id),
            (alp, "Stock to Online Warehouse", "ALP/ONL", alp.lot_stock_id, onl.lot_stock_id)):
        if not PT.search_count([("name", "=", name), ("warehouse_id", "=", wh.id)]):
            PT.create(_vals(PT, {
                "name": name, "code": "internal", "sequence_code": code, "warehouse_id": wh.id,
                "default_location_src_id": src.id, "default_location_dest_id": dest.id,
                "company_id": company.id,
            }))
    return {"FAC": fac, "ALP": alp, "ONL": onl}


def _work_centres(env, whs):
    WC = env["mrp.workcenter"]
    for name, code, cost in (
            ("Injection Moulding Machine 1 (250 T)", "IMM1", 650),
            ("Injection Moulding Machine 2 (450 T)", "IMM2", 900),
            ("Injection Moulding Machine 3 (650 T)", "IMM3", 1200),
            ("Grinder", "GRD", 150),
            ("Alipur: Body and Wiring Line", "ALP-ASM", 400),
            ("Alipur: Battery Fitting", "ALP-BAT", 250),
            ("Alipur: Inspection and Test Ride", "ALP-QC", 250),
            ("Alipur: Packing", "ALP-PCK", 180)):
        if not WC.search_count([("code", "=", code)]):
            WC.create({"name": name, "code": code, "costs_hour": cost})


def _payment_terms(env):
    PT = env["account.payment.term"]
    for name, lines in (
            ("100% Advance", [(100.0, 0)]),
            ("30% Advance, 70% in 15 Days", [(30.0, 0), (70.0, 15)])):
        if not PT.search_count([("name", "=", name)]):
            PT.create({"name": name, "line_ids": [
                Command.create({"value": "percent", "value_amount": pct, "delay_type": "days_after", "nb_days": days})
                for pct, days in lines]})


USERS = [
    # key, name, login, job, groups, default warehouse
    ("director", "M Nijhawan", "m.nijhawan", "Director",
     ["base.group_system", "sales_team.group_sale_manager", "stock.group_stock_manager", "mrp.group_mrp_manager",
      "purchase.group_purchase_manager", "account.group_account_manager", "fleet.fleet_group_manager",
      "littlerides_erp.group_lr_stock_checker", "littlerides_erp.group_lr_voucher_checker"], "ALP"),
    ("pranav", "Pranav Nijhawan", "pranav", "Factory and Production Head",
     ["mrp.group_mrp_manager", "stock.group_stock_manager", "purchase.group_purchase_manager",
      "sales_team.group_sale_salesman_all_leads", "fleet.fleet_group_user",
      "littlerides_erp.group_lr_stock_checker"], "FAC"),
    ("tanav", "Tanav Nijhawan", "tanav", "Sales and Shop Head",
     ["sales_team.group_sale_manager", "stock.group_stock_user", "purchase.group_purchase_user",
      "account.group_account_invoice", "mrp.group_mrp_user", "fleet.fleet_group_user",
      "littlerides_erp.group_lr_stock_checker", "littlerides_erp.group_lr_voucher_checker"], "ALP"),
    ("alipur", "Alipur Assembly In-charge", "alipur", "Assembly In-charge, Alipur",
     ["mrp.group_mrp_user", "littlerides_erp.group_lr_stock_maker"], "ALP"),
    ("moulding", "Moulding Supervisor", "moulding", "Moulding Supervisor",
     ["mrp.group_mrp_user", "littlerides_erp.group_lr_stock_maker"], "FAC"),
    ("store", "Factory Store Keeper", "store", "Store Keeper",
     ["purchase.group_purchase_user", "littlerides_erp.group_lr_stock_maker"], "FAC"),
    ("shop", "Shop Sales Executive", "shop.sales", "Sales Executive",
     ["sales_team.group_sale_salesman", "littlerides_erp.group_lr_own_customers"], "ALP"),
    ("online", "Online Orders Executive", "online", "Amazon and Flipkart Orders",
     ["sales_team.group_sale_salesman_all_leads", "littlerides_erp.group_lr_stock_maker"], "ONL"),
    ("dispatch", "Dispatch and Fleet In-charge", "dispatch", "Dispatch In-charge",
     ["littlerides_erp.group_lr_stock_maker", "fleet.fleet_group_user"], "ALP"),
    ("accounts", "Accounts Executive", "accounts", "Accounts Executive",
     ["account.group_account_invoice", "purchase.group_purchase_user", "stock.group_stock_user"], "ALP"),
]


def _users(env, whs):
    Users = env["res.users"].with_context(no_reset_password=True)
    base_user = env.ref("base.group_user")
    users = {}
    for key, name, login, job, group_xmlids, wh_code in USERS:
        user = Users.with_context(active_test=False).search([("login", "=", login)], limit=1)
        groups = base_user
        for xmlid in group_xmlids:
            group = env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups |= group
        if not user:
            user = Users.create({
                "name": name, "login": login, "password": DEFAULT_PASSWORD,
                "group_ids": [Command.set(groups.ids)],
            })
        else:
            user.group_ids = [Command.link(g.id) for g in groups]
        user.partner_id.write({"function": job, "tz": "Asia/Kolkata"})
        if "property_warehouse_id" in user._fields:
            user.with_company(env.company).property_warehouse_id = whs[wh_code].id
        users[key] = user
    return users


def _teams(env, users):
    Team = env["crm.team"]
    offline = Team.search([], limit=1)
    offline.write({"name": "Offline: Shop and Dealers", "user_id": users["tanav"].id,
                   "member_ids": [Command.set((users["tanav"] | users["director"] | users["shop"]).ids)]})
    if not Team.search_count([("name", "=", "Online: Amazon and Flipkart")]):
        Team.create({"name": "Online: Amazon and Flipkart", "user_id": users["online"].id,
                     "member_ids": [Command.set(users["online"].ids)]})
