from odoo import fields, models


ACTION_TYPES = [
    ("submit", "Submeteu para aprovação"),
    ("approve", "Aprovou"),
    ("reject", "Rejeitou"),
    ("reopen", "Reabriu"),
]


class AfrEcmApprovalAction(models.Model):
    _name = "afr.ecm.approval.action"
    _description = "Ação de Aprovação ECM"
    _order = "date desc, id desc"
    _rec_name = "action"

    file_id = fields.Many2one(
        "dms.file",
        required=True,
        ondelete="cascade",
        index=True,
    )
    level_id = fields.Many2one(
        "afr.ecm.approval.level",
        ondelete="set null",
        index=True,
    )
    action = fields.Selection(ACTION_TYPES, required=True)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    note = fields.Text()

