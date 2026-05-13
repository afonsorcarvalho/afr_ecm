# -*- coding: utf-8 -*-
"""CAPA (Corrective/Preventive Action) workflow model — afr.ecm.capa.

State machine for timed verification of effectiveness aligned to ISO 9001
cl. 10.2 / ISO 13485 (CAPA process):

    draft -> analysis -> plan -> implementation
          -> verify_30d -> verify_60d -> verify_90d
          -> closed_effective / closed_ineffective / reopened

Verification dates (30/60/90d) are computed off implementation_date. A cron
posts escalation activities for overdue verifications.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


CAPA_TYPE = [
    ("corrective", "Corretiva"),
    ("preventive", "Preventiva"),
]

CAPA_STATES = [
    ("draft", "Rascunho"),
    ("analysis", "Análise"),
    ("plan", "Plano de Ação"),
    ("implementation", "Implementação"),
    ("verify_30d", "Verificação 30d"),
    ("verify_60d", "Verificação 60d"),
    ("verify_90d", "Verificação 90d"),
    ("closed_effective", "Encerrada — Eficaz"),
    ("closed_ineffective", "Encerrada — Ineficaz"),
    ("reopened", "Reaberta"),
]

CLOSURE_DECISION = [
    ("effective", "Eficaz"),
    ("ineffective", "Ineficaz"),
    ("reopen", "Reabrir"),
]


class AfrEcmCapa(models.Model):
    _name = "afr.ecm.capa"
    _description = "CAPA (Ação Corretiva/Preventiva) — SGQ"
    _inherit = ["mail.thread", "mail.activity.mixin", "afr.ecm.audit.mixin"]
    _order = "create_date desc, id desc"
    _rec_name = "name"

    # ---------- identificação ----------
    name = fields.Char(
        string="Código",
        required=True,
        copy=False,
        readonly=True,
        index=True,
        default=lambda self: _("Novo"),
        tracking=True,
    )
    title = fields.Char(string="Título", required=True, tracking=True)
    description = fields.Html(string="Descrição")

    nc_id = fields.Many2one(
        "afr.ecm.nc",
        string="NC de Origem",
        ondelete="set null",
        tracking=True,
    )
    type = fields.Selection(
        CAPA_TYPE, string="Tipo", default="corrective", required=True, tracking=True
    )
    responsible_id = fields.Many2one(
        "res.users", string="Responsável", tracking=True
    )

    state = fields.Selection(
        CAPA_STATES, string="Estado", default="draft", required=True, copy=False, tracking=True
    )

    # ---------- análise e plano ----------
    risk_analysis = fields.Html(string="Análise de Risco")
    action_plan = fields.Html(string="Plano de Ação")

    # ---------- implementação ----------
    implementation_date = fields.Date(string="Data de Implementação", tracking=True)

    # ---------- verificações 30/60/90 dias ----------
    verify_30d_due_date = fields.Date(
        string="Vencimento Verificação 30d",
        compute="_compute_verify_due_dates",
        store=True,
    )
    verify_30d_date = fields.Date(string="Data Verificação 30d", tracking=True)
    verify_30d_result = fields.Html(string="Resultado Verificação 30d")
    verify_30d_effective = fields.Boolean(string="30d Eficaz?", tracking=True)

    verify_60d_due_date = fields.Date(
        string="Vencimento Verificação 60d",
        compute="_compute_verify_due_dates",
        store=True,
    )
    verify_60d_date = fields.Date(string="Data Verificação 60d", tracking=True)
    verify_60d_result = fields.Html(string="Resultado Verificação 60d")
    verify_60d_effective = fields.Boolean(string="60d Eficaz?", tracking=True)

    verify_90d_due_date = fields.Date(
        string="Vencimento Verificação 90d",
        compute="_compute_verify_due_dates",
        store=True,
    )
    verify_90d_date = fields.Date(string="Data Verificação 90d", tracking=True)
    verify_90d_result = fields.Html(string="Resultado Verificação 90d")
    verify_90d_effective = fields.Boolean(string="90d Eficaz?", tracking=True)

    # ---------- encerramento ----------
    closure_decision = fields.Selection(
        CLOSURE_DECISION, string="Decisão de Encerramento", tracking=True
    )
    closure_date = fields.Date(string="Data de Encerramento", tracking=True)

    # ---------- evidências / pasta ----------
    attachment_ids = fields.Many2many(
        "dms.file",
        relation="afr_ecm_capa_dms_file_rel",
        column1="capa_id",
        column2="file_id",
        string="Evidências (ECM)",
    )
    directory_id = fields.Many2one(
        "dms.directory",
        string="Pasta ECM",
        default=lambda self: self._default_directory_id(),
    )

    # ---------- defaults ----------
    @api.model
    def _default_directory_id(self):
        doc_type = self.env["afr.ecm.document.type"].search(
            [("code", "=", "SGQ_CAPA")], limit=1
        )
        if doc_type and doc_type.default_directory_id:
            return doc_type.default_directory_id.id
        return False

    # ---------- computed: due dates ----------
    @api.depends("implementation_date")
    def _compute_verify_due_dates(self):
        for rec in self:
            if rec.implementation_date:
                rec.verify_30d_due_date = rec.implementation_date + timedelta(days=30)
                rec.verify_60d_due_date = rec.implementation_date + timedelta(days=60)
                rec.verify_90d_due_date = rec.implementation_date + timedelta(days=90)
            else:
                rec.verify_30d_due_date = False
                rec.verify_60d_due_date = False
                rec.verify_90d_due_date = False

    # ---------- create override (sequence) ----------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") in (_("Novo"), "Novo", "New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("afr.ecm.capa")
                    or _("Novo")
                )
        return super().create(vals_list)

    # ---------- helpers ----------
    def _schedule_activity(self, summary, note, days=0, deadline=None):
        self.ensure_one()
        if deadline is None:
            deadline = fields.Date.context_today(self) + timedelta(days=days)
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            date_deadline=deadline,
            summary=summary,
            note=note,
            user_id=(self.responsible_id or self.env.user).id,
        )

    def _close_open_activities(self):
        for rec in self:
            if rec.activity_ids:
                rec.activity_ids.action_feedback(
                    feedback=_("Encerrada por mudança de estado da CAPA.")
                )

    # ---------- guards ----------
    def _check_can_start_analysis(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(
                    _("Só é possível iniciar Análise a partir de Rascunho.")
                )

    def _check_can_approve_plan(self):
        for rec in self:
            if rec.state != "analysis":
                raise UserError(
                    _("Só é possível aprovar Plano a partir de Análise.")
                )
            if not rec.risk_analysis:
                raise UserError(
                    _("Preencha a Análise de Risco antes de aprovar o Plano.")
                )

    def _check_can_implement(self):
        for rec in self:
            if rec.state != "plan":
                raise UserError(
                    _("CAPA precisa estar em Plano de Ação para implementar.")
                )
            if not rec.action_plan:
                raise UserError(
                    _("Preencha o Plano de Ação antes de marcar como implementado.")
                )

    # ---------- actions ----------
    def action_start_analysis(self):
        self._check_can_start_analysis()
        for rec in self:
            rec.state = "analysis"
            rec._schedule_activity(
                summary=_("CAPA %s — Análise de risco e causa") % rec.name,
                note=_("Conduza análise de risco. Prazo: 7 dias."),
                days=7,
            )
        return True

    def action_approve_plan(self):
        self._check_can_approve_plan()
        for rec in self:
            rec._close_open_activities()
            rec.state = "plan"
            rec._schedule_activity(
                summary=_("CAPA %s — Aprovar e detalhar Plano de Ação") % rec.name,
                note=_("Detalhe e aprove o plano de ação."),
                days=7,
            )
        return True

    def action_mark_implemented(self):
        self._check_can_implement()
        for rec in self:
            rec._close_open_activities()
            rec.implementation_date = fields.Date.context_today(rec)
            rec.state = "verify_30d"
            rec._schedule_activity(
                summary=_("CAPA %s — Verificação de eficácia 30d") % rec.name,
                note=_(
                    "Verificar eficácia 30 dias após implementação. "
                    "Vencimento: %s"
                ) % (rec.verify_30d_due_date or "-"),
                deadline=rec.verify_30d_due_date,
            )
        return True

    def _do_verify(self, stage):
        """stage in (30, 60, 90). Aplica verificação e transita."""
        result_field = "verify_%dd_result" % stage
        date_field = "verify_%dd_date" % stage
        effective_field = "verify_%dd_effective" % stage
        for rec in self:
            if rec.state != "verify_%dd" % stage:
                raise UserError(
                    _("CAPA precisa estar em Verificação %dd.") % stage
                )
            if not rec[result_field]:
                raise UserError(
                    _("Preencha o resultado da Verificação %dd.") % stage
                )
            if not rec[date_field]:
                rec[date_field] = fields.Date.context_today(rec)
            rec._close_open_activities()
            if not rec[effective_field]:
                # ineficaz → reopened
                rec.state = "reopened"
                rec._schedule_activity(
                    summary=_("CAPA %s — Reaberta (verificação %dd ineficaz)")
                    % (rec.name, stage),
                    note=_(
                        "Verificação %dd não foi eficaz. Reabra análise/plano "
                        "ou justifique encerramento como ineficaz."
                    ) % stage,
                    days=2,
                )
                continue
            # eficaz → próxima etapa
            if stage == 30:
                rec.state = "verify_60d"
                rec._schedule_activity(
                    summary=_("CAPA %s — Verificação 60d") % rec.name,
                    note=_("Verificar eficácia 60 dias após implementação."),
                    deadline=rec.verify_60d_due_date,
                )
            elif stage == 60:
                rec.state = "verify_90d"
                rec._schedule_activity(
                    summary=_("CAPA %s — Verificação 90d") % rec.name,
                    note=_("Verificar eficácia 90 dias após implementação."),
                    deadline=rec.verify_90d_due_date,
                )
            else:  # 90
                rec._schedule_activity(
                    summary=_("CAPA %s — Decidir encerramento como Eficaz") % rec.name,
                    note=_(
                        "Verificação 90d eficaz. Encerre como Eficaz para "
                        "concluir o ciclo CAPA."
                    ),
                    days=2,
                )
        return True

    def action_verify_30d(self):
        return self._do_verify(30)

    def action_verify_60d(self):
        return self._do_verify(60)

    def action_verify_90d(self):
        return self._do_verify(90)

    def action_close_effective(self):
        for rec in self:
            if rec.state not in ("verify_60d", "verify_90d"):
                raise UserError(
                    _(
                        "Encerramento como Eficaz só após verificação 60d ou 90d."
                    )
                )
            rec._close_open_activities()
            rec.write({
                "state": "closed_effective",
                "closure_decision": "effective",
                "closure_date": fields.Date.context_today(rec),
            })
        return True

    def action_close_ineffective(self):
        for rec in self:
            if rec.state not in (
                "verify_30d", "verify_60d", "verify_90d", "reopened"
            ):
                raise UserError(
                    _("Encerramento como Ineficaz requer estágio de verificação ou reaberta.")
                )
            rec._close_open_activities()
            rec.write({
                "state": "closed_ineffective",
                "closure_decision": "ineffective",
                "closure_date": fields.Date.context_today(rec),
            })
        return True

    def action_reopen(self):
        if not self.env.user.has_group("afr_ecm.group_ecm_manager"):
            raise UserError(_("Apenas gestores ECM podem reabrir uma CAPA."))
        for rec in self:
            rec._close_open_activities()
            rec.write({
                "state": "reopened",
                "closure_decision": "reopen",
                "closure_date": False,
            })
        return True

    # ---------- cron ----------
    @api.model
    def _cron_capa_verification_reminders(self):
        """Encontra CAPAs em verify_NNd com vencimento já passado e ainda sem
        data de verificação registrada — posta atividade de escalação."""
        today = fields.Date.context_today(self)
        stages = [
            ("verify_30d", "verify_30d_due_date", "verify_30d_date"),
            ("verify_60d", "verify_60d_due_date", "verify_60d_date"),
            ("verify_90d", "verify_90d_due_date", "verify_90d_date"),
        ]
        count = 0
        for state, due_f, done_f in stages:
            overdue = self.search([
                ("state", "=", state),
                (due_f, "!=", False),
                (due_f, "<=", today),
                (done_f, "=", False),
            ])
            for rec in overdue:
                already = rec.activity_ids.filtered(
                    lambda a, s=state: a.summary and (
                        "Escalação" in (a.summary or "")
                        and rec.name in (a.summary or "")
                        and s in (a.summary or "")
                    )
                )
                if already:
                    continue
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=today,
                    summary=_("CAPA %s — Escalação: %s vencida") % (rec.name, state),
                    note=_(
                        "Verificação %(s)s da CAPA %(n)s está vencida desde "
                        "%(d)s. Registre o resultado ou justifique atraso."
                    ) % {"s": state, "n": rec.name, "d": rec[due_f]},
                    user_id=(rec.responsible_id or self.env.user).id,
                )
                count += 1
        return count

    # ---------- constraints ----------
    @api.constrains("state", "closure_decision")
    def _check_closure_consistency(self):
        for rec in self:
            if rec.state == "closed_effective" and rec.closure_decision != "effective":
                raise ValidationError(
                    _("CAPA encerrada como Eficaz exige decisão = Eficaz.")
                )
            if rec.state == "closed_ineffective" and rec.closure_decision != "ineffective":
                raise ValidationError(
                    _("CAPA encerrada como Ineficaz exige decisão = Ineficaz.")
                )
