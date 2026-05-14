# -*- coding: utf-8 -*-
"""NC 24h SLA escalation cron — afr.ecm.nc inheritance.

F4.3.8: Hourly check for NCs stuck in 'disposition' state >24h.
Posts urgent chatter message + creates WARNING activity + marks escalated.

RDC 15 compliance: 24h SLA for immediate disposition / correction.
"""
from datetime import timedelta

from odoo import _, api, fields, models
import logging

_logger = logging.getLogger(__name__)


class NcSlaCheck(models.Model):
    _inherit = 'afr.ecm.nc'

    disposition_entered_date = fields.Datetime(
        string="Data Entrada Disposição",
        readonly=True,
        help="Timestamp quando NC entrou em estado 'disposition'. Usado para SLA 24h.",
    )

    sla_24h_escalated = fields.Boolean(
        string="SLA 24h Escalado",
        default=False,
        help="True quando o cron de SLA 24h já escalou esta NC. Reset ao mover para outro estado.",
    )

    def write(self, vals):
        # Track when state enters 'disposition'
        for rec in self:
            if 'state' in vals and vals['state'] == 'disposition' and rec.state != 'disposition':
                vals['disposition_entered_date'] = fields.Datetime.now()

        # Reset escalation flag if state changes away from 'disposition'
        if 'state' in vals and vals['state'] != 'disposition':
            for rec in self:
                if rec.state == 'disposition' and rec.sla_24h_escalated:
                    vals['sla_24h_escalated'] = False
                    vals['disposition_entered_date'] = False

        return super().write(vals)

    @api.model
    def _cron_nc_sla_24h_check(self):
        """Hourly: find NCs stuck in 'disposition' state >24h that have not
        been escalated yet. Post urgent chatter + create WARNING activity.

        RDC 15 SLA requirement: 24h for immediate correction (disposition).
        """
        now = fields.Datetime.now()
        threshold_24h_ago = now - timedelta(hours=24)

        # Find NCs: in disposition, entered >24h ago, not yet escalated
        ncs_to_escalate = self.search([
            ('state', '=', 'disposition'),
            ('sla_24h_escalated', '=', False),
            ('disposition_entered_date', '<', threshold_24h_ago),
        ])

        _logger.info(
            "NC SLA 24h cron: found %d NCs to escalate (stuck >24h in disposition)",
            len(ncs_to_escalate)
        )

        for nc in ncs_to_escalate:
            try:
                # Collect recipients for chatter mention
                recipient_ids = set()

                # 1. Responsible user
                if nc.responsible_id:
                    recipient_ids.add(nc.responsible_id.partner_id.id)

                # 2. Originator user
                if nc.originator_id:
                    recipient_ids.add(nc.originator_id.partner_id.id)

                # 3. Group members: ECM Manager group
                try:
                    ecm_manager_group = self.env.ref('afr_ecm.group_ecm_manager')
                    for user in ecm_manager_group.users:
                        recipient_ids.add(user.partner_id.id)
                except Exception as e:
                    _logger.warning("Could not find group_ecm_manager: %s", e)

                # 4. Group members: ECM Area SGQ group
                try:
                    ecm_sgq_group = self.env.ref('afr_ecm.group_ecm_area_sgq')
                    for user in ecm_sgq_group.users:
                        recipient_ids.add(user.partner_id.id)
                except Exception as e:
                    _logger.warning("Could not find group_ecm_area_sgq: %s", e)

                recipient_ids = list(recipient_ids)

                # Post chatter message (URGENT tone)
                message_body = _(
                    "<p><strong style='color: red;'>⚠️ NC SLA VENCIDO — DISPOSIÇÃO NÃO CONCLUÍDA ⚠️</strong></p>"
                    "<p>NC <strong>%(nc_name)s</strong> encontra-se em estado "
                    "<strong>Disposição</strong> há mais de 24 horas (SLA RDC 15).</p>"
                    "<p><strong>Entrada em disposição:</strong> %(entry_date)s</p>"
                    "<p><strong>Prazo expirado em:</strong> %(deadline)s</p>"
                    "<p>Ação imediata necessária. Registre a correção ou justifique atraso.</p>"
                ) % {
                    'nc_name': nc.name,
                    'entry_date': nc.disposition_entered_date.strftime('%Y-%m-%d %H:%M:%S') if nc.disposition_entered_date else '?',
                    'deadline': (nc.disposition_entered_date + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S') if nc.disposition_entered_date else '?',
                }

                nc.message_post(
                    body=message_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                    partner_ids=recipient_ids,
                )

                # Create WARNING activity (deadline = today, already overdue)
                activity_type_warning = self.env.ref('mail.mail_activity_data_warning')
                nc.activity_schedule(
                    activity_type_warning.id,
                    date_deadline=fields.Date.context_today(self),
                    summary=_("NC SLA 24h vencido — disposição não concluída"),
                    note=_(
                        "SLA RDC 15 expirado. NC em disposição >24h sem conclusão. "
                        "Complete a disposição ou justifique o atraso imediatamente."
                    ),
                    user_id=nc.responsible_id.id if nc.responsible_id else self.env.user.id,
                )

                # Mark as escalated (idempotency)
                nc.sla_24h_escalated = True

                _logger.info("NC SLA escalation complete: %s", nc.name)

            except Exception as e:
                _logger.error(
                    "Error escalating NC %s: %s", nc.name, str(e), exc_info=True
                )
                # Continue to next NC
                continue

        return True
