# -*- coding: utf-8 -*-
"""Tests for F4.3.4 — NC and CAPA workflow models.

Mirrors the existing test_approval.py pattern: top-level Odoo imports, tagged
TransactionCase. pytest will only collect, not execute (no Odoo env at
collection time, so the file must import cleanly).
"""
from datetime import date, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_nc_capa")
class TestNcCapa(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Nc = cls.env["afr.ecm.nc"]
        cls.Capa = cls.env["afr.ecm.capa"]

        cls.sgq_user = new_test_user(
            cls.env,
            login="sgq_user_test",
            groups="afr_ecm.group_ecm_area_sgq",
        )
        cls.manager = new_test_user(
            cls.env,
            login="ecm_mgr_nc_capa",
            groups="afr_ecm.group_ecm_manager",
        )
        cls.regular_user = new_test_user(
            cls.env,
            login="ecm_user_nc_capa",
            groups="afr_ecm.group_ecm_user",
        )

    # ---------- helpers ----------
    def _make_nc(self, **kwargs):
        vals = {
            "title": "NC de teste",
            "severity": "medium",
            "origin": "process",
            "responsible_id": self.sgq_user.id,
        }
        vals.update(kwargs)
        return self.Nc.create(vals)

    def _make_capa(self, nc=None, **kwargs):
        vals = {
            "title": "CAPA de teste",
            "type": "corrective",
            "responsible_id": self.sgq_user.id,
        }
        if nc:
            vals["nc_id"] = nc.id
        vals.update(kwargs)
        return self.Capa.create(vals)

    # ---------- NC: sequence ----------
    def test_nc_sequence_assigned(self):
        nc = self._make_nc()
        self.assertTrue(nc.name, "NC.name deve ser preenchido por sequência")
        self.assertNotEqual(nc.name, "Novo")
        self.assertTrue(nc.name.startswith("NC/"))

    # ---------- NC: state machine happy path ----------
    def test_nc_full_flow_to_closed(self):
        nc = self._make_nc()
        self.assertEqual(nc.state, "draft")
        nc.action_start_disposition()
        self.assertEqual(nc.state, "disposition")

        # não pode avançar sem disposition_text
        with self.assertRaises(UserError):
            nc.action_complete_disposition()

        nc.write({
            "disposition_text": "<p>Lote isolado</p>",
            "disposition_date": "2026-05-13 10:00:00",
        })
        nc.action_complete_disposition()
        self.assertEqual(nc.state, "investigation")

        # não pode avançar sem root_cause_text
        with self.assertRaises(UserError):
            nc.action_complete_investigation()

        nc.root_cause_text = "<p>Falha no procedimento X</p>"
        nc.action_complete_investigation()
        self.assertEqual(nc.state, "decision_capa")

        # encerrar sem CAPA exige justificativa
        with self.assertRaises(UserError):
            nc.action_close_no_capa()

        nc.closure_reason = "<p>Risco baixo, ação imediata suficiente</p>"
        nc.action_close_no_capa()
        self.assertEqual(nc.state, "closed")
        self.assertTrue(nc.closure_date)

    # ---------- NC: escalation creates CAPA ----------
    def test_nc_escalation_creates_capa(self):
        nc = self._make_nc(severity="critical")
        nc.action_start_disposition()
        nc.write({
            "disposition_text": "<p>Lote bloqueado</p>",
            "disposition_date": "2026-05-13 10:00:00",
        })
        nc.action_complete_disposition()
        nc.root_cause_text = "<p>Causa identificada</p>"
        nc.action_complete_investigation()
        self.assertEqual(nc.state, "decision_capa")
        nc.action_open_capa()
        self.assertEqual(nc.state, "escalated_to_capa")
        self.assertTrue(nc.capa_id)
        self.assertEqual(nc.capa_id.nc_id, nc)
        self.assertTrue(nc.capa_id.name.startswith("CAPA/"))

    def test_nc_reopen_requires_manager(self):
        nc = self._make_nc()
        nc.action_start_disposition()
        with self.assertRaises(UserError):
            nc.with_user(self.regular_user).action_reopen()
        nc.with_user(self.manager).action_reopen()
        self.assertEqual(nc.state, "draft")

    def test_nc_cannot_advance_from_wrong_state(self):
        nc = self._make_nc()
        with self.assertRaises(UserError):
            nc.action_complete_disposition()
        with self.assertRaises(UserError):
            nc.action_complete_investigation()
        with self.assertRaises(UserError):
            nc.action_open_capa()
        with self.assertRaises(UserError):
            nc.action_close_no_capa()

    # ---------- CAPA: sequence ----------
    def test_capa_sequence_assigned(self):
        capa = self._make_capa()
        self.assertTrue(capa.name.startswith("CAPA/"))

    # ---------- CAPA: state machine and verification ----------
    def test_capa_happy_path_30_60_90_effective(self):
        capa = self._make_capa()
        capa.action_start_analysis()
        self.assertEqual(capa.state, "analysis")

        with self.assertRaises(UserError):
            capa.action_approve_plan()  # falta risk_analysis

        capa.risk_analysis = "<p>Risco médio</p>"
        capa.action_approve_plan()
        self.assertEqual(capa.state, "plan")

        with self.assertRaises(UserError):
            capa.action_mark_implemented()  # falta action_plan

        capa.action_plan = "<p>Treinamento + revisão de PO</p>"
        capa.action_mark_implemented()
        self.assertEqual(capa.state, "verify_30d")
        self.assertTrue(capa.implementation_date)
        self.assertEqual(
            capa.verify_30d_due_date,
            capa.implementation_date + timedelta(days=30),
        )
        self.assertEqual(
            capa.verify_60d_due_date,
            capa.implementation_date + timedelta(days=60),
        )
        self.assertEqual(
            capa.verify_90d_due_date,
            capa.implementation_date + timedelta(days=90),
        )

        # 30d sem result → bloqueia
        with self.assertRaises(UserError):
            capa.action_verify_30d()

        capa.write({
            "verify_30d_result": "<p>Sem reincidência</p>",
            "verify_30d_effective": True,
        })
        capa.action_verify_30d()
        self.assertEqual(capa.state, "verify_60d")

        capa.write({
            "verify_60d_result": "<p>OK 60d</p>",
            "verify_60d_effective": True,
        })
        capa.action_verify_60d()
        self.assertEqual(capa.state, "verify_90d")

        capa.write({
            "verify_90d_result": "<p>OK 90d</p>",
            "verify_90d_effective": True,
        })
        capa.action_verify_90d()
        self.assertEqual(capa.state, "verify_90d")  # aguarda close manual

        capa.action_close_effective()
        self.assertEqual(capa.state, "closed_effective")
        self.assertEqual(capa.closure_decision, "effective")
        self.assertTrue(capa.closure_date)

    def test_capa_ineffective_at_30d_goes_reopened(self):
        capa = self._make_capa()
        capa.action_start_analysis()
        capa.risk_analysis = "<p>Risco</p>"
        capa.action_approve_plan()
        capa.action_plan = "<p>Plano</p>"
        capa.action_mark_implemented()
        capa.write({
            "verify_30d_result": "<p>Reincidiu</p>",
            "verify_30d_effective": False,
        })
        capa.action_verify_30d()
        self.assertEqual(capa.state, "reopened")

    def test_capa_close_ineffective_from_reopened(self):
        capa = self._make_capa()
        capa.action_start_analysis()
        capa.risk_analysis = "<p>R</p>"
        capa.action_approve_plan()
        capa.action_plan = "<p>P</p>"
        capa.action_mark_implemented()
        capa.write({
            "verify_30d_result": "<p>X</p>",
            "verify_30d_effective": False,
        })
        capa.action_verify_30d()
        self.assertEqual(capa.state, "reopened")
        capa.action_close_ineffective()
        self.assertEqual(capa.state, "closed_ineffective")
        self.assertEqual(capa.closure_decision, "ineffective")

    def test_capa_close_effective_only_after_60d(self):
        capa = self._make_capa()
        capa.action_start_analysis()
        capa.risk_analysis = "<p>R</p>"
        capa.action_approve_plan()
        capa.action_plan = "<p>P</p>"
        capa.action_mark_implemented()
        # ainda em verify_30d → fechar como Eficaz não permitido
        with self.assertRaises(UserError):
            capa.action_close_effective()

    def test_capa_reopen_requires_manager(self):
        capa = self._make_capa()
        capa.action_start_analysis()
        with self.assertRaises(UserError):
            capa.with_user(self.regular_user).action_reopen()
        capa.with_user(self.manager).action_reopen()
        self.assertEqual(capa.state, "reopened")

    # ---------- CAPA cron ----------
    def test_capa_cron_overdue_creates_activity(self):
        capa = self._make_capa()
        capa.action_start_analysis()
        capa.risk_analysis = "<p>R</p>"
        capa.action_approve_plan()
        capa.action_plan = "<p>P</p>"
        capa.action_mark_implemented()
        # força implementation_date para 40 dias atrás → 30d vencido
        capa.implementation_date = date.today() - timedelta(days=40)
        # recompute
        capa._compute_verify_due_dates()
        before = len(capa.activity_ids)
        self.Capa._cron_capa_verification_reminders()
        capa.invalidate_cache()
        after = len(capa.activity_ids)
        self.assertGreater(
            after, before,
            "cron deve criar atividade de escalação para verificação 30d vencida",
        )

    # ---------- NC constraint: escalated requires capa ----------
    def test_nc_escalated_requires_capa(self):
        nc = self._make_nc()
        with self.assertRaises(ValidationError):
            nc.write({"state": "escalated_to_capa", "capa_id": False})
