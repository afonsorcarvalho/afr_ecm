import base64
import uuid
from datetime import date, timedelta

from odoo.tests.common import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "afr_ecm")
class TestExpiration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.DocType = cls.env["afr.ecm.document.type"]
        cls.File = cls.env["dms.file"]

        cls.access_group = cls.env["dms.access.group"].create(
            {
                "name": "Exp Test ACL",
                "perm_create": True,
                "perm_write": True,
                "perm_unlink": True,
                "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_user").id)],
            }
        )
        cls.storage = cls.env["dms.storage"].create(
            {"name": "Exp Storage", "save_type": "database"}
        )
        dir_form = Form(cls.env["dms.directory"])
        dir_form.name = uuid.uuid4().hex
        dir_form.is_root_directory = True
        dir_form.storage_id = cls.storage
        dir_form.group_ids.add(cls.access_group)
        cls.directory = dir_form.save()

        cls.dt_ret = cls.DocType.create(
            {
                "name": "Retenção 60d",
                "code": "test_exp_ret60",
                "retention_days": 60,
            }
        )

    @classmethod
    def _content(cls):
        return base64.b64encode(b"\xff content")

    def _make_file(self, expiration=None, doc_type=None):
        vals = {
            "name": uuid.uuid4().hex,
            "directory_id": self.directory.id,
            "content": self._content(),
        }
        if doc_type:
            vals["document_type_id"] = doc_type.id
        if expiration:
            vals["expiration_date"] = expiration
        return self.File.create(vals)

    def test_compute_status_none(self):
        f = self._make_file()
        self.assertEqual(f.expiration_status, "none")
        self.assertEqual(f.days_to_expire, 0)

    def test_compute_status_ok(self):
        f = self._make_file(expiration=date.today() + timedelta(days=60))
        self.assertEqual(f.expiration_status, "ok")
        self.assertEqual(f.days_to_expire, 60)

    def test_compute_status_warning(self):
        f = self._make_file(expiration=date.today() + timedelta(days=15))
        self.assertEqual(f.expiration_status, "warning")

    def test_compute_status_critical(self):
        f = self._make_file(expiration=date.today() + timedelta(days=3))
        self.assertEqual(f.expiration_status, "critical")

    def test_compute_status_expired(self):
        f = self._make_file(expiration=date.today() - timedelta(days=1))
        self.assertEqual(f.expiration_status, "expired")

    def test_search_expiration_status(self):
        # cria 1 de cada
        self._make_file(expiration=date.today() - timedelta(days=2))  # expired
        self._make_file(expiration=date.today() + timedelta(days=2))  # critical
        self._make_file(expiration=date.today() + timedelta(days=20))  # warning
        self._make_file(expiration=date.today() + timedelta(days=60))  # ok

        expired = self.File.search([("expiration_status", "=", "expired")])
        self.assertTrue(expired)
        for f in expired:
            self.assertEqual(f.expiration_status, "expired")

        warning = self.File.search([("expiration_status", "=", "warning")])
        for f in warning:
            self.assertEqual(f.expiration_status, "warning")

    def test_onchange_retention_sets_expiration(self):
        f = self.File.new(
            {
                "name": "x",
                "directory_id": self.directory.id,
                "document_type_id": self.dt_ret.id,
            }
        )
        f._onchange_document_type_id()
        self.assertEqual(
            f.expiration_date, date.today() + timedelta(days=60)
        )

    def test_onchange_does_not_overwrite_existing_expiration(self):
        existing = date.today() + timedelta(days=10)
        f = self.File.new(
            {
                "name": "y",
                "directory_id": self.directory.id,
                "document_type_id": self.dt_ret.id,
                "expiration_date": existing,
            }
        )
        f._onchange_document_type_id()
        self.assertEqual(f.expiration_date, existing)

    def test_get_expiration_alert_days_default(self):
        # remove param se houver pra testar default
        self.env["ir.config_parameter"].sudo().set_param(
            "afr_ecm.expiration_alert_days", "30,7,0"
        )
        days = self.File._get_expiration_alert_days()
        self.assertEqual(days, [30, 7, 0])

    def test_get_expiration_alert_days_custom(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "afr_ecm.expiration_alert_days", "60, 15, 1"
        )
        self.assertEqual(self.File._get_expiration_alert_days(), [60, 15, 1])

    def test_cron_alerts_in_window(self):
        today = date.today()
        # files: vence hoje, em 7d, em 30d, em 5d (fora janela 30/7/0)
        f0 = self._make_file(expiration=today)
        f7 = self._make_file(expiration=today + timedelta(days=7))
        f30 = self._make_file(expiration=today + timedelta(days=30))
        f5 = self._make_file(expiration=today + timedelta(days=5))

        sent = self.File._cron_check_expirations(today=today)
        self.assertEqual(sent, 3, "esperava alertas para 0,7,30")
        for f in (f0, f7, f30):
            self.assertEqual(f.last_expiration_alert, today)
        self.assertFalse(f5.last_expiration_alert)

    def test_cron_does_not_repeat_same_day(self):
        today = date.today()
        f = self._make_file(expiration=today)
        self.File._cron_check_expirations(today=today)
        self.assertEqual(f.last_expiration_alert, today)
        msg_before = len(f.message_ids)
        # roda de novo no mesmo dia
        self.File._cron_check_expirations(today=today)
        msg_after = len(f.message_ids)
        self.assertEqual(msg_before, msg_after, "não deve repetir alerta no mesmo dia")

    def test_cron_alerts_expired(self):
        today = date.today()
        f = self._make_file(expiration=today - timedelta(days=3))
        sent = self.File._cron_check_expirations(today=today)
        self.assertGreaterEqual(sent, 1)
        self.assertEqual(f.last_expiration_alert, today)

    def test_cron_skips_rejected(self):
        from odoo.tests.common import new_test_user
        today = date.today()
        # cria user ativo aprovador
        approver = new_test_user(
            self.env, login="exp_approver",
            groups="afr_ecm.group_ecm_user",
        )
        dt = self.DocType.create(
            {"name": "Skip", "code": "test_skip", "requires_approval": True}
        )
        self.env["afr.ecm.approval.level"].create(
            {
                "document_type_id": dt.id,
                "name": "L1",
                "user_id": approver.id,
                "consensus": "any",
            }
        )
        f = self._make_file(expiration=today, doc_type=dt)
        f.action_submit_for_approval()
        f.with_user(approver).action_reject()
        self.assertEqual(f.approval_state, "rejected")
        self.File._cron_check_expirations(today=today)
        self.assertFalse(f.last_expiration_alert, "rejected não deve receber alerta")
