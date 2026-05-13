# -*- coding: utf-8 -*-
"""Non-Conformity (NC) workflow model — afr.ecm.nc.

State machine aligned to ISO 9001 cl. 10.2 / ISO 13485 for a CME externa.

    draft -> disposition (24h SLA: immediate correction)
          -> investigation (15-day SLA: root cause analysis)
          -> decision_capa (open CAPA or close without one)
          -> closed  OR  escalated_to_capa

Files of doc type SGQ_NC are wired to land in folder 05_Nao_Conformidades
(default_directory_id of the SGQ_NC document.type).
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


SEVERITY = [
    ("low", "Baixa"),
    ("medium", "Média"),
    ("high", "Alta"),
    ("critical", "Crítica"),
]

ORIGIN = [
    ("audit", "Auditoria"),
    ("complaint", "Reclamação"),
    ("process", "Processo Interno"),
    ("supplier", "Fornecedor"),
    ("other", "Outro"),
]

RISK = [
    ("low", "Baixo"),
    ("medium", "Médio"),
    ("high", "Alto"),
]

STATES = [
    ("draft", "Rascunho"),
    ("disposition", "Disposição (24h)"),
    ("investigation", "Investigação (15d)"),
    ("decision_capa", "Decisão CAPA"),
    ("closed", "Encerrada"),
    ("escalated_to_capa", "Escalada para CAPA"),
]

LINKED_EVENT_TYPES = [
    ("bi_positive", "BI Positivo"),
    ("recall", "Recall"),
    ("cycle_failure", "Falha de Ciclo"),
    ("complaint", "Reclamação"),
    ("audit", "Auditoria"),
    ("inspection", "Inspeção Sanitária"),
    ("other", "Outro"),
]


class AfrEcmNc(models.Model):
    _name = "afr.ecm.nc"
    _description = "Não-Conformidade (NC) — SGQ"
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

    severity = fields.Selection(
        SEVERITY, string="Severidade", default="medium", required=True, tracking=True
    )
    origin = fields.Selection(
        ORIGIN, string="Origem", default="process", required=True, tracking=True
    )
    risk_assessment = fields.Selection(
        RISK, string="Avaliação de Risco", tracking=True
    )

    originator_id = fields.Many2one(
        "res.users",
        string="Identificada por",
        default=lambda self: self.env.user,
        tracking=True,
    )
    responsible_id = fields.Many2one(
        "res.users", string="Responsável", tracking=True
    )

    state = fields.Selection(
        STATES, string="Estado", default="draft", required=True, copy=False, tracking=True
    )

    # ---------- disposição (correção imediata) ----------
    disposition_text = fields.Html(string="Disposição / Correção Imediata")
    disposition_date = fields.Datetime(string="Data da Disposição", tracking=True)

    # ---------- investigação / causa raiz ----------
    root_cause_text = fields.Html(string="Análise de Causa Raiz")
    root_cause_date = fields.Datetime(string="Data da Causa Raiz", tracking=True)

    # ---------- decisão CAPA ----------
    capa_id = fields.Many2one(
        "afr.ecm.capa",
        string="CAPA Vinculada",
        copy=False,
        tracking=True,
        ondelete="set null",
    )

    # ---------- encerramento ----------
    closure_reason = fields.Html(string="Justificativa de Encerramento")
    closure_date = fields.Datetime(string="Data de Encerramento", tracking=True)

    # ---------- evidências / pasta ECM ----------
    attachment_ids = fields.Many2many(
        "dms.file",
        relation="afr_ecm_nc_dms_file_rel",
        column1="nc_id",
        column2="file_id",
        string="Evidências (ECM)",
    )
    directory_id = fields.Many2one(
        "dms.directory",
        string="Pasta ECM",
        default=lambda self: self._default_directory_id(),
    )

    # ---------- cross-reference ----------
    linked_event_type = fields.Selection(
        LINKED_EVENT_TYPES, string="Tipo de Evento Vinculado"
    )
    linked_event_ref = fields.Char(string="Referência do Evento")

    # ---------- defaults ----------
    @api.model
    def _default_directory_id(self):
        doc_type = self.env["afr.ecm.document.type"].search(
            [("code", "=", "SGQ_NC")], limit=1
        )
        if doc_type and doc_type.default_directory_id:
            return doc_type.default_directory_id.id
        return False

    # ---------- create override (sequence) ----------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") in (_("Novo"), "Novo", "New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("afr.ecm.nc")
                    or _("Novo")
                )
        return super().create(vals_list)

    # ---------- helpers ----------
    def _schedule_activity(self, summary, note, days=0, hours=0):
        """Cria mail.activity TODO com prazo days/hours a partir de agora."""
        self.ensure_one()
        deadline = fields.Date.context_today(self) + timedelta(
            days=days + (1 if hours and hours >= 24 else 0)
        )
        # Para SLA em horas usamos date_deadline = hoje (ou +1 se >=24h);
        # o gating real é via cron / chatter visual.
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            date_deadline=deadline,
            summary=summary,
            note=note,
            user_id=(self.responsible_id or self.originator_id or self.env.user).id,
        )

    def _close_open_activities(self):
        for rec in self:
            if rec.activity_ids:
                rec.activity_ids.action_feedback(
                    feedback=_("Encerrada por mudança de estado da NC.")
                )

    # ---------- guards ----------
    def _check_can_leave_disposition(self):
        for rec in self:
            if not rec.disposition_text or not rec.disposition_date:
                raise UserError(
                    _(
                        "Preencha 'Disposição / Correção Imediata' e 'Data da "
                        "Disposição' antes de avançar para Investigação."
                    )
                )

    def _check_can_leave_investigation(self):
        for rec in self:
            if not rec.root_cause_text:
                raise UserError(
                    _(
                        "Preencha 'Análise de Causa Raiz' antes de avançar para "
                        "Decisão CAPA."
                    )
                )

    def _check_can_close(self):
        for rec in self:
            if not rec.closure_reason:
                raise UserError(
                    _("Informe a justificativa de encerramento antes de fechar.")
                )

    # ---------- actions ----------
    def action_start_disposition(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(
                    _("Só é possível iniciar Disposição a partir de Rascunho.")
                )
            rec.state = "disposition"
            rec._schedule_activity(
                summary=_("NC %s — Disposição imediata (SLA 24h)") % rec.name,
                note=_(
                    "Registre a correção imediata e a data da disposição. "
                    "SLA: 24h."
                ),
                hours=24,
            )
        return True

    def action_complete_disposition(self):
        self._check_can_leave_disposition()
        for rec in self:
            if rec.state != "disposition":
                raise UserError(
                    _("A NC precisa estar em Disposição para avançar.")
                )
            rec._close_open_activities()
            rec.state = "investigation"
            if not rec.disposition_date:
                rec.disposition_date = fields.Datetime.now()
            rec._schedule_activity(
                summary=_("NC %s — Investigação / Causa Raiz (SLA 15d)") % rec.name,
                note=_(
                    "Conclua a análise de causa raiz em até 15 dias. "
                    "SLA: 15 dias corridos."
                ),
                days=15,
            )
        return True

    def action_complete_investigation(self):
        self._check_can_leave_investigation()
        for rec in self:
            if rec.state != "investigation":
                raise UserError(
                    _("A NC precisa estar em Investigação para avançar.")
                )
            if not rec.root_cause_date:
                rec.root_cause_date = fields.Datetime.now()
            rec._close_open_activities()
            rec.state = "decision_capa"
            rec._schedule_activity(
                summary=_("NC %s — Decisão sobre abertura de CAPA") % rec.name,
                note=_(
                    "Avalie risco e severidade. Decida: abrir CAPA ou fechar "
                    "com justificativa."
                ),
                days=2,
            )
        return True

    def action_open_capa(self):
        Capa = self.env["afr.ecm.capa"]
        for rec in self:
            if rec.state != "decision_capa":
                raise UserError(
                    _("A NC precisa estar em Decisão CAPA para escalar.")
                )
            if rec.capa_id:
                raise UserError(_("Esta NC já possui uma CAPA vinculada."))
            capa = Capa.create({
                "nc_id": rec.id,
                "title": rec.title,
                "description": rec.description,
                "type": "corrective",
                "responsible_id": (rec.responsible_id or rec.originator_id).id,
            })
            rec._close_open_activities()
            rec.capa_id = capa.id
            rec.state = "escalated_to_capa"
            rec.message_post(
                body=_("CAPA <a href=# data-oe-model=afr.ecm.capa "
                       "data-oe-id=%(id)d>%(name)s</a> criada a partir desta NC.")
                % {"id": capa.id, "name": capa.name},
            )
        return True

    def action_close_no_capa(self):
        self._check_can_close()
        for rec in self:
            if rec.state != "decision_capa":
                raise UserError(
                    _("Só é possível encerrar sem CAPA a partir de Decisão CAPA.")
                )
            rec._close_open_activities()
            rec.state = "closed"
            rec.closure_date = fields.Datetime.now()
        return True

    def action_reopen(self):
        if not self.env.user.has_group("afr_ecm.group_ecm_manager"):
            raise UserError(
                _("Apenas gestores ECM podem reabrir uma NC.")
            )
        for rec in self:
            rec._close_open_activities()
            rec.state = "draft"
            rec.closure_date = False
        return True

    # ---------- constraints ----------
    @api.constrains("state", "closure_reason")
    def _check_closed_has_reason(self):
        for rec in self:
            if rec.state == "closed" and not rec.closure_reason:
                raise ValidationError(
                    _("NC encerrada exige justificativa em 'Encerramento'.")
                )

    @api.constrains("capa_id", "state")
    def _check_escalated_has_capa(self):
        for rec in self:
            if rec.state == "escalated_to_capa" and not rec.capa_id:
                raise ValidationError(
                    _("NC escalada precisa estar vinculada a uma CAPA.")
                )
