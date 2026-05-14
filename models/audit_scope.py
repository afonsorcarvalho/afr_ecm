"""afr.ecm.audit.scope — Escopo de Auditoria Externa.

Registra quais pastas (`dms.directory`) um grupo de auditores externos pode
visualizar durante um engajamento de auditoria com prazo definido.

O campo computado `res.users.audit_scope_directory_ids` é lido diretamente
pelas record rules:
  - `rule_ecm_auditor_externo_readonly` (dms.file) — filtra arquivos visíveis.
  - `rule_ecm_auditor_directory_tree` (dms.directory) — permite navegar a
    árvore de pastas (escopos + parents) sem sincronização dinâmica.

Ciclo de vida:
  - Gestor ECM (group_ecm_manager) cria o escopo com auditores + pastas + datas.
  - Cron diário `_cron_expire_audit_scopes()` arquiva escopos vencidos (end_date < hoje).
  - Auditores perdem acesso automaticamente no dia seguinte ao fim do engajamento.

F4.3.10: removida a sincronização dinâmica de Auditor_Externo dms.access.group
via hooks create/write/unlink. O controle de acesso é agora inteiramente por
ir.rule (stateless, por usuário, sem race conditions de cron).
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
        """Arquiva escopos cujo end_date é anterior a hoje.

        F4.3.10: a sincronização com Auditor_Externo dms.access.group foi
        removida. O acesso é controlado inteiramente por ir.rule stateless
        via res.users.audit_scope_directory_ids (computed em tempo real).
        """
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

    O campo `audit_scope_directory_ids` é lido diretamente pelas record rules:
      - `rule_ecm_auditor_externo_readonly` (dms.file):
            [('directory_id', 'child_of', user.audit_scope_directory_ids.ids)]
      - `rule_ecm_auditor_directory_tree` (dms.directory):
            ['|', ('id', 'child_of', user.audit_scope_directory_ids.ids),
                  ('id', 'parent_of', user.audit_scope_directory_ids.ids)]

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
