from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    def _lr_is_manual_voucher(self):
        self.ensure_one()
        return (self.move_type == "entry" and self.journal_id.type == "general"
                and not self.origin_payment_id and not self.statement_line_id)

    def action_post(self):
        if (not self.env.su
                and not self.env.user.has_group("littlerides_erp.group_lr_voucher_checker")
                and any(move._lr_is_manual_voucher() for move in self)):
            raise UserError(_(
                "Manual journal entries are posted by a voucher checker. "
                "Save it as draft; the checker will verify the amount and post it."))
        return super().action_post()
