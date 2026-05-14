# -*- coding: utf-8 -*-
"""Tests for F4.3.5 — Recall workflow + auto-trigger from BI Positivo file.

Mirrors the existing test_nc_capa.py and test_approval.py patterns: top-level
Odoo imports, tagged TransactionCase. pytest will only collect, not execute
(no Odoo env at collection time, so the file must import cleanly).
"""
import base64
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import Form, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_recall")
class TestRecall(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Recall = cls.env["afr.ecm.recall"]
        cls.DocType = cls.env["afr.ecm.document.type"]
        cls.File = cls.env["dms.file"]

        cls.operacao_user = new_test_user(
            cls.env,
            login="operacao_user_test_recall",
            groups="afr_ecm.group_ecm_area_operacao",
        )
        cls.manager = new_test_user(
            cls.env,
            login="ecm_mgr_test_recall",
            groups="afr_ecm.group_ecm_manager",
        )
        cls.regular_user = new_test_user(
            cls.env,
            login="ecm_user_test_recall",
            groups="afr_ecm.group_ecm_user",
        )

        cls.partner_a = cls.env["res.partner"].create({"name": "Cliente A"})
        cls.partner_b = cls.env["res.partner"].create({"name": "Cliente B"})

        # ---- DMS infra para o teste de auto-trigger ----
        cls.access_group = cls.env["dms.access.group"].create(
            {
                "name": "Test Recall Access",
                "perm_create": True,
                "perm_write": True,
                "perm_unlink": True,
                "group_ids": [
                    (4, cls.env.ref("afr_ecm.group_ecm_user").id),
                ],
            }
        )
        cls.storage = cls.env["dms.storage"].create(
            {"name": "Test Storage Recall", "save_type": "database"}
        )
        directory_form = Form(cls.env["dms.directory"])
        directory_form.name = uuid.uuid4().hex
        directory_form.is_root_directory = True
        directory_form.storage_id = cls.storage
        directory_form.group_ids.add(cls.access_group)
        cls.directory = directory_form.save()

        # search-or-create — types não estão seedados em document_type_data.xml
        cls.dt_bi_pos = cls.DocType.search([("code", "=", "OP_BI_POS")], limit=1)
        if not cls.dt_bi_pos:
            cls.dt_bi_pos = cls.DocType.create({
                "name": "BI Positivo (test)",
                "code": "OP_BI_POS",
                "requires_approval": True,
            })
        cls.dt_recall = cls.DocType.search([("code", "=", "OP_RECALL")], limit=1)
        if not cls.dt_recall:
            cls.dt_recall = cls.DocType.create({
                "name": "Recall (test)",
                "code": "OP_RECALL",
                "requires_approval": False,
            })

    # ---------- helpers ----------
    def _make_recall(self, **kwargs):
        vals = {
            "title": "Recall de teste",
            "trigger_type": "cycle_failure",
            "severity": "high",
            "lot_id_text": "LOT-2026-001",
            "cycle_id_text": "CYC-2026-001",
            "equipment_text": "AC01",
            "responsible_id": self.operacao_user.id,
        }
        vals.update(kwargs)
        return self.Recall.create(vals)

    def _content(self):
        return base64.b64encode(b"\xff bi-positive-content")

    def _create_bi_pos_file(self, **kwargs):
        vals = {
            "name": kwargs.pop("name", "BI+ %s" % uuid.uuid4().hex[:8]),
            "directory_id": self.directory.id,
            "content": self._content(),
            "document_type_id": self.dt_bi_pos.id,
        }
        vals.update(kwargs)
        return self.File.create(vals)

    # ---------- 1. happy path: full state machine ----------
    def test_recall_full_flow_to_closed(self):
        rec = self._make_recall()
        self.assertEqual(rec.state, "draft")
        self.assertTrue(rec.name and rec.name.startswith("REC/"))

        # decision: needs decision_text + decision_date
        with self.assertRaises(UserError):
            rec.action_take_decision()
        rec.write({
            "decision_text": "<p>Ciclo falhou no teste BD.</p>",
            "decision_date": fields.Datetime.now(),
        })
        rec.action_take_decision()
        self.assertEqual(rec.state, "decision")

        # notification: needs clients + notification text/date
        with self.assertRaises(UserError):
            rec.action_notify_clients()
        rec.write({
            "affected_clients_ids": [(4, self.partner_a.id)],
            "notification_text": "<p>Recall LOT-2026-001 emitido.</p>",
            "notification_date": fields.Datetime.now(),
        })
        rec.action_notify_clients()
        self.assertEqual(rec.state, "notification")

        # collection: needs start date
        with self.assertRaises(UserError):
            rec.action_start_collection()
        rec.write({"collection_start_date": fields.Date.today()})
        rec.action_start_collection()
        self.assertEqual(rec.state, "collection_in_progress")

        # complete collection -> disposal
        rec.action_complete_collection()
        self.assertEqual(rec.state, "disposal")

        # close: needs disposal text/date + closure_summary
        with self.assertRaises(UserError):
            rec.action_record_disposal()
        rec.write({
            "disposal_text": "<p>Material incinerado pela XPTO Ltda.</p>",
            "disposal_date": fields.Date.today(),
            "closure_summary": "<p>Recall encerrado eficaz.</p>",
        })
        rec.action_record_disposal()
        self.assertEqual(rec.state, "closed")
        self.assertTrue(rec.closure_date)

    # ---------- 2. guard: missing decision_text ----------
    def test_guard_decision_requires_text_and_date(self):
        rec = self._make_recall()
        # only date, no text
        rec.write({"decision_date": fields.Datetime.now()})
        with self.assertRaises(UserError):
            rec.action_take_decision()
        # only text, no date
        rec.write({"decision_date": False, "decision_text": "<p>x</p>"})
        with self.assertRaises(UserError):
            rec.action_take_decision()

    # ---------- 3. guard: notification requires clients ----------
    def test_guard_notification_requires_clients(self):
        rec = self._make_recall(
            decision_text="<p>ok</p>",
            decision_date=fields.Datetime.now(),
        )
        rec.action_take_decision()
        rec.write({
            "notification_text": "<p>aviso</p>",
            "notification_date": fields.Datetime.now(),
        })
        with self.assertRaises(UserError):
            rec.action_notify_clients()

    # ---------- 4. guard: close requires disposal_text + summary ----------
    def test_guard_close_requires_disposal_and_summary(self):
        rec = self._make_recall(
            decision_text="<p>ok</p>",
            decision_date=fields.Datetime.now(),
            affected_clients_ids=[(4, self.partner_a.id)],
            notification_text="<p>aviso</p>",
            notification_date=fields.Datetime.now(),
            collection_start_date=fields.Date.today(),
        )
        rec.action_take_decision()
        rec.action_notify_clients()
        rec.action_start_collection()
        rec.action_complete_collection()
        # missing disposal + summary
        with self.assertRaises(UserError):
            rec.action_record_disposal()
        # only disposal, no summary
        rec.write({
            "disposal_text": "<p>incinerado</p>",
            "disposal_date": fields.Date.today(),
        })
        with self.assertRaises(UserError):
            rec.action_record_disposal()

    # ---------- 5. cancel flow ----------
    def test_cancel_by_manager(self):
        rec = self._make_recall()
        # non-manager cannot cancel
        with self.assertRaises(UserError):
            rec.with_user(self.operacao_user).action_cancel()
        rec.with_user(self.manager).action_cancel()
        self.assertEqual(rec.state, "cancelled")
        self.assertTrue(rec.closure_date)
        # cannot cancel a closed recall
        rec2 = self._make_recall(
            decision_text="<p>ok</p>",
            decision_date=fields.Datetime.now(),
            affected_clients_ids=[(4, self.partner_a.id)],
            notification_text="<p>aviso</p>",
            notification_date=fields.Datetime.now(),
            collection_start_date=fields.Date.today(),
        )
        rec2.action_take_decision()
        rec2.action_notify_clients()
        rec2.action_start_collection()
        rec2.action_complete_collection()
        rec2.write({
            "disposal_text": "<p>x</p>",
            "disposal_date": fields.Date.today(),
            "closure_summary": "<p>ok</p>",
        })
        rec2.action_record_disposal()
        with self.assertRaises(UserError):
            rec2.with_user(self.manager).action_cancel()

    # ---------- 6. automation: BI+ file spawns recall ----------
    def test_auto_recall_on_bi_positive_file_create(self):
        f = self._create_bi_pos_file()
        recall = self.Recall.search(
            [("bi_positive_file_id", "=", f.id)], limit=1
        )
        self.assertTrue(recall, "Recall deve ser criado automaticamente")
        self.assertEqual(recall.trigger_type, "bi_positive")
        self.assertEqual(recall.severity, "critical")
        self.assertEqual(recall.state, "draft")
        self.assertIn(f, recall.attachment_ids)

    # ---------- 7. idempotency: duplicate file create -> no duplicate recall ----------
    def test_auto_recall_idempotent(self):
        f = self._create_bi_pos_file()
        first = self.Recall.search(
            [("bi_positive_file_id", "=", f.id)]
        )
        self.assertEqual(len(first), 1)
        # call the spawn again directly: should NOT create another
        f._spawn_recall_for_bi_positive()
        again = self.Recall.search(
            [("bi_positive_file_id", "=", f.id)]
        )
        self.assertEqual(len(again), 1, "Spawn deve ser idempotente")

    # ---------- 8. context flag skips auto-creation ----------
    def test_skip_recall_trigger_context_flag(self):
        f = self.File.with_context(
            afr_ecm_skip_recall_trigger=True,
        ).create({
            "name": "Skip BI+ %s" % uuid.uuid4().hex[:8],
            "directory_id": self.directory.id,
            "content": self._content(),
            "document_type_id": self.dt_bi_pos.id,
        })
        recall = self.Recall.search(
            [("bi_positive_file_id", "=", f.id)]
        )
        self.assertFalse(
            recall,
            "Flag afr_ecm_skip_recall_trigger deve impedir criação automática",
        )

    # ---------- 9. cron escalation posts activity ----------
    def test_cron_overdue_posts_escalation_activity(self):
        rec = self._make_recall(
            decision_text="<p>ok</p>",
            decision_date=fields.Datetime.now(),
        )
        rec.action_take_decision()
        # force write_date para o passado, simulando SLA estourado
        past = fields.Datetime.now() - timedelta(hours=48)
        self.env.cr.execute(
            "UPDATE afr_ecm_recall SET write_date=%s WHERE id=%s",
            (past, rec.id),
        )
        rec.invalidate_cache()
        self.Recall._cron_recall_overdue_alerts()
        rec.invalidate_cache()
        escalated = rec.activity_ids.filtered(
            lambda a: a.summary and a.summary.startswith("ESCALAÇÃO Recall")
        )
        self.assertTrue(escalated, "Cron deve agendar atividade de escalação")
