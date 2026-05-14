"""Unit tests for F4.3.7 — monthly cycle summary cron.

Tests use mock objects and date patching to control time without freezegun.
The test class skips cleanly when ``afr_supervisorio_ciclos`` is not installed.

Run with:
    ./odoo-bin -d <db> --test-enable --stop-after-init -u afr_ecm

Coverage:
    1. Cron creates one summary file per equipment when cycles exist.
    2. Cron is idempotent (second run does NOT duplicate files).
    3. Cron skips gracefully when the destination folder is missing.
    4. Cron skips gracefully when the cycle model is not in the registry.
    5. Summary file name follows pattern CYCLES_SUMMARY_<EQ>_<YYYY-MM>.html.
    6. Summary file expiration_date is set to today + 5 years.
    7. HTML content contains expected headings and period.
    8. No cycles in the month → no files created.
"""
import base64
import unittest
import uuid
import logging
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from dateutil.relativedelta import relativedelta

from odoo.tests import TransactionCase, tagged
from odoo.tests.common import Form

_logger = logging.getLogger(__name__)


@tagged("afr_ecm", "f4_3_7", "cycle_summary", "-standard", "-at_install")
class TestCycleSummary(TransactionCase):
    """Tests for DmsFileCycleSummary._cron_monthly_cycle_summary."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Skip all tests when afr_supervisorio_ciclos is not installed.
        if cls.env.get("afr.supervisorio.ciclos") is None:
            raise unittest.SkipTest(
                "afr_supervisorio_ciclos not installed — skipping F4.3.7 tests."
            )

    def setUp(self):
        super().setUp()
        # Re-check at instance level in case module was deactivated mid-run.
        if self.env.get("afr.supervisorio.ciclos") is None:
            raise unittest.SkipTest(
                "afr_supervisorio_ciclos not installed — skipping F4.3.7 tests."
            )

        # ── DMS Storage ───────────────────────────────────────────────────────
        access_group = self.env["dms.access.group"].create({
            "name": "Cycle Test ACL " + uuid.uuid4().hex[:4],
            "perm_create": True,
            "perm_write": True,
            "perm_unlink": True,
            "group_ids": [(4, self.env.ref("afr_ecm.group_ecm_user").id)],
        })
        self.storage = self.env["dms.storage"].create({
            "name": "Cycle Summary Test Storage " + uuid.uuid4().hex[:4],
            "save_type": "database",
        })

        # Root directory (OCA dms requires Form + is_root_directory=True for root).
        root_form = Form(self.env["dms.directory"])
        root_form.name = "CycleRoot_" + uuid.uuid4().hex[:6]
        root_form.is_root_directory = True
        root_form.storage_id = self.storage
        root_form.group_ids.add(access_group)
        self.root_dir = root_form.save()

        # Destination folder (child of root — simulates Resumos_Ciclos).
        self.dest_folder = self.env["dms.directory"].create({
            "name": "Resumos_Ciclos",
            "parent_id": self.root_dir.id,
            "storage_id": self.storage.id,
        })

        # ── Equipment mock ─────────────────────────────────────────────────────
        # engc.equipment lives in engc_os. We mock it at the Python level to
        # avoid a hard dependency from the test suite.
        self._equipment_mock = MagicMock()
        self._equipment_mock.id = 9901
        self._equipment_mock.name = "Autoclave Teste 01"
        self._equipment_mock.apelido = "AUTO01"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _make_fake_cycle(self, state="concluido", ib_resultado=None,
                         duration=1.5, batch_number="LOTE-001",
                         start_date=None):
        """Return a minimal mock resembling an afr.supervisorio.ciclos record."""
        cycle = MagicMock()
        cycle.id = id(cycle)
        cycle.name = f"CICLO_{id(cycle)}"
        cycle.equipment_id = self._equipment_mock
        cycle.state = state
        cycle.ib_resultado = ib_resultado
        cycle.duration = duration
        cycle.batch_number = batch_number
        cycle.start_date = start_date or datetime(2026, 4, 15, 9, 0, 0)
        return cycle

    def _patch_cycle_model(self, cycles):
        """Context manager: patch env.get('afr.supervisorio.ciclos') with fake model.

        Captures the original env.__class__.get before patching so that calls
        for other models are forwarded correctly without recursion.
        """
        original_get = self.env.__class__.get

        fake_model = MagicMock()
        fake_model.sudo.return_value = fake_model
        fake_model.search.return_value = cycles

        return patch.object(
            self.env.__class__,
            "get",
            side_effect=lambda name, *a, **kw: fake_model
            if name == "afr.supervisorio.ciclos"
            else original_get(self.env, name, *a, **kw),
        )

    def _patch_dest_folder(self, folder):
        """Context manager: patch _cycle_summary_destination_folder."""
        return patch.object(
            self.env["dms.file"].__class__,
            "_cycle_summary_destination_folder",
            return_value=folder,
        )

    def _freeze_today(self, today_date):
        """Context manager: patch fields.Date.today() to a fixed date."""
        return patch("odoo.fields.Date.today", return_value=today_date)

    # ── Test 1: creates one file per equipment ────────────────────────────────

    def test_creates_one_file_per_equipment(self):
        """Cron must create exactly one dms.file per equipment with cycles."""
        today = date(2026, 5, 2)
        cycles = [
            self._make_fake_cycle(state="concluido"),
            self._make_fake_cycle(state="erro"),
        ]

        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with self._patch_cycle_model(cycles):
                    created = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(len(created), 1, "Expected exactly one file created.")
        created_file = self.env["dms.file"].sudo().browse(created[0])
        self.assertTrue(created_file.exists(), "Created file must exist in DB.")

    # ── Test 2: idempotency ───────────────────────────────────────────────────

    def test_idempotent_no_duplicate(self):
        """Running the cron twice must not create duplicate files."""
        today = date(2026, 5, 2)
        cycles = [self._make_fake_cycle(state="concluido")]

        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with self._patch_cycle_model(cycles):
                    created_first = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(len(created_first), 1, "First run must create one file.")

        # Second run — must detect the existing file and skip.
        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with self._patch_cycle_model(cycles):
                    created_second = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(
            len(created_second), 0,
            "Cron must not create a duplicate file on second run.",
        )

    # ── Test 3: skips when destination folder missing ─────────────────────────

    def test_skips_when_folder_missing(self):
        """Cron must return [] and log warning when destination folder absent."""
        today = date(2026, 5, 2)
        cycles = [self._make_fake_cycle(state="concluido")]
        # Empty recordset to simulate missing folder.
        empty_folder = self.env["dms.directory"].sudo().browse([])

        with self._freeze_today(today):
            with self._patch_dest_folder(empty_folder):
                with self._patch_cycle_model(cycles):
                    with self.assertLogs(
                        "odoo.addons.afr_ecm.models.dms_file_cycle_summary",
                        level="WARNING",
                    ) as cm:
                        result = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(result, [], "Must return [] when folder not found.")
        self.assertTrue(
            any("destination folder not found" in msg for msg in cm.output),
            "Expected 'destination folder not found' warning in logs.",
        )

    # ── Test 4: skips when cycle model not in registry ────────────────────────

    def test_skips_when_cycle_model_missing(self):
        """Cron must return [] and log warning when afr.supervisorio.ciclos absent."""
        today = date(2026, 5, 2)
        original_get = self.env.__class__.get

        with self._freeze_today(today):
            with patch.object(
                self.env.__class__,
                "get",
                side_effect=lambda name, *a, **kw: None
                if name == "afr.supervisorio.ciclos"
                else original_get(self.env, name, *a, **kw),
            ):
                with self.assertLogs(
                    "odoo.addons.afr_ecm.models.dms_file_cycle_summary",
                    level="WARNING",
                ) as cm:
                    result = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(result, [], "Must return [] when cycle model absent.")
        self.assertTrue(
            any("not in registry" in msg for msg in cm.output),
            "Expected 'not in registry' warning in logs.",
        )

    # ── Test 5: file name pattern ─────────────────────────────────────────────

    def test_file_name_pattern(self):
        """Created file must match CYCLES_SUMMARY_<EQ>_<YYYY-MM>.html pattern."""
        today = date(2026, 5, 2)
        cycles = [self._make_fake_cycle(state="concluido")]

        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with self._patch_cycle_model(cycles):
                    created = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(len(created), 1)
        f = self.env["dms.file"].sudo().browse(created[0])
        # Equipment slug for apelido="AUTO01" → "AUTO01"
        self.assertRegex(
            f.name,
            r"^CYCLES_SUMMARY_[A-Z0-9_]+_2026-04\.html$",
            "File name must match CYCLES_SUMMARY_<EQ>_YYYY-MM.html pattern.",
        )
        self.assertTrue(
            f.name.endswith("_2026-04.html"),
            "Year-month suffix must be _2026-04 when today=2026-05-02.",
        )

    # ── Test 6: expiration date = today + 5 years ─────────────────────────────

    def test_expiration_date_five_years(self):
        """Created file must have expiration_date set to today + 5 years."""
        today = date(2026, 5, 2)
        expected_expiry = today + relativedelta(years=5)
        cycles = [self._make_fake_cycle(state="concluido")]

        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with self._patch_cycle_model(cycles):
                    created = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(len(created), 1)
        f = self.env["dms.file"].sudo().browse(created[0])
        self.assertEqual(
            f.expiration_date,
            expected_expiry,
            f"expiration_date must be {expected_expiry}.",
        )

    # ── Test 7: HTML content sanity ───────────────────────────────────────────

    def test_html_content_has_summary_title(self):
        """Created file content must contain expected HTML headings and period."""
        today = date(2026, 5, 2)
        cycles = [
            self._make_fake_cycle(state="concluido", ib_resultado="negativo"),
            self._make_fake_cycle(state="erro"),
        ]

        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with self._patch_cycle_model(cycles):
                    created = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(len(created), 1)
        f = self.env["dms.file"].sudo().browse(created[0])
        raw_content = base64.b64decode(f.content).decode("utf-8")
        self.assertIn("Sumário Mensal de Ciclos", raw_content)
        self.assertIn("2026-04", raw_content)

    # ── Test 8: no cycles → no files created ─────────────────────────────────

    def test_no_cycles_no_files(self):
        """When there are no cycles in the target month, no files are created."""
        today = date(2026, 5, 2)
        original_get = self.env.__class__.get

        fake_model = MagicMock()
        fake_model.sudo.return_value = fake_model
        fake_model.search.return_value = []

        with self._freeze_today(today):
            with self._patch_dest_folder(self.dest_folder):
                with patch.object(
                    self.env.__class__,
                    "get",
                    side_effect=lambda name, *a, **kw: fake_model
                    if name == "afr.supervisorio.ciclos"
                    else original_get(self.env, name, *a, **kw),
                ):
                    created = self.env["dms.file"]._cron_monthly_cycle_summary()

        self.assertEqual(
            created, [],
            "No files should be created when no cycles exist in the target month.",
        )
