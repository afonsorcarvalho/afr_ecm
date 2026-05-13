"""Unit tests for license renewal alert pipeline (F4.3.2)."""
import logging
from datetime import date, timedelta
from unittest.mock import patch

from odoo.tests.common import TransactionCase

_logger = logging.getLogger(__name__)


class TestLicenseRenewalAlerts(TransactionCase):
    """Test license renewal alert pipeline."""

    def setUp(self):
        super().setUp()

        # Skip if mail module not loaded
        if not self.env['ir.module.module'].search(
            [('name', '=', 'mail'), ('state', '=', 'installed')]
        ):
            self.skipTest("mail module not installed")

        # Create document types for testing
        dt_model = self.env['afr.ecm.document.type']

        self.doc_type_afe = dt_model.create(
            {
                'name': 'AFE ANVISA',
                'code': 'REG_AFE',
                'active': True,
            }
        )

        self.doc_type_cnd = dt_model.create(
            {
                'name': 'Certidão Negativa',
                'code': 'FIN_CND',
                'active': True,
            }
        )

        self.doc_type_other = dt_model.create(
            {
                'name': 'Outro Documento',
                'code': 'DOC_OTHER',
                'active': True,
            }
        )

        # Create test directory
        self.directory = self.env['dms.directory'].create(
            {
                'name': 'Test Directory',
                'company_id': self.env.company.id,
            }
        )

        # Get or create test users + group
        self.user_manager = self.env['res.users'].create(
            {
                'name': 'Manager Test',
                'login': 'mgr_test@example.com',
                'email': 'mgr_test@example.com',
                'active': True,
            }
        )

        mgr_group = self.env.ref('afr_ecm.group_ecm_manager', raise_if_not_found=False)
        if mgr_group:
            mgr_group.write({'users': [(4, self.user_manager.id)]})

    def _create_file(self, doc_type, exp_date, active=True):
        """Helper: create a test dms.file."""
        return self.env['dms.file'].create(
            {
                'name': f"Test File {doc_type.code}",
                'directory_id': self.directory.id,
                'document_type_id': doc_type.id,
                'expiration_date': exp_date,
                'active': active,
            }
        )

    def test_license_renewal_alert_reminder_90_days(self):
        """Doc REG_AFE with exp_date +89d → creates activity, tier='reminder'."""
        today = date.today()
        exp_date = today + timedelta(days=89)

        file_rec = self._create_file(self.doc_type_afe, exp_date)

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should have created 1 activity
        self.assertEqual(count, 1)
        self.assertEqual(len(file_rec.activity_ids), 1)
        activity = file_rec.activity_ids[0]
        self.assertIn('Lembrete', activity.summary)

    def test_license_renewal_alert_warning_60_days(self):
        """Doc REG_AFE with exp_date +59d → creates activity, tier='warning'."""
        today = date.today()
        exp_date = today + timedelta(days=59)

        file_rec = self._create_file(self.doc_type_afe, exp_date)

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should have created 1 activity
        self.assertEqual(count, 1)
        self.assertEqual(len(file_rec.activity_ids), 1)
        activity = file_rec.activity_ids[0]
        self.assertIn('Aviso', activity.summary)

    def test_license_renewal_alert_critical_30_days(self):
        """Doc REG_AFE with exp_date +29d → creates activity, tier='critical'."""
        today = date.today()
        exp_date = today + timedelta(days=29)

        file_rec = self._create_file(self.doc_type_afe, exp_date)

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should have created 1 activity
        self.assertEqual(count, 1)
        self.assertEqual(len(file_rec.activity_ids), 1)
        activity = file_rec.activity_ids[0]
        self.assertIn('Crítico', activity.summary)

    def test_license_renewal_no_alert_beyond_90_days(self):
        """Doc REG_AFE with exp_date +120d → no activity."""
        today = date.today()
        exp_date = today + timedelta(days=120)

        file_rec = self._create_file(self.doc_type_afe, exp_date)

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should have created 0 activities
        self.assertEqual(count, 0)
        self.assertEqual(len(file_rec.activity_ids), 0)

    def test_license_renewal_skip_non_license_docs(self):
        """Doc DOC_OTHER (not in LICENSE_DOC_CODES) → no activity."""
        today = date.today()
        exp_date = today + timedelta(days=29)

        file_rec = self._create_file(self.doc_type_other, exp_date)

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should have created 0 activities
        self.assertEqual(count, 0)
        self.assertEqual(len(file_rec.activity_ids), 0)

    def test_license_renewal_idempotent_no_duplicates(self):
        """Running cron twice same day → no duplicate activities."""
        today = date.today()
        exp_date = today + timedelta(days=29)

        file_rec = self._create_file(self.doc_type_afe, exp_date)

        # Run cron twice
        count1 = self.env['dms.file']._cron_license_renewal_alerts()
        count2 = self.env['dms.file']._cron_license_renewal_alerts()

        # First run: 1 activity
        self.assertEqual(count1, 1)
        # Second run: 0 (dedupe check)
        self.assertEqual(count2, 0)
        # Total: 1 activity
        self.assertEqual(len(file_rec.activity_ids), 1)

    def test_license_renewal_multiple_docs_mixed_tiers(self):
        """Multiple docs at different tiers → correct count."""
        today = date.today()

        # Create 3 docs at different tiers
        doc_89d = self._create_file(self.doc_type_afe, today + timedelta(days=89))
        doc_59d = self._create_file(self.doc_type_afe, today + timedelta(days=59))
        doc_29d = self._create_file(self.doc_type_afe, today + timedelta(days=29))
        doc_120d = self._create_file(self.doc_type_afe, today + timedelta(days=120))

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should create 3 activities (skip 120d)
        self.assertEqual(count, 3)
        self.assertEqual(len(doc_89d.activity_ids), 1)
        self.assertEqual(len(doc_59d.activity_ids), 1)
        self.assertEqual(len(doc_29d.activity_ids), 1)
        self.assertEqual(len(doc_120d.activity_ids), 0)

    def test_license_renewal_inactive_file_skipped(self):
        """Inactive file → no activity."""
        today = date.today()
        exp_date = today + timedelta(days=29)

        file_rec = self._create_file(self.doc_type_afe, exp_date, active=False)

        # Run cron
        count = self.env['dms.file']._cron_license_renewal_alerts()

        # Should skip inactive file
        self.assertEqual(count, 0)
        self.assertEqual(len(file_rec.activity_ids), 0)
