"""Enterprise base configuration: complaints team and Enterprise access for the Little Rides users.
Each block runs in its own savepoint, so a field renamed in a later Odoo 19 build skips that block
with a log line instead of stopping the install."""
import logging

from odoo import Command

_logger = logging.getLogger(__name__)

GROUPS = {
    # login: Enterprise groups
    "m.nijhawan": ["quality.group_quality_manager", "helpdesk.group_helpdesk_manager", "account.group_account_manager"],
    "pranav": ["quality.group_quality_manager", "helpdesk.group_helpdesk_user"],
    "tanav": ["helpdesk.group_helpdesk_manager", "quality.group_quality_user", "account.group_account_user"],
    "alipur": ["quality.group_quality_user"],
    "moulding": ["quality.group_quality_user"],
    "shop.sales": ["helpdesk.group_helpdesk_user"],
    "online": ["helpdesk.group_helpdesk_user"],
    "dispatch": ["helpdesk.group_helpdesk_user"],
    "accounts": ["account.group_account_user"],
}


def _safe(env, label, fn):
    try:
        with env.cr.savepoint():
            fn()
    except Exception as e:  # noqa: BLE001
        _logger.warning("Little Rides enterprise setup: %s skipped (%s)", label, e)


def _vals(model, vals):
    return {k: v for k, v in vals.items() if k in model._fields}


def post_init_hook(env):
    env = env(context=dict(env.context, tracking_disable=True, mail_create_nolog=True))
    _safe(env, "user access", lambda: _groups(env))
    _safe(env, "complaints team", lambda: _helpdesk_team(env))
    _logger.info("Little Rides enterprise configuration done")


def _groups(env):
    for login, xmlids in GROUPS.items():
        user = env["res.users"].search([("login", "=", login)], limit=1)
        if not user:
            continue
        for xmlid in xmlids:
            group = env.ref(xmlid, raise_if_not_found=False)
            if group:
                user.group_ids = [Command.link(group.id)]


def _helpdesk_team(env):
    Team = env["helpdesk.team"]
    if Team.search_count([("name", "=", "Dealer and Customer Complaints")]):
        return
    members = env["res.users"].search([("login", "in", ["tanav", "shop.sales", "online", "dispatch"])])
    Team.create(_vals(Team, {
        "name": "Dealer and Customer Complaints",
        "member_ids": [Command.set(members.ids)],
        "use_sla": True,
    }))
