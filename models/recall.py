# -*- coding: utf-8 -*-
"""Recall (lot recovery) workflow model — afr.ecm.recall.

State machine for recovering potentially non-sterile PPS lots from clients
when a sterilization cycle is invalidated (typically by a positive Biological
Indicator — BI+).

    draft -> decision -> notification -> collection_in_progress -> disposal -> closed
                                                                 -> cancelled

Triggered automatically by the creation/approval of a `dms.file` of doc type
code `OP_BI_POS` (see `dms_file_recall_trigger.py`). Files are stored in the
folder anchored by doc type `OP_RECALL` (08_Recalls/).
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

TRIGGER_TYPES = [
    ("bi_positive", "BI Positivo"),
    ("cycle_failure", "Falha de Ciclo"),
    ("client_complaint", "Reclamação de Cliente"),
    ("regulatory_order", "Ordem Regulatória"),
    ("internal_review", "Revisão Interna"),
    ("other", "Outro"),
]

STATES = [
    ("draft", "Rascunho"),
    ("decision", "Decisão"),
    ("notification", "Notificação"),
    ("collection_in_progress", "Coleta em Andamento"),
    ("disposal", "Descarte / Disposição Final"),
    ("closed", "Encerrado"),
    ("cancelled", "Cancelado"),
]

# SLAs in hours/days between stages — used by cron escalation.
SLA_DECISION_HOURS = 24
SLA_NOTIFICATION_HOURS = 48
SLA_COLLECTION_DAYS = 7


class AfrEcmRecall(models.Model):
    _name = "afr.ecm.recall"
    _description = "Recall (Recuperação de Lote)"
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
    severity = fields.Selection(
        SEVERITY, string="Severidade", default="high", required=True, tracking=True
    )

    state = fields.Selection(
        STATES,
        string="Estado",
        default="draft",
        required=True,
        copy=False,
        tracking=True,
    )

    # ---------- gatilho ----------
    trigger_type = fields.Selection(
        TRIGGER_TYPES, string="Tipo de Gatilho", default="other",
        required=True, tracking=True,
    )
    trigger_event_ref = fields.Char(
        string="Referência do Evento Gatilho",
        help="Identificador externo do evento que originou este recall "
             "(xmlid de doc, código de NC, OS, etc.). Placeholder textual.",
    )
    bi_positive_file_id = fields.Many2one(
        "dms.file",
        string="Documento BI+ Origem",
        ondelete="set null",
        copy=False,
        help="Arquivo OP_BI_POS que disparou este recall (quando aplicável).",
    )

    # ---------- escopo do lote ----------
    cycle_id_text = fields.Char(
        string="Ciclo (ID)",
        help="Identificador do ciclo de esterilização. Texto livre — futura "
             "FK para afr_supervisorio_ciclos.",
    )
    lot_id_text = fields.Char(
        string="Lote (ID)",
        help="Identificador do lote afetado. Texto livre.",
    )
    equipment_text = fields.Char(
        string="Equipamento",
        help="Identificação do esterilizador (ex.: AC01). Texto livre.",
    )

    # ---------- partes envolvidas ----------
    affected_clients_ids = fields.Many2many(
        "res.partner",
        relation="afr_ecm_recall_partner_rel",
        column1="recall_id",
        column2="partner_id",
        string="Clientes Afetados",
    )
    responsible_id = fields.Many2one(
        "res.users",
        string="Coordenador do Recall",
        default=lambda self: self.env.user,
        tracking=True,
    )

    # ---------- decisão ----------
    decision_date = fields.Datetime(string="Data da Decisão", tracking=True)
    decision_text = fields.Html(
        string="Justificativa da Decisão",
        help="Racional técnico/regulatório que motivou abrir o recall.",
    )

    # ---------- notificação ----------
    notification_date = fields.Datetime(string="Data da Notificação", tracking=True)
    notification_text = fields.Html(
        string="Conteúdo da Notificação",
        help="Texto / resumo da comunicação enviada aos clientes.",
    )

    # ---------- coleta ----------
    collection_start_date = fields.Date(string="Início da Coleta", tracking=True)
    collection_progress_text = fields.Html(string="Progresso da Coleta")

    # ---------- disposição final ----------
    disposal_date = fields.Date(string="Data do Descarte", tracking=True)
    disposal_text = fields.Html(
        string="Disposição Final",
        help="Destino final do material (incinerado, devolvido ao "
             "fabricante, outro).",
    )

    # ---------- NOTIVISA (placeholder até F4.3.6 mesclar) ----------
    notivisa_required = fields.Boolean(
        string="Requer NOTIVISA",
        help="Marca se o recall demanda notificação à ANVISA via NOTIVISA.",
    )
    notivisa_ref = fields.Char(
        string="Referência NOTIVISA",
        help="Placeholder textual (protocolo NOTIVISA, código de evento). "
             "TODO: converter para Many2one('afr.ecm.notivisa') após "
             "F4.3.6 ser mesclado.",
    )

    # ---------- encerramento ----------
    closure_date = fields.Datetime(string="Data de Encerramento", tracking=True)
    closure_summary = fields.Html(
        string="Sumário de Encerramento",
        help="Resumo executivo do recall — eficácia, lições aprendidas.",
    )

    # ---------- evidências / pasta ECM ----------
    attachment_ids = fields.Many2many(
        "dms.file",
        relation="afr_ecm_recall_dms_file_rel",
        column1="recall_id",
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
            [("code", "=", "OP_RECALL")], limit=1
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
                    self.env["ir.sequence"].next_by_code("afr.ecm.recall")
                    or _("Novo")
                )
        return super().create(vals_list)

    # ---------- helpers ----------
    def _schedule_activity(self, summary, note, days=0, hours=0):
        """Cria mail.activity TODO."""
        self.ensure_one()
        deadline = fields.Date.context_today(self) + timedelta(
            days=days + (1 if hours and hours >= 24 else 0)
        )
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
                    feedback=_("Encerrada por mudança de estado do recall.")
                )

    # ---------- guards ----------
    def _check_can_take_decision(self, decision_text=None, decision_date=None):
        for rec in self:
            text = decision_text if decision_text is not None else rec.decision_text
            dt = decision_date if decision_date is not None else rec.decision_date
            if not text or not dt:
                raise UserError(
                    _(
                        "Preencha 'Justificativa da Decisão' e 'Data da Decisão' "
                        "antes de avançar."
                    )
                )

    def _check_can_notify(self):
        for rec in self:
            if not rec.affected_clients_ids:
                raise UserError(
                    _("Informe ao menos um cliente afetado antes de notificar.")
                )
            if not rec.notification_text or not rec.notification_date:
                raise UserError(
                    _(
                        "Preencha 'Conteúdo da Notificação' e 'Data da "
                        "Notificação' antes de avançar."
                    )
                )

    def _check_can_start_collection(self):
        for rec in self:
            if not rec.collection_start_date:
                raise UserError(
                    _("Informe a 'Data de Início da Coleta' antes de avançar.")
                )

    def _check_can_close(self):
        for rec in self:
            if not rec.disposal_text or not rec.disposal_date:
                raise UserError(
                    _(
                        "Preencha 'Disposição Final' e 'Data do Descarte' "
                        "antes de encerrar."
                    )
                )
            if not rec.closure_summary:
                raise UserError(
                    _("Informe o 'Sumário de Encerramento' antes de fechar.")
                )

    # ---------- actions ----------
    def action_take_decision(self):
        """draft -> decision. Atomic write to avoid partial-state constraint
        breakage (lesson from F4.3.4)."""
        for rec in self:
            if rec.state != "draft":
                raise UserError(
                    _("Só é possível tomar decisão a partir de Rascunho.")
                )
            self._check_can_take_decision()
            rec._close_open_activities()
            rec.write({
                "state": "decision",
                "decision_date": rec.decision_date or fields.Datetime.now(),
            })
            rec._schedule_activity(
                summary=_("Recall %s — Notificar clientes (SLA 24h)") % rec.name,
                note=_(
                    "Identifique e notifique todos os clientes afetados. "
                    "SLA: 24h."
                ),
                hours=SLA_DECISION_HOURS,
            )
        return True

    def action_notify_clients(self):
        """decision -> notification."""
        for rec in self:
            if rec.state != "decision":
                raise UserError(
                    _("O recall precisa estar em Decisão para notificar.")
                )
            self._check_can_notify()
            rec._close_open_activities()
            rec.write({
                "state": "notification",
                "notification_date": (
                    rec.notification_date or fields.Datetime.now()
                ),
            })
            rec._schedule_activity(
                summary=_("Recall %s — Iniciar coleta (SLA 48h)") % rec.name,
                note=_(
                    "Inicie a coleta dos materiais notificados. SLA: 48h."
                ),
                hours=SLA_NOTIFICATION_HOURS,
            )
        return True

    def action_start_collection(self):
        """notification -> collection_in_progress."""
        for rec in self:
            if rec.state != "notification":
                raise UserError(
                    _("O recall precisa estar em Notificação para iniciar coleta.")
                )
            self._check_can_start_collection()
            rec._close_open_activities()
            rec.write({"state": "collection_in_progress"})
            rec._schedule_activity(
                summary=_("Recall %s — Concluir coleta (SLA 7d)") % rec.name,
                note=_("Finalize a coleta e prepare o material para descarte."),
                days=SLA_COLLECTION_DAYS,
            )
        return True

    def action_complete_collection(self):
        """collection_in_progress -> disposal."""
        for rec in self:
            if rec.state != "collection_in_progress":
                raise UserError(
                    _("O recall precisa estar em Coleta para avançar para Descarte.")
                )
            rec._close_open_activities()
            rec.write({"state": "disposal"})
            rec._schedule_activity(
                summary=_("Recall %s — Registrar disposição final") % rec.name,
                note=_("Registre a destinação final do material recolhido."),
                days=2,
            )
        return True

    def action_record_disposal(self):
        """disposal -> closed. ATOMIC write — sets state + closure_date +
        text fields together so closure constraints validate against the full
        record, not a partial transition (F4.3.4 hotfix lesson)."""
        for rec in self:
            if rec.state != "disposal":
                raise UserError(
                    _("O recall precisa estar em Descarte para encerrar.")
                )
            self._check_can_close()
            rec._close_open_activities()
            rec.write({
                "state": "closed",
                "closure_date": fields.Datetime.now(),
            })
        return True

    def action_cancel(self):
        """any state except closed -> cancelled. Manager-only."""
        if not self.env.user.has_group("afr_ecm.group_ecm_manager"):
            raise UserError(_("Apenas gestores ECM podem cancelar um recall."))
        for rec in self:
            if rec.state == "closed":
                raise UserError(
                    _("Não é possível cancelar um recall já encerrado.")
                )
            rec._close_open_activities()
            rec.write({
                "state": "cancelled",
                "closure_date": fields.Datetime.now(),
            })
        return True

    # ---------- constraints ----------
    @api.constrains("state", "decision_text", "decision_date")
    def _check_decision_state_consistency(self):
        for rec in self:
            if rec.state in (
                "decision", "notification", "collection_in_progress",
                "disposal", "closed",
            ):
                if not rec.decision_text or not rec.decision_date:
                    raise ValidationError(
                        _(
                            "Recall em '%s' exige 'Justificativa da Decisão' "
                            "e 'Data da Decisão'."
                        ) % rec.state
                    )

    @api.constrains("state", "affected_clients_ids")
    def _check_notification_has_clients(self):
        for rec in self:
            if rec.state in (
                "notification", "collection_in_progress", "disposal", "closed",
            ):
                if not rec.affected_clients_ids:
                    raise ValidationError(
                        _("Recall em '%s' exige clientes afetados.") % rec.state
                    )

    @api.constrains("state", "disposal_text", "closure_summary")
    def _check_closed_has_summary(self):
        for rec in self:
            if rec.state == "closed":
                if not rec.disposal_text or not rec.closure_summary:
                    raise ValidationError(
                        _(
                            "Recall encerrado exige 'Disposição Final' e "
                            "'Sumário de Encerramento'."
                        )
                    )

    # ---------- cron: SLA escalation ----------
    @api.model
    def _cron_recall_overdue_alerts(self):
        """Runs daily. For each non-closed recall, checks the time-in-state
        against its stage SLA and posts an escalation activity to the
        responsible (or any ECM manager)."""
        now = fields.Datetime.now()
        Mgr = self.env.ref(
            "afr_ecm.group_ecm_manager", raise_if_not_found=False
        )
        manager_user = False
        if Mgr and Mgr.users:
            manager_user = Mgr.users[0]

        # Stages with SLA from when the state was last entered.
        # We approximate with write_date since the last transition write —
        # good enough for daily escalation.
        stage_slas = {
            "decision": timedelta(hours=SLA_DECISION_HOURS),
            "notification": timedelta(hours=SLA_NOTIFICATION_HOURS),
            "collection_in_progress": timedelta(days=SLA_COLLECTION_DAYS),
        }
        for state_code, sla in stage_slas.items():
            overdue = self.sudo().search([
                ("state", "=", state_code),
                ("write_date", "<", now - sla),
            ])
            for rec in overdue:
                user = rec.responsible_id or manager_user or self.env.user
                # avoid spamming: only one open escalation activity at a time
                already = rec.activity_ids.filtered(
                    lambda a: a.summary
                    and a.summary.startswith("ESCALAÇÃO Recall")
                )
                if already:
                    continue
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("ESCALAÇÃO Recall %s — SLA estourado em '%s'")
                    % (rec.name, state_code),
                    note=_(
                        "Recall fora do SLA na etapa '%s'. Tomar ação imediata."
                    ) % state_code,
                    user_id=user.id,
                    date_deadline=fields.Date.context_today(self),
                )
        return True
