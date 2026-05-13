"""F4.3.3 — Cron escalation for pending TI access revocations.

Finds TI_ACC_REV files still in draft/pending state more than 4 hours after
creation and notifies ECM Manager + Diretoria groups via chatter.

Deduplication: posts use a stable sentinel '[TI_REV_ESC]' in the message body.
If such a message already exists, the record is skipped (no new schema field
needed, no edits to existing files).
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Sentinel token embedded in escalation messages so the cron can detect them.
_ESCALATION_SENTINEL = '[TI_REV_ESC]'


class DmsFileRevocationCron(models.Model):
    _inherit = 'dms.file'

    @api.model
    def _cron_pending_revocations_escalate(self, now=None):
        """Find TI_ACC_REV drafts/pending older than 4 hours → escalate via chatter.

        Args:
            now (datetime, optional): injected datetime for unit-test time control.
                                      Defaults to fields.Datetime.now().

        Returns:
            int: number of records escalated.
        """
        now = now or fields.Datetime.now()
        cutoff = now - timedelta(hours=4)

        # Locate the document type
        doc_type = self.env['afr.ecm.document.type'].sudo().search(
            [('code', '=', 'TI_ACC_REV')], limit=1
        )
        if not doc_type:
            _logger.info(
                "afr_ecm cron: TI_ACC_REV doc type not found — nothing to escalate"
            )
            return 0

        # Find revocation files still in unfinished approval state
        domain = [
            ('document_type_id', '=', doc_type.id),
            ('create_date', '<=', fields.Datetime.to_string(cutoff)),
            '|',
            ('approval_state', 'in', ('draft', 'pending')),
            ('approval_state', '=', False),   # no approval workflow on type
        ]
        candidates = self.sudo().search(domain)
        escalated = 0

        for rec in candidates:
            # Dedup: skip if already escalated (sentinel found in any message body)
            already_escalated = any(
                _ESCALATION_SENTINEL in (msg.body or '')
                for msg in rec.sudo().message_ids
            )
            if already_escalated:
                continue

            recipients = self._cron_revocation_escalation_recipients()

            partner_ids = recipients.mapped('partner_id').ids if recipients else []

            age_hours = (now - rec.create_date).total_seconds() / 3600
            body = _(
                "<p><b>%s Escalação: Revogação de Acesso TI pendente</b></p>"
                "<p>O documento <b>%s</b> está em estado '<b>%s</b>' há <b>%.1f horas</b> "
                "(prazo máximo LGPD/ISO-27001: 4h).</p>"
                "<p>Ação imediata necessária: concluir checklist de revogação.</p>"
            ) % (
                _ESCALATION_SENTINEL,
                rec.name or '',
                rec.approval_state or 'sem workflow',
                age_hours,
            )

            try:
                rec.sudo().message_post(
                    body=body,
                    message_type='notification',
                    subtype_xmlid='mail.mt_comment',
                    partner_ids=partner_ids,
                )
                escalated += 1
                _logger.warning(
                    "afr_ecm: escalated TI revocation file id=%s (%s) — %.1fh old",
                    rec.id, rec.name, age_hours,
                )
            except Exception:
                _logger.exception(
                    "afr_ecm: failed to escalate revocation file id=%s", rec.id
                )

        return escalated

    @api.model
    def _cron_revocation_escalation_recipients(self):
        """Return the union of users from group_ecm_manager and group_ecm_area_diretoria."""
        env = self.env
        users = env['res.users']

        mgr_group = env.ref('afr_ecm.group_ecm_manager', raise_if_not_found=False)
        if mgr_group:
            users |= mgr_group.users.filtered('active')

        dir_group = env.ref(
            'afr_ecm.group_ecm_area_diretoria', raise_if_not_found=False
        )
        if dir_group:
            users |= dir_group.users.filtered('active')

        if not users:
            _logger.warning(
                "afr_ecm: no escalation recipients found "
                "(group_ecm_manager + group_ecm_area_diretoria are both empty)"
            )

        return users
