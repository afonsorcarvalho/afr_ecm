"""License renewal alert pipeline for regulatory/compliance documents.

Focuses on AFE, AE, LS, alvará, AVCB, CND, ART, NR-13 and other regulatory
licenses with 3-tier alerting at 90/60/30 days before expiration.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Document type codes for regulatory licenses requiring renewal alerts
LICENSE_DOC_CODES = (
    'REG_AFE',      # AFE ANVISA
    'REG_AE',       # AE ANVISA
    'REG_LS',       # Licença de Funcionamento
    'REG_ALVARA',   # Alvará
    'REG_RT',       # Responsável Técnico
    'REG_ART',      # ART - Anotação de Responsabilidade Técnica
    'FIN_CND',      # Certidão Negativa
    'EM_NR13',      # Inspeção NR-13
    'EM_AVCB',      # Certificado AVCB (bombeiros)
    'EM_SPDA',      # Sistema de Proteção contra Descargas Atmosféricas
    'EM_CAL',       # Certificado de Calibração
)

# Thresholds for tiered alerting
RENEWAL_THRESHOLDS = [90, 60, 30]  # days before expiration


class DmsFileLicenseRenewal(models.Model):
    """Inherit dms.file to add regulatory license renewal alert logic."""

    _inherit = 'dms.file'

    @api.model
    def _cron_license_renewal_alerts(self):
        """Daily cron: scan license docs with expiration_date, emit tiered alerts.

        For each regulatory document (code in LICENSE_DOC_CODES):
          - Compute days_left = (expiration_date - today).days
          - Determine tier: 30-60d=warning, 60-90d=reminder, 0-30d=critical
          - Check for existing activity (anti-dup by summary matching threshold)
          - Create mail.activity if crossing threshold for first time
          - Assign to afr_ecm.group_ecm_area_regulatorio (fallback: group_ecm_manager)

        Returns count of activities created.
        """
        today = fields.Date.today()

        # Search candidates: licensed docs with expiration_date set, not rejected
        domain = [
            ('document_type_id.code', 'in', LICENSE_DOC_CODES),
            ('expiration_date', '!=', False),
            ('active', '=', True),
            '|',
            ('approval_state', '=', False),
            ('approval_state', '!=', 'rejected'),
        ]

        candidates = self.sudo().search(domain)
        created_count = 0

        for file_rec in candidates:
            days_left = (file_rec.expiration_date - today).days

            # Classify tier
            if days_left < 0:
                tier = 'expired'
                tier_label = _('Vencido')
                color = '#d32f2f'  # red
            elif days_left <= 30:
                tier = 'critical'
                tier_label = _('Crítico (≤30d)')
                color = '#f57c00'  # orange
            elif days_left <= 60:
                tier = 'warning'
                tier_label = _('Aviso (≤60d)')
                color = '#fbc02d'  # yellow
            elif days_left <= 90:
                tier = 'reminder'
                tier_label = _('Lembrete (≤90d)')
                color = '#1976d2'  # blue
            else:
                # Beyond 90 days — skip
                continue

            # Dedupe: check if activity exists with same threshold summary
            summary_key = _("Renovação Licença: %s [%s]") % (
                file_rec.document_type_id.name or file_rec.name,
                tier_label,
            )

            existing = file_rec.activity_ids.filtered(
                lambda a: a.summary and tier_label in (a.summary or '')
            )
            if existing:
                # Already alerted on this tier — skip
                continue

            # Build detailed summary
            try:
                directory_name = file_rec.directory_id.complete_name or 'N/A'
            except Exception:
                directory_name = 'N/A'

            summary = _("Renovação Licença: %s [%s]") % (
                file_rec.document_type_id.code or 'N/A',
                tier_label,
            )

            note = _(
                "<p><b>Documento:</b> %s</p>"
                "<p><b>Tipo:</b> %s</p>"
                "<p><b>Pasta:</b> %s</p>"
                "<p><b>Vencimento:</b> %s</p>"
                "<p><b>Dias até vencer:</b> %d</p>"
                "<p><b>Status:</b> %s</p>"
            ) % (
                file_rec.name or 'N/A',
                file_rec.document_type_id.name or 'N/A',
                directory_name,
                file_rec.expiration_date or 'N/A',
                days_left,
                tier_label,
            )

            # Resolve recipient group: afr_ecm.group_ecm_area_regulatorio → fallback manager
            recipients = []
            try:
                reg_group = self.env.ref(
                    'afr_ecm.group_ecm_area_regulatorio',
                    raise_if_not_found=False,
                )
                if reg_group:
                    recipients = reg_group.users.filtered('active')
            except Exception as e:
                _logger.warning(
                    "afr_ecm: failed to load group_ecm_area_regulatorio: %s", e
                )

            if not recipients:
                # Fallback to manager group
                try:
                    mgr_group = self.env.ref('afr_ecm.group_ecm_manager')
                    if mgr_group:
                        recipients = mgr_group.users.filtered('active')
                except Exception as e:
                    _logger.warning(
                        "afr_ecm: failed to load group_ecm_manager: %s", e
                    )

            if not recipients:
                _logger.warning(
                    "afr_ecm: no recipients for license renewal alert on file id=%s",
                    file_rec.id,
                )
                continue

            # Create activity for each recipient
            act_type = self.env.ref(
                'mail.mail_activity_data_warning',
                raise_if_not_found=False,
            )
            if not act_type:
                _logger.warning(
                    "afr_ecm: mail.mail_activity_data_warning not found"
                )
                continue

            for user in recipients:
                try:
                    file_rec.sudo().activity_schedule(
                        'mail.mail_activity_data_warning',
                        user_id=user.id,
                        summary=summary,
                        note=note,
                        date_deadline=file_rec.expiration_date,
                    )
                    created_count += 1
                except Exception as e:
                    _logger.exception(
                        "afr_ecm: failed to create activity for file id=%s, user=%s: %s",
                        file_rec.id, user.id, e,
                    )

        return created_count
