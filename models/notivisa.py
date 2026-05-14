# -*- coding: utf-8 -*-
"""NOTIVISA workflow model — afr.ecm.notivisa.

Tracks the full lifecycle of tecnovigilância (adverse event / technical
complaint) records required by ANVISA for reprocessed PPS:

    draft -> analysis -> notified -> under_investigation
          -> corrective_action -> closed
                               -> cancelled (manager-only, any non-closed state)

LGPD considerations:
- patient_initials stores only initials, never full name (LGPD Art. 5 VI).
- Freeform clinical text fields (description, analysis_text, etc.) do NOT
  carry tracking=True to avoid PII leaking into chatter log exports.
- State, severity, responsible_id and protocol are tracked (operational data).

Dependencies:
- F4.3.4: afr.ecm.capa (corrective_action_capa_id m2o — requires capa.py loaded)
- F4.3.5: afr.ecm.recall — NOT yet available. recall_id_text is a plain
  Char placeholder. Once F4.3.5 ships, convert to m2o afr.ecm.recall and
  run a data migration.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


EVENT_TYPE = [
    ("adverse_event", "Evento Adverso"),
    ("technical_complaint", "Queixa Técnica"),
    ("suspected_failure", "Falha Suspeita"),
    ("other", "Outro"),
]

SEVERITY = [
    ("low", "Baixa"),
    ("medium", "Média"),
    ("high", "Alta"),
    ("critical", "Crítica"),
    ("death", "Óbito"),
]

STATES = [
    ("draft", "Rascunho"),
    ("analysis", "Análise"),
    ("notified", "Notificado ANVISA"),
    ("under_investigation", "Em Investigação"),
    ("corrective_action", "Ação Corretiva"),
    ("closed", "Encerrado"),
    ("cancelled", "Cancelado"),
]

CLOSURE_OUTCOME = [
    ("resolved", "Resolvido"),
    ("no_action_needed", "Sem Ação Necessária"),
    ("capa_opened", "CAPA Aberta"),
    ("recall_triggered", "Recall Acionado"),
    ("other", "Outro"),
]

# SLA per state in calendar days (used by cron escalation)
_STATE_SLA_DAYS = {
    "analysis": 2,           # ~48h
    "notified": 30,
    "under_investigation": 15,
    "corrective_action": 7,
}


class AfrEcmNotivisa(models.Model):
    _name = "afr.ecm.notivisa"
    _description = "NOTIVISA — Tecnovigilância (Evento Adverso / Queixa Técnica)"
    _inherit = ["mail.thread", "mail.activity.mixin", "afr.ecm.audit.mixin"]
    _order = "create_date desc, id desc"
    _rec_name = "name"

    # ------------------------------------------------------------------ #
    #  Identificação                                                       #
    # ------------------------------------------------------------------ #
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

    event_type = fields.Selection(
        EVENT_TYPE, string="Tipo de Evento", required=True,
        default="adverse_event", tracking=True,
    )
    severity = fields.Selection(
        SEVERITY, string="Severidade", required=True,
        default="medium", tracking=True,
    )

    event_date = fields.Date(string="Data do Evento", tracking=True)
    report_received_date = fields.Date(
        string="Data do Conhecimento", tracking=True,
        help="Data em que a empresa tomou conhecimento do evento.",
    )

    client_partner_id = fields.Many2one(
        "res.partner", string="Hospital / Cliente Notificante",
        ondelete="set null", tracking=True,
    )

    # LGPD — only initials stored here (Art. 5 VI — dados de saúde).
    patient_initials = fields.Char(
        string="Iniciais do Paciente (LGPD)",
        size=10,
        help="Apenas iniciais (ex: J.S.). LGPD: nunca inserir nome completo.",
    )

    lot_id_text = fields.Char(
        string="Lote do Material Suspeito",
        help="Identificação do lote do material PPS suspeito.",
    )
    equipment_text = fields.Char(
        string="Equipamento (Autoclave/Lavadora)",
        help="Identificação do esterilizador ou lavadora utilizado.",
    )
    cycle_id_text = fields.Char(
        string="Referência do Ciclo",
        help="Código de ciclo no módulo supervisório (referência cruzada).",
    )

    # Freeform clinical / analytical text — no tracking (PII risk).
    description = fields.Html(
        string="Descrição do Evento",
        help="Relato do evento. NÃO inserir dados identificadores do paciente.",
    )

    state = fields.Selection(
        STATES, string="Estado", default="draft",
        required=True, copy=False, tracking=True,
    )

    # SLA tracking — date each state was entered (used by cron).
    state_entered_date = fields.Datetime(
        string="Data de Entrada no Estado", copy=False,
        help="Preenchido automaticamente em cada transição de estado.",
    )

    # Per-state deadlines (queryable, mirrors CAPA pattern).
    analysis_deadline = fields.Datetime(
        string="Prazo Análise (48h)", copy=False,
        help="Deadline calculado ao entrar em Análise (state_entered + 2 dias).",
    )
    notified_deadline = fields.Datetime(
        string="Prazo Resposta ANVISA (30d)", copy=False,
    )
    investigation_deadline = fields.Datetime(
        string="Prazo Investigação (15d)", copy=False,
    )
    corrective_deadline = fields.Datetime(
        string="Prazo Ação Corretiva (7d)", copy=False,
    )

    # ------------------------------------------------------------------ #
    #  Análise interna                                                     #
    # ------------------------------------------------------------------ #
    analysis_text = fields.Html(string="Análise Interna")
    analysis_date = fields.Datetime(string="Data da Análise", tracking=True)
    responsible_id = fields.Many2one(
        "res.users", string="Responsável (RT / Designado)",
        tracking=True,
    )

    # ------------------------------------------------------------------ #
    #  Notificação ANVISA                                                  #
    # ------------------------------------------------------------------ #
    notivisa_protocol = fields.Char(
        string="Protocolo NOTIVISA (ANVISA)",
        copy=False,
        tracking=True,
        help="Número de protocolo gerado no portal NOTIVISA após submissão manual.",
    )
    notivisa_submission_date = fields.Datetime(
        string="Data de Submissão NOTIVISA", copy=False, tracking=True,
    )
    notivisa_response_received = fields.Boolean(
        string="Resposta ANVISA Recebida?", tracking=True,
    )
    notivisa_response_text = fields.Html(string="Texto da Resposta ANVISA")

    # ------------------------------------------------------------------ #
    #  Investigação                                                        #
    # ------------------------------------------------------------------ #
    investigation_text = fields.Html(string="Investigação / Causa Raiz")
    investigation_date = fields.Datetime(string="Data de Investigação", tracking=True)

    # ------------------------------------------------------------------ #
    #  Ação Corretiva                                                      #
    # ------------------------------------------------------------------ #
    corrective_action_text = fields.Html(string="Ação Corretiva")
    corrective_action_date = fields.Datetime(string="Data da Ação Corretiva", tracking=True)
    corrective_action_capa_id = fields.Many2one(
        "afr.ecm.capa",
        string="CAPA Vinculada",
        copy=False,
        ondelete="set null",
        tracking=True,
        help="CAPA aberta a partir deste NOTIVISA. Vínculo é unidirecional — "
             "afr.ecm.capa não referencia afr.ecm.notivisa (F4.3.7 pode adicionar "
             "reverse link).",
    )

    # Placeholder — will become m2o afr.ecm.recall when F4.3.5 ships.
    recall_id_text = fields.Char(
        string="Referência Recall (placeholder)",
        help="Código de recall associado. Campo texto até F4.3.5 criar o model "
             "afr.ecm.recall — converter para m2o nesse momento.",
    )

    # ------------------------------------------------------------------ #
    #  Cancelamento                                                        #
    # ------------------------------------------------------------------ #
    cancel_reason = fields.Text(
        string="Motivo do Cancelamento",
        copy=False,
        help="Obrigatório ao cancelar. Requer grupo ECM Manager.",
    )

    # ------------------------------------------------------------------ #
    #  Encerramento                                                        #
    # ------------------------------------------------------------------ #
    closure_date = fields.Datetime(string="Data de Encerramento", tracking=True)
    closure_summary = fields.Html(string="Sumário de Encerramento")
    closure_outcome = fields.Selection(
        CLOSURE_OUTCOME, string="Desfecho", tracking=True,
    )

    # ------------------------------------------------------------------ #
    #  Evidências e pasta ECM                                             #
    # ------------------------------------------------------------------ #
    attachment_ids = fields.Many2many(
        "dms.file",
        relation="afr_ecm_notivisa_dms_file_rel",
        column1="notivisa_id",
        column2="file_id",
        string="Evidências (ECM)",
    )
    directory_id = fields.Many2one(
        "dms.directory",
        string="Pasta ECM",
        default=lambda self: self._default_directory_id(),
        help="Default: pasta âncora do doc type REG_TEC (20_Regulatorio). "
             "Também pode usar OP_TEC (10_Operacao) se o evento for puramente "
             "operacional.",
    )

    # ------------------------------------------------------------------ #
    #  Defaults                                                            #
    # ------------------------------------------------------------------ #
    @api.model
    def _default_directory_id(self):
        """Anchor folder for REG_TEC doc type (20_Regulatorio/Registros/05_Tecnovigilancia)."""
        doc_type = self.env["afr.ecm.document.type"].search(
            [("code", "=", "REG_TEC")], limit=1
        )
        if doc_type and doc_type.default_directory_id:
            return doc_type.default_directory_id.id
        return False

    # ------------------------------------------------------------------ #
    #  Create (sequence)                                                   #
    # ------------------------------------------------------------------ #
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") in (_("Novo"), "Novo", "New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("afr.ecm.notivisa")
                    or _("Novo")
                )
        return super().create(vals_list)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #
    def _schedule_activity(self, summary, note, days=0):
        """Creates a TODO mail activity for responsible_id."""
        self.ensure_one()
        deadline = fields.Date.context_today(self) + timedelta(days=max(days, 0))
        user_id = (self.responsible_id or self.env.user).id
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            date_deadline=deadline,
            summary=summary,
            note=note,
            user_id=user_id,
        )

    def _close_open_activities(self):
        for rec in self:
            if rec.activity_ids:
                rec.activity_ids.action_feedback(
                    feedback=_("Atividade encerrada por mudança de estado do NOTIVISA.")
                )

    def _now(self):
        return fields.Datetime.now()

    # ------------------------------------------------------------------ #
    #  Guards                                                              #
    # ------------------------------------------------------------------ #
    def _check_notivisa_protocol_required(self):
        """Death severity requires NOTIVISA protocol before advancing."""
        for rec in self:
            if rec.severity == "death" and not rec.notivisa_protocol:
                raise UserError(
                    _(
                        "NOTIVISA %(name)s: severidade ÓBITO exige protocolo NOTIVISA "
                        "registrado antes de avançar o estado."
                    ) % {"name": rec.name}
                )

    def _check_state(self, allowed, action_label=""):
        for rec in self:
            if rec.state not in allowed:
                raise UserError(
                    _(
                        "Ação '%(action)s' não permitida no estado '%(state)s' "
                        "(%(name)s)."
                    ) % {
                        "action": action_label,
                        "state": dict(STATES).get(rec.state, rec.state),
                        "name": rec.name,
                    }
                )

    # ------------------------------------------------------------------ #
    #  Actions / State machine                                             #
    # ------------------------------------------------------------------ #
    def action_start_analysis(self):
        """draft → analysis. Schedules 48h activity for notification decision."""
        self._check_state(["draft"], "Iniciar Análise")
        now = self._now()
        for rec in self:
            rec.write({
                "state": "analysis",
                "state_entered_date": now,
                "analysis_deadline": now + timedelta(days=_STATE_SLA_DAYS["analysis"]),
            })
            rec._schedule_activity(
                summary=_("NOTIVISA %s — Decisão de notificação ANVISA (48h)") % rec.name,
                note=_(
                    "Analise o evento e decida se é necessária notificação ao portal "
                    "NOTIVISA/ANVISA. Prazo: 48 horas."
                ),
                days=_STATE_SLA_DAYS["analysis"],
            )
        return True

    def action_record_notification(self):
        """analysis → notified. Requires notivisa_protocol + notivisa_submission_date."""
        self._check_state(["analysis"], "Registrar Notificação ANVISA")
        now = self._now()
        for rec in self:
            if not rec.notivisa_protocol:
                raise UserError(
                    _("NOTIVISA %s: informe o Protocolo NOTIVISA antes de registrar a notificação.") % rec.name
                )
            if not rec.notivisa_submission_date:
                raise UserError(
                    _("NOTIVISA %s: informe a Data de Submissão NOTIVISA.") % rec.name
                )
            rec._close_open_activities()
            rec.write({
                "state": "notified",
                "state_entered_date": now,
                "notified_deadline": now + timedelta(days=_STATE_SLA_DAYS["notified"]),
            })
            rec._schedule_activity(
                summary=_("NOTIVISA %s — Aguardar resposta ANVISA (30d)") % rec.name,
                note=_(
                    "Monitore a resposta do portal NOTIVISA/ANVISA. "
                    "Prazo esperado: 30 dias a partir da submissão."
                ),
                days=_STATE_SLA_DAYS["notified"],
            )
        return True

    def action_start_investigation(self):
        """analysis or notified → under_investigation.

        From 'analysis': allowed only if severity != 'death' (death always requires
        formal ANVISA notification first).
        """
        self._check_state(["analysis", "notified"], "Iniciar Investigação")
        now = self._now()
        for rec in self:
            if rec.state == "analysis" and rec.severity == "death":
                raise UserError(
                    _(
                        "NOTIVISA %s: severidade ÓBITO requer notificação ANVISA antes "
                        "de iniciar investigação sem protocolo."
                    ) % rec.name
                )
            rec._close_open_activities()
            rec.write({
                "state": "under_investigation",
                "state_entered_date": now,
                "investigation_deadline": now + timedelta(days=_STATE_SLA_DAYS["under_investigation"]),
            })
            rec._schedule_activity(
                summary=_("NOTIVISA %s — Investigação de causa raiz (15d)") % rec.name,
                note=_(
                    "Conduza investigação de causa raiz do evento adverso/queixa técnica. "
                    "Prazo: 15 dias."
                ),
                days=_STATE_SLA_DAYS["under_investigation"],
            )
        return True

    def action_record_corrective_action(self):
        """under_investigation → corrective_action."""
        self._check_state(["under_investigation"], "Registrar Ação Corretiva")
        now = self._now()
        for rec in self:
            if not rec.investigation_text:
                raise UserError(
                    _("NOTIVISA %s: preencha a Investigação / Causa Raiz antes de avançar.") % rec.name
                )
            rec._close_open_activities()
            rec.write({
                "state": "corrective_action",
                "state_entered_date": now,
                "corrective_deadline": now + timedelta(days=_STATE_SLA_DAYS["corrective_action"]),
                "investigation_date": now,
            })
            rec._schedule_activity(
                summary=_("NOTIVISA %s — Implementar ação corretiva (7d)") % rec.name,
                note=_(
                    "Implemente a ação corretiva definida. "
                    "Considere abrir CAPA se ação for sistêmica. Prazo: 7 dias."
                ),
                days=_STATE_SLA_DAYS["corrective_action"],
            )
        return True

    def action_close(self):
        """corrective_action → closed. Requires closure_summary + closure_outcome.

        Also enforces: severity=death requires notivisa_protocol (double-gate).
        """
        self._check_state(["corrective_action"], "Encerrar")
        now = self._now()
        for rec in self:
            if not rec.closure_summary:
                raise UserError(
                    _("NOTIVISA %s: preencha o Sumário de Encerramento.") % rec.name
                )
            if not rec.closure_outcome:
                raise UserError(
                    _("NOTIVISA %s: selecione o Desfecho antes de encerrar.") % rec.name
                )
            # Double-gate for death severity.
            if rec.severity == "death" and not rec.notivisa_protocol:
                raise UserError(
                    _(
                        "NOTIVISA %s: severidade ÓBITO não pode ser encerrado sem "
                        "protocolo NOTIVISA registrado."
                    ) % rec.name
                )
            rec._close_open_activities()
            rec.write({
                "state": "closed",
                "state_entered_date": now,
                "closure_date": now,
            })
        return True

    def action_cancel(self):
        """Any non-closed state → cancelled. Manager-only. Requires cancel_reason."""
        if not self.env.user.has_group("afr_ecm.group_ecm_manager"):
            raise UserError(_("Apenas gestores ECM podem cancelar um NOTIVISA."))
        self._check_state(
            ["draft", "analysis", "notified", "under_investigation", "corrective_action"],
            "Cancelar",
        )
        now = self._now()
        for rec in self:
            if not rec.cancel_reason:
                raise UserError(
                    _("NOTIVISA %s: informe o Motivo do Cancelamento antes de cancelar.") % rec.name
                )
            rec._close_open_activities()
            rec.write({
                "state": "cancelled",
                "state_entered_date": now,
            })
        return True

    def action_open_capa(self):
        """Helper: creates a linked afr.ecm.capa and sets corrective_action_capa_id.

        Only available from corrective_action state. One-way link — capa has no
        back-reference to notivisa (F4.3.7 may add reverse link).
        """
        self._check_state(["corrective_action"], "Abrir CAPA")
        for rec in self:
            if rec.corrective_action_capa_id:
                raise UserError(
                    _("NOTIVISA %s: já existe uma CAPA vinculada (%s).") % (
                        rec.name, rec.corrective_action_capa_id.name
                    )
                )
            capa = self.env["afr.ecm.capa"].create({
                "title": _("CAPA — %(notivisa)s: %(title)s") % {
                    "notivisa": rec.name,
                    "title": rec.title,
                },
                "type": "corrective",
                "responsible_id": rec.responsible_id.id if rec.responsible_id else False,
                "description": _(
                    "<p>CAPA criada a partir do NOTIVISA <strong>%(n)s</strong>.</p>"
                    "<p><strong>Tipo de evento:</strong> %(et)s</p>"
                    "<p><strong>Severidade:</strong> %(sv)s</p>"
                ) % {
                    "n": rec.name,
                    "et": dict(EVENT_TYPE).get(rec.event_type, rec.event_type),
                    "sv": dict(SEVERITY).get(rec.severity, rec.severity),
                },
            })
            rec.write({"corrective_action_capa_id": capa.id})
        return True

    # ------------------------------------------------------------------ #
    #  Cron: overdue escalation                                            #
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_notivisa_overdue(self):
        """Daily cron: finds NOTIVISA records past stage SLA and escalates.

        Posts an activity to responsible_id + notifies group_ecm_area_regulatorio
        and group_ecm_manager via message_post.
        """
        now = fields.Datetime.now()
        today = fields.Date.today()

        stage_deadline_map = [
            ("analysis", "analysis_deadline"),
            ("notified", "notified_deadline"),
            ("under_investigation", "investigation_deadline"),
            ("corrective_action", "corrective_deadline"),
        ]

        escalated = 0
        for state, deadline_field in stage_deadline_map:
            overdue = self.search([
                ("state", "=", state),
                (deadline_field, "!=", False),
                (deadline_field, "<", now),
            ])
            for rec in overdue:
                # Avoid duplicate escalation activities on same day.
                already = rec.activity_ids.filtered(
                    lambda a, n=rec.name: "Escalação" in (a.summary or "")
                    and n in (a.summary or "")
                )
                if already:
                    continue

                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=today,
                    summary=_("NOTIVISA %s — ESCALAÇÃO: SLA vencido") % rec.name,
                    note=_(
                        "O NOTIVISA <strong>%(n)s</strong> está vencido no estado "
                        "<em>%(s)s</em>. Prazo era %(d)s. "
                        "Registre o andamento ou justifique o atraso."
                    ) % {
                        "n": rec.name,
                        "s": dict(STATES).get(state, state),
                        "d": rec[deadline_field],
                    },
                    user_id=(rec.responsible_id or self.env.user).id,
                )

                # Notify manager group via chatter.
                manager_group = self.env.ref(
                    "afr_ecm.group_ecm_manager", raise_if_not_found=False
                )
                regulatorio_group = self.env.ref(
                    "afr_ecm.group_ecm_area_regulatorio", raise_if_not_found=False
                )
                partner_ids = []
                for grp in (manager_group, regulatorio_group):
                    if grp:
                        partner_ids += grp.users.mapped("partner_id").ids

                if partner_ids:
                    rec.message_post(
                        body=_(
                            "Escalação automática: NOTIVISA <strong>%(n)s</strong> "
                            "com SLA vencido no estado <em>%(s)s</em> "
                            "(prazo era %(d)s)."
                        ) % {
                            "n": rec.name,
                            "s": dict(STATES).get(state, state),
                            "d": rec[deadline_field],
                        },
                        partner_ids=list(set(partner_ids)),
                        subtype_xmlid="mail.mt_note",
                    )
                escalated += 1

        return escalated

    # ------------------------------------------------------------------ #
    #  Constraints                                                         #
    # ------------------------------------------------------------------ #
    @api.constrains("severity", "state", "notivisa_protocol")
    def _check_death_requires_protocol_on_close(self):
        """Ensure severity=death records are never closed without notivisa_protocol."""
        for rec in self:
            if (
                rec.state == "closed"
                and rec.severity == "death"
                and not rec.notivisa_protocol
            ):
                raise ValidationError(
                    _(
                        "NOTIVISA %(name)s: registros com severidade ÓBITO não podem "
                        "ser encerrados sem protocolo NOTIVISA."
                    ) % {"name": rec.name}
                )

    @api.constrains("state", "cancel_reason")
    def _check_cancel_reason_set(self):
        """Ensure cancel_reason is filled when state is cancelled."""
        for rec in self:
            if rec.state == "cancelled" and not rec.cancel_reason:
                raise ValidationError(
                    _(
                        "NOTIVISA %(name)s: motivo de cancelamento é obrigatório."
                    ) % {"name": rec.name}
                )
