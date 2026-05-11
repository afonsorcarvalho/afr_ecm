from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


CONSENSUS = [
    ("any", "Qualquer 1 (basta 1 aprovar)"),
    ("all", "Todos (consenso unânime)"),
]


class AfrEcmApprovalLevel(models.Model):
    _name = "afr.ecm.approval.level"
    _description = "Nível de Aprovação ECM"
    _order = "document_type_id, sequence, id"

    document_type_id = fields.Many2one(
        "afr.ecm.document.type",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    group_id = fields.Many2one("res.groups", string="Grupo Aprovador")
    user_id = fields.Many2one("res.users", string="Usuário Aprovador")
    consensus = fields.Selection(
        CONSENSUS,
        default="any",
        required=True,
        help="Quando o nível tem múltiplos aprovadores possíveis (grupo), define se basta 1 ou se todos precisam aprovar.",
    )
    active = fields.Boolean(default=True)

    @api.constrains("group_id", "user_id", "name")
    def _check_approver_set(self):
        for rec in self:
            if not rec.group_id and not rec.user_id:
                raise ValidationError(
                    _("Nível '%s': defina pelo menos um Grupo ou Usuário aprovador.") % rec.name
                )

    def _expected_approver_users(self):
        """Conjunto vivo de res.users esperados aprovar este nível.
        Avaliação live: mudanças de membership de grupo refletem imediato.
        """
        self.ensure_one()
        users = self.env["res.users"]
        if self.user_id:
            users |= self.user_id
        if self.group_id:
            users |= self.group_id.users
        return users.filtered(lambda u: u.active)

    def _can_user_approve(self, user):
        self.ensure_one()
        return user in self._expected_approver_users()
