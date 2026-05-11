from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .document_type import CONFIDENTIALITY


APPROVAL_STATE = [
    ("draft", "Rascunho"),
    ("pending", "Em Aprovação"),
    ("approved", "Aprovado"),
    ("rejected", "Rejeitado"),
]

# Campos cuja escrita NÃO é considerada alteração de conteúdo
# (logging, chatter, activities, audit, próprio workflow).
_APPROVAL_META_FIELDS = frozenset(
    [
        "approval_state",
        "current_level_id",
        "approval_action_ids",
        "message_ids",
        "message_follower_ids",
        "message_partner_ids",
        "message_main_attachment_id",
        "message_is_follower",
        "activity_ids",
        "activity_state",
        "activity_user_id",
        "activity_type_id",
        "activity_date_deadline",
        "activity_summary",
        "activity_exception_decoration",
        "activity_exception_icon",
        "write_date",
        "write_uid",
    ]
)


class DmsFile(models.Model):
    _name = "dms.file"
    _inherit = ["dms.file", "afr.ecm.audit.mixin"]

    document_type_id = fields.Many2one(
        "afr.ecm.document.type",
        string="Tipo de Documento",
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    confidentiality = fields.Selection(
        CONFIDENTIALITY,
        default="internal",
        required=True,
        index=True,
        tracking=True,
    )
    metadata_value_ids = fields.One2many(
        "afr.ecm.metadata.value",
        "file_id",
        string="Metadados",
    )
    physical_location_id = fields.Many2one(
        "afr.ecm.physical.location",
        string="Localização Física",
        ondelete="set null",
        index=True,
        tracking=True,
    )
    expiration_date = fields.Date(
        string="Vencimento",
        index=True,
        help="Usado em fases futuras para alertas e expiração automática.",
    )

    approval_state = fields.Selection(
        APPROVAL_STATE,
        string="Status Aprovação",
        index=True,
        tracking=True,
        copy=False,
    )
    current_level_id = fields.Many2one(
        "afr.ecm.approval.level",
        string="Nível Atual",
        copy=False,
        ondelete="set null",
    )
    approval_action_ids = fields.One2many(
        "afr.ecm.approval.action",
        "file_id",
        string="Histórico de Aprovações",
    )

    # ------------------------------------------------------------------
    # Onchange / overrides básicos
    # ------------------------------------------------------------------
    @api.onchange("document_type_id")
    def _onchange_document_type_id(self):
        for rec in self:
            if rec.document_type_id:
                if not rec.confidentiality or rec.confidentiality == "internal":
                    rec.confidentiality = rec.document_type_id.default_confidentiality
                if rec.document_type_id.default_directory_id and not rec.directory_id:
                    rec.directory_id = rec.document_type_id.default_directory_id

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if (
                rec.document_type_id
                and rec.document_type_id.requires_approval
                and not rec.approval_state
            ):
                rec.approval_state = "draft"
        return records

    def write(self, vals):
        # Bloqueia edição de conteúdo em arquivos approved.
        # Bypass apenas via sudo() (workflow interno) ou superuser.
        if self and not self.env.su:
            forbidden = set(vals) - _APPROVAL_META_FIELDS
            if forbidden:
                approved = self.filtered(lambda r: r.approval_state == "approved")
                if approved:
                    raise UserError(
                        _(
                            "Arquivo aprovado é imutável. "
                            "Clique em 'Reabrir' para voltar ao rascunho antes de editar. "
                            "Campos bloqueados: %s"
                        )
                        % ", ".join(sorted(forbidden))
                    )
        return super().write(vals)

    def _audit_log_view(self):
        Log = self.env["afr.ecm.audit.log"].sudo()
        for rec in self:
            Log.log("view", rec)

    # ------------------------------------------------------------------
    # Workflow de aprovação
    # ------------------------------------------------------------------
    def _approval_levels(self):
        self.ensure_one()
        return self.document_type_id.approval_level_ids.filtered("active").sorted(
            key=lambda l: (l.sequence, l.id)
        )

    def _approval_first_level(self):
        levels = self._approval_levels()
        return levels[:1]

    def _approval_next_level(self):
        self.ensure_one()
        levels = list(self._approval_levels())
        if not self.current_level_id or self.current_level_id not in levels:
            return self.env["afr.ecm.approval.level"]
        idx = levels.index(self.current_level_id)
        if idx + 1 < len(levels):
            return levels[idx + 1]
        return self.env["afr.ecm.approval.level"]

    def _approval_consensus_satisfied(self):
        """True se o nível atual já tem aprovações suficientes (any/all)."""
        self.ensure_one()
        level = self.current_level_id
        if not level:
            return False
        approvers = self.approval_action_ids.filtered(
            lambda a: a.action == "approve" and a.level_id == level
        ).user_id
        if level.consensus == "any":
            return bool(approvers)
        # all: todos os esperados (vivos no momento) devem ter aprovado
        expected = level._expected_approver_users()
        return bool(expected) and (expected <= approvers)

    def _approval_log(self, action, level=None, note=None):
        self.ensure_one()
        # captura uid ANTES do sudo (sudo() troca env.uid para SUPERUSER)
        uid = self.env.uid
        return self.env["afr.ecm.approval.action"].sudo().create(
            {
                "file_id": self.id,
                "level_id": (level or self.current_level_id).id or False,
                "action": action,
                "user_id": uid,
                "note": note or False,
            }
        )

    _APPROVAL_ACTIVITY_XMLID = "afr_ecm.mail_activity_data_approval"

    def _approval_create_activity(self):
        """Cria mail.activity 'Aprovar' para os usuários esperados do nível atual."""
        self.ensure_one()
        if not self.current_level_id:
            return
        act_type = self.env.ref(self._APPROVAL_ACTIVITY_XMLID, raise_if_not_found=False)
        if not act_type:
            return
        users = self.current_level_id._expected_approver_users()
        for user in users:
            self.activity_schedule(
                self._APPROVAL_ACTIVITY_XMLID,
                user_id=user.id,
                summary=_("Aprovar documento: %s") % (self.name or ""),
                note=_("Nível: %s") % (self.current_level_id.name or ""),
            )

    def _approval_clear_activities(self):
        """Remove activities pendentes de aprovação para todos usuários."""
        self.ensure_one()
        act_type = self.env.ref(self._APPROVAL_ACTIVITY_XMLID, raise_if_not_found=False)
        if not act_type:
            return
        self.activity_ids.filtered(lambda a: a.activity_type_id == act_type).unlink()

    # --- ações expostas no botão ---
    def action_submit_for_approval(self):
        for rec in self:
            if rec.approval_state not in ("draft",):
                raise UserError(_("Só é possível submeter rascunhos."))
            first = rec._approval_first_level()
            if not first:
                raise UserError(
                    _("Tipo de documento '%s' não possui níveis de aprovação configurados.")
                    % (rec.document_type_id.name or "")
                )
            rec.sudo().write(
                {
                    "approval_state": "pending",
                    "current_level_id": first.id,
                }
            )
            rec._approval_log("submit", level=first)
            rec.sudo()._approval_create_activity()
        return True

    def action_approve(self):
        for rec in self:
            if rec.approval_state != "pending":
                raise UserError(_("Documento não está em aprovação."))
            level = rec.current_level_id
            if not level:
                raise UserError(_("Sem nível de aprovação atual."))
            if not level._can_user_approve(self.env.user):
                raise UserError(
                    _("Você não está autorizado a aprovar o nível '%s'.") % level.name
                )
            # impede aprovação dupla pelo mesmo user no mesmo nível
            already = rec.approval_action_ids.filtered(
                lambda a: a.action == "approve"
                and a.level_id == level
                and a.user_id == self.env.user
            )
            if already:
                raise UserError(_("Você já aprovou este nível."))
            rec._approval_log("approve", level=level)
            # remove activity deste user
            act_type = self.env.ref(self._APPROVAL_ACTIVITY_XMLID, raise_if_not_found=False)
            if act_type:
                rec.sudo().activity_ids.filtered(
                    lambda a: a.activity_type_id == act_type
                    and a.user_id == self.env.user
                ).unlink()
            if rec._approval_consensus_satisfied():
                next_level = rec._approval_next_level()
                if next_level:
                    rec.sudo().write({"current_level_id": next_level.id})
                    rec.sudo()._approval_clear_activities()
                    rec.sudo()._approval_create_activity()
                else:
                    rec.sudo().write(
                        {
                            "approval_state": "approved",
                            "current_level_id": False,
                        }
                    )
                    rec.sudo()._approval_clear_activities()
        return True

    def action_reject(self):
        for rec in self:
            if rec.approval_state != "pending":
                raise UserError(_("Documento não está em aprovação."))
            level = rec.current_level_id
            if level and not level._can_user_approve(self.env.user):
                raise UserError(
                    _("Você não está autorizado a rejeitar o nível '%s'.") % level.name
                )
            rec._approval_log("reject", level=level)
            rec.sudo().write(
                {
                    "approval_state": "rejected",
                    "current_level_id": False,
                }
            )
            rec.sudo()._approval_clear_activities()
        return True

    def action_reopen(self):
        for rec in self:
            if rec.approval_state not in ("rejected", "approved"):
                raise UserError(_("Só rejeitados ou aprovados podem ser reabertos."))
            is_admin = self.env.user.has_group("afr_ecm.group_ecm_admin")
            is_manager = self.env.user.has_group("afr_ecm.group_ecm_manager")
            is_author = rec.create_uid == self.env.user
            if not (is_admin or is_manager or is_author):
                raise UserError(
                    _("Apenas o autor, gestor ou administrador ECM pode reabrir.")
                )
            rec._approval_log("reopen")
            rec.sudo().write(
                {
                    "approval_state": "draft",
                    "current_level_id": False,
                }
            )
            rec.sudo()._approval_clear_activities()
        return True
