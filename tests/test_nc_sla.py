# -*- coding: utf-8 -*-
"""Tests for F4.3.8 — NC 24h SLA escalation cron.

Tests the hourly check for NCs stuck in disposition >24h:
- Chatter posting with urgent message
- WARNING activity creation
- Idempotent escalation via sla_24h_escalated flag
- Reset on state transition away from disposition
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo import fields


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_nc_sla")
class TestNcSla(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Nc = cls.env["afr.ecm.nc"]

        cls.sgq_user = new_test_user(
            cls.env,
            login="sgq_user_sla_test",
            groups="afr_ecm.group_ecm_area_sgq",
        )
        cls.manager = new_test_user(
            cls.env,
            login="ecm_mgr_sla_test",
            groups="afr_ecm.group_ecm_manager",
        )

    def _make_nc(self, **kwargs):
        """Create an NC with defaults."""
        vals = {
            "title": "NC de teste SLA",
            "severity": "medium",
            "origin": "process",
            "responsible_id": self.sgq_user.id,
        }
        vals.update(kwargs)
        return self.Nc.create(vals)

    # ---------- Helper: move NC to disposition ----------
    def _nc_to_disposition(self, nc):
        """Move NC draft -> disposition and set disposition_entered_date."""
        nc.action_start_disposition()
        nc.disposition_entered_date = fields.Datetime.now()

    # ---------- Test 1: NC <24h ago → cron does NOT escalate ----------
    def test_nc_within_24h_not_escalated(self):
        nc = self._make_nc()
        nc.action_start_disposition()
        # Set disposition_entered_date to 12 hours ago
        nc.disposition_entered_date = fields.Datetime.now() - timedelta(hours=12)

        # Run cron
        self.Nc._cron_nc_sla_24h_check()
        nc.invalidate_cache()

        # Should NOT be escalated
        self.assertFalse(nc.sla_24h_escalated, "NC <24h should NOT be escalated")
        # No new warning activity should be created
        warning_activities = nc.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref('mail.mail_activity_data_warning')
        )
        self.assertEqual(
            len(warning_activities), 0,
            "No warning activity should be created for NC <24h"
        )

    # ---------- Test 2: NC stuck >24h → cron escalates ----------
    def test_nc_stuck_24h_plus_escalates(self):
        nc = self._make_nc()
        nc.action_start_disposition()
        # Set disposition_entered_date to 30 hours ago
        nc.disposition_entered_date = fields.Datetime.now() - timedelta(hours=30)

        # Pre-check: not escalated yet
        self.assertFalse(nc.sla_24h_escalated)

        # Run cron
        self.Nc._cron_nc_sla_24h_check()
        nc.invalidate_cache()

        # Should be escalated
        self.assertTrue(nc.sla_24h_escalated, "NC >24h should be escalated")

        # Chatter message should be posted
        messages = nc.message_ids.filtered(lambda m: m.message_type == 'comment')
        self.assertGreater(len(messages), 0, "Chatter message should be posted")
        # Message should contain SLA warning
        chatter_text = ' '.join([m.body for m in messages])
        self.assertIn('SLA', chatter_text.upper(), "Chatter should mention SLA")

        # WARNING activity should be created
        warning_activities = nc.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref('mail.mail_activity_data_warning')
        )
        self.assertGreater(
            len(warning_activities), 0,
            "WARNING activity should be created"
        )

    # ---------- Test 3: NC already escalated → cron does NOT re-escalate ----------
    def test_nc_already_escalated_not_re_escalated(self):
        nc = self._make_nc()
        nc.action_start_disposition()
        nc.disposition_entered_date = fields.Datetime.now() - timedelta(hours=30)

        # Run cron once
        self.Nc._cron_nc_sla_24h_check()
        nc.invalidate_cache()
        self.assertTrue(nc.sla_24h_escalated)

        # Count messages and activities after first escalation
        message_count_before = len(nc.message_ids)
        activity_count_before = len(nc.activity_ids)

        # Run cron again (should NOT re-escalate)
        self.Nc._cron_nc_sla_24h_check()
        nc.invalidate_cache()

        # Counts should NOT increase
        message_count_after = len(nc.message_ids)
        activity_count_after = len(nc.activity_ids)

        self.assertEqual(
            message_count_before, message_count_after,
            "No new chatter message on re-escalation"
        )
        # Activity count may not change (already created)
        # We mainly check sla_24h_escalated remains True
        self.assertTrue(nc.sla_24h_escalated, "Escalation flag should remain True")

    # ---------- Test 4: NC moved out of disposition → escalation flag reset ----------
    def test_nc_escalation_reset_on_state_change(self):
        nc = self._make_nc()
        nc.action_start_disposition()
        nc.disposition_entered_date = fields.Datetime.now() - timedelta(hours=30)

        # Escalate once
        self.Nc._cron_nc_sla_24h_check()
        nc.invalidate_cache()
        self.assertTrue(nc.sla_24h_escalated)
        self.assertTrue(nc.disposition_entered_date)

        # Move to investigation (completes disposition)
        nc.disposition_text = "<p>Isolado</p>"
        nc.disposition_date = fields.Datetime.now()
        nc.action_complete_disposition()

        nc.invalidate_cache()

        # Escalation flag should be reset
        self.assertFalse(
            nc.sla_24h_escalated,
            "Escalation flag should reset when leaving disposition"
        )
        self.assertFalse(
            nc.disposition_entered_date,
            "disposition_entered_date should reset"
        )
        self.assertEqual(nc.state, "investigation")

    # ---------- Test 5: Multiple NCs, only past SLA processed ----------
    def test_multiple_ncs_only_overdue_escalated(self):
        # NC1: <24h ago (should NOT escalate)
        nc1 = self._make_nc(title="NC1 — Within 24h")
        nc1.action_start_disposition()
        nc1.disposition_entered_date = fields.Datetime.now() - timedelta(hours=12)

        # NC2: >24h ago (should escalate)
        nc2 = self._make_nc(title="NC2 — Over 24h")
        nc2.action_start_disposition()
        nc2.disposition_entered_date = fields.Datetime.now() - timedelta(hours=36)

        # NC3: >24h ago but already escalated (should NOT re-escalate)
        nc3 = self._make_nc(title="NC3 — Already escalated")
        nc3.action_start_disposition()
        nc3.disposition_entered_date = fields.Datetime.now() - timedelta(hours=40)
        nc3.sla_24h_escalated = True

        # Run cron
        self.Nc._cron_nc_sla_24h_check()

        # Refresh all
        nc1.invalidate_cache()
        nc2.invalidate_cache()
        nc3.invalidate_cache()

        # Assertions
        self.assertFalse(nc1.sla_24h_escalated, "NC1 <24h should NOT be escalated")
        self.assertTrue(nc2.sla_24h_escalated, "NC2 >24h should be escalated")
        self.assertTrue(nc3.sla_24h_escalated, "NC3 should remain escalated (already was)")

        # Only NC2 should have new warning activity (or chatter from this run)
        nc2_warnings = nc2.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref('mail.mail_activity_data_warning')
        )
        self.assertGreater(len(nc2_warnings), 0, "NC2 should have warning activity")

    # ---------- Test 6: Cron handles error gracefully ----------
    def test_cron_error_handling(self):
        """Cron should log errors but continue processing other NCs."""
        nc1 = self._make_nc(title="NC — Good")
        nc1.action_start_disposition()
        nc1.disposition_entered_date = fields.Datetime.now() - timedelta(hours=30)

        nc2 = self._make_nc(title="NC — Will be deleted")
        nc2.action_start_disposition()
        nc2.disposition_entered_date = fields.Datetime.now() - timedelta(hours=30)

        # Delete nc2 to trigger error during processing
        nc2_id = nc2.id
        nc2.unlink()

        # Run cron - should not crash, should process nc1 successfully
        result = self.Nc._cron_nc_sla_24h_check()
        self.assertTrue(result, "Cron should return True despite errors")

        nc1.invalidate_cache()
        self.assertTrue(nc1.sla_24h_escalated, "NC1 should still be escalated")
