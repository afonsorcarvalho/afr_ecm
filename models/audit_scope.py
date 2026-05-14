"""afr.ecm.audit.scope — Escopo de Auditoria Externa.

Registra quais pastas (`dms.directory`) um grupo de auditores externos pode
visualizar durante um engajamento de auditoria com prazo definido.

O campo computado `res.users.audit_scope_directory_ids` é lido diretamente
pela record rule `rule_ecm_auditor_externo_readonly` para filtrar arquivos
(`dms.file`) acessíveis ao auditor.

Ciclo de vida:
  - Gestor ECM (group_ecm_manager) cria o escopo com auditores + pastas + datas.
  - Cron diário `_cron_expire_audit_scopes()` arquiva escopos vencidos (end_date < hoje).
  - Auditores perdem acesso automaticamente no dia seguinte ao fim do engajamento.

Nota de modelagem: este modelo não é instalado automaticamente pelo
`__manifest__.py`. A integração é especificada no arquivo de patch
`MANIFEST_PATCH_F4_3_9.md` e aplicada na sprint seguinte (F4.3.10).
"""
import logging
from datetime import date

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AfrEcmAuditScope(models.Model):
    """Escopo de auditoria externa: pastas + auditores + período."""

    _name = "afr.ecm.audit.scope"
    _description = "ECM — Escopo de Auditoria Externa"
    _inherit = ["afr.ecm.audit.mixin"]
    _order = "start_date desc, name"

    # ------------------------------------------------------------------
    # Campos
    # ------------------------------------------------------------------

    name = fields.Char(
        string="Nome do Engajamento",
        required=True,
        help='Exemplo: "Auditoria ISO 9001 — 2026-05"',
    )

    auditor_user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="afr_ecm_audit_scope_user_rel",
        column1="scope_id",
        column2="user_id",
        string="Auditores",
        help="Usuários com perfil Auditor Externo que participam deste engajamento.",
        domain=[("share", "=", False)],
    )

    directory_ids = fields.Many2many(
        comodel_name="dms.directory",
        relation="afr_ecm_audit_scope_dir_rel",
        column1="scope_id",
        column2="directory_id",
        string="Pastas em Escopo",
        help="Pastas do ECM que os auditores podem visualizar durante este engajamento.",
    )

    start_date = fields.Date(
        string="Início",
        required=True,
        default=fields.Date.today,
    )

    end_date = fields.Date(
        string="Fim",
        required=True,
        help="O cron diário arquivará este escopo automaticamente após esta data.",
    )

    active = fields.Boolean(
        default=True,
        help="Escopo inativo → auditores perdem acesso imediatamente. "
        "O cron diário desativa escopos com end_date no passado.",
    )

    notes = fields.Text(
        string="Observações",
        help="Contexto adicional: norma auditada, cliente, auditor líder, etc.",
    )

    # Campo computado para exibição conveniente na view
    directory_count = fields.Integer(
        string="Pastas",
        compute="_compute_directory_count",
    )
    auditor_count = fields.Integer(
        string="Auditores",
        compute="_compute_auditor_count",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("directory_ids")
    def _compute_directory_count(self):
        for rec in self:
            rec.directory_count = len(rec.directory_ids)

    @api.depends("auditor_user_ids")
    def _compute_auditor_count(self):
        for rec in self:
            rec.auditor_count = len(rec.auditor_user_ids)

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_expire_audit_scopes(self):
        """Arquiva escopos cujo end_date é anterior a hoje + sync access.group."""
        today = date.today()
        expired = self.search([
            ("active", "=", True),
            ("end_date", "<", today),
        ])
        if expired:
            expired.with_context(audit_skip_write=False).write({"active": False})
            _logger.info(
                "AFR ECM: %d escopo(s) de auditoria expirado(s) arquivados: %s",
                len(expired),
                expired.mapped("name"),
            )
        # Sync sempre (mesmo sem expirados) p/ garantir estado correto
        self._sync_auditor_dms_access_group()

    @api.model
    def _sync_auditor_dms_access_group(self):
        """Recalcula directory_ids do dms.access.group Auditor_Externo a partir
        dos escopos ativos no momento. Chamado após create/write/expire.
        """
        AccessGroup = self.env["dms.access.group"].sudo()
        group = AccessGroup.search([("name", "=", "Auditor_Externo")], limit=1)
        if not group:
            _logger.warning("Auditor_Externo dms.access.group não encontrado — skip sync.")
            return
        today = date.today()
        active = self.sudo().search([
            ("active", "=", True),
            ("start_date", "<=", today),
            ("end_date", ">=", today),
        ])
        dirs = active.mapped("directory_ids")
        if group.directory_ids.ids != dirs.ids:
            group.write({"directory_ids": [(6, 0, dirs.ids)]})
            _logger.info(
                "AFR ECM: Auditor_Externo dms.access.group sincronizado com %d pasta(s): %s",
                len(dirs), dirs.mapped("complete_name"),
            )

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._sync_auditor_dms_access_group()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ("directory_ids", "auditor_user_ids", "active", "start_date", "end_date")):
            self._sync_auditor_dms_access_group()
        return res

    def unlink(self):
        res = super().unlink()
        self.env["afr.ecm.audit.scope"]._sync_auditor_dms_access_group()
        return res

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_audit_scope_active(self):
        """Retorna True se este escopo está ativo e dentro do período.

        Usado em testes e em lógica de negócio para verificar validade
        sem depender de contexto de data do servidor.
        """
        self.ensure_one()
        today = date.today()
        return self.active and self.start_date <= today <= self.end_date

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for rec in self:
            if rec.end_date < rec.start_date:
                from odoo.exceptions import ValidationError
                raise ValidationError(
                    f"O fim ({rec.end_date}) não pode ser anterior ao início ({rec.start_date})."
                )


class ResUsersAuditScope(models.Model):
    """Extensão de res.users para expor diretórios de auditoria.

    O campo `audit_scope_directory_ids` é lido diretamente pela record rule
    `rule_ecm_auditor_externo_readonly` no domínio:
        [('directory_id', 'in', user.audit_scope_directory_ids.ids)]

    O campo é computado sem store para garantir que sempre reflita o estado
    atual dos escopos ativos.
    """

    _inherit = "res.users"

    audit_scope_directory_ids = fields.Many2many(
        comodel_name="dms.directory",
        string="Pastas de Auditoria (escopos ativos)",
        compute="_compute_audit_scope_directory_ids",
        help="Agrega pastas de todos os escopos de auditoria ativos aos quais "
        "este usuário pertence. Usado pela record rule de auditor externo.",
    )

    @api.depends("groups_id")  # recalcula ao mudar grupos (e portanto escopos)
    def _compute_audit_scope_directory_ids(self):
        """Retorna pastas de escopos ativos onde o usuário é auditor.

        Usa sudo() para cruzar dados de `afr.ecm.audit.scope` sem depender
        dos direitos do usuário atual (a record rule já filtra o acesso a files).
        """
        AuditScope = self.env["afr.ecm.audit.scope"].sudo()
        today = date.today()
        for user in self:
            active_scopes = AuditScope.search([
                ("active", "=", True),
                ("start_date", "<=", today),
                ("end_date", ">=", today),
                ("auditor_user_ids", "in", user.ids),
            ])
            dirs = active_scopes.mapped("directory_ids")
            user.audit_scope_directory_ids = dirs
