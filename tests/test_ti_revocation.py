"""F4.3.3 — Unit tests for TI access revocation on employee off-boarding.

Tests:
  1. Setting active employee inactive creates TI_ACC_REV draft file + activity + chatter.
  2. Created file's directory matches the expected anchor folder.
  3. Cron escalates a 5h-old draft (injected time via `now=` param).
  4. Cron does NOT escalate a completed/closed revocation (approved state).
  5. Cron does NOT re-escalate if sentinel already present in messages.
"""
import base64
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests.common import Form, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "afr_ecm")
class TestTiRevocation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.Employee = cls.env['hr.employee']
        cls.DocType = cls.env['afr.ecm.document.type']
        cls.File = cls.env['dms.file']
        cls.Directory = cls.env['dms.directory']

        # --- DMS Storage + directory ---
        cls.access_group = cls.env['dms.access.group'].create({
            'name': 'TI Rev Test ACL',
            'perm_create': True,
            'perm_write': True,
            'perm_unlink': True,
            'group_ids': [(4, cls.env.ref('afr_ecm.group_ecm_user').id)],
        })
        cls.storage = cls.env['dms.storage'].create({
            'name': 'TI Rev Test Storage',
            'save_type': 'database',
        })
        dir_form = Form(cls.env['dms.directory'])
        dir_form.name = 'TI_Rev_Root_' + uuid.uuid4().hex[:6]
        dir_form.is_root_directory = True
        dir_form.storage_id = cls.storage
        dir_form.group_ids.add(cls.access_group)
        cls.root_dir = dir_form.save()

        # Anchor sub-directory (simulates 05_Gestao_Acessos)
        cls.anchor_dir = cls.env['dms.directory'].create({
            'name': '05_Gestao_Acessos',
            'parent_id': cls.root_dir.id,
            'storage_id': cls.storage.id,
        })

        # --- Document type TI_ACC_REV (may or may not exist in DB) ---
        cls.doc_type_rev = cls.env['afr.ecm.document.type'].sudo().search(
            [('code', '=', 'TI_ACC_REV')], limit=1
        )
        if not cls.doc_type_rev:
            cls.doc_type_rev = cls.env['afr.ecm.document.type'].sudo().create({
                'name': 'Revogação de Acesso TI',
                'code': 'TI_ACC_REV',
                'requires_approval': False,
                'default_directory_id': cls.anchor_dir.id,
            })
        else:
            # Point to our test directory so the file lands somewhere accessible
            cls.doc_type_rev.sudo().write({
                'default_directory_id': cls.anchor_dir.id,
            })

        # --- A manager user (fallback recipient) ---
        cls.manager = new_test_user(
            cls.env,
            login='ti_rev_manager_' + uuid.uuid4().hex[:4],
            groups='afr_ecm.group_ecm_manager',
        )

        # --- Test employee ---
        cls.employee = cls.env['hr.employee'].sudo().create({
            'name': 'Funcionário Teste Revogação',
            'active': True,
        })

    # ------------------------------------------------------------------ helpers

    def _deactivate_employee(self, employee=None):
        """Set employee active=False via write(), triggering the hook."""
        emp = employee or self.employee
        emp.sudo().write({'active': False})
        return emp

    def _get_revocation_files(self, employee=None):
        emp = employee or self.employee
        return self.File.sudo().search([
            ('document_type_id', '=', self.doc_type_rev.id),
            ('name', 'like', 'REV_'),
        ])

    # ------------------------------------------------------------------ tests

    def test_01_deactivation_creates_revocation_file_and_activity(self):
        """Setting active→False creates TI_ACC_REV draft file + mail.activity."""
        emp = self.env['hr.employee'].sudo().create({
            'name': 'Emp Desativação ' + uuid.uuid4().hex[:4],
            'active': True,
        })

        files_before = self.File.sudo().search([
            ('document_type_id', '=', self.doc_type_rev.id),
        ])

        self._deactivate_employee(emp)

        files_after = self.File.sudo().search([
            ('document_type_id', '=', self.doc_type_rev.id),
        ])
        new_files = files_after - files_before
        self.assertTrue(new_files, "Esperava ao menos 1 arquivo TI_ACC_REV criado")

        rev_file = new_files[0]

        # File name matches pattern REV_*
        self.assertTrue(
            rev_file.name.startswith('REV_'),
            "Nome do arquivo deve começar com REV_",
        )

        # Content is non-empty and base64-decodable with checklist content
        content_bytes = base64.b64decode(rev_file.content)
        self.assertIn(b'Checklist de Revoga', content_bytes)

        # mail.activity created on the file
        act_type = self.env.ref('mail.mail_activity_data_warning', raise_if_not_found=False)
        self.assertTrue(act_type, "mail.mail_activity_data_warning deve existir")
        activities = rev_file.sudo().activity_ids.filtered(
            lambda a: a.activity_type_id == act_type
        )
        self.assertTrue(
            activities,
            "Esperava mail.activity de revogação no arquivo TI_ACC_REV",
        )
        # Deadline = today (same-day)
        self.assertEqual(
            activities[0].date_deadline,
            fields.Date.today(),
            "Deadline da activity deve ser hoje",
        )

        # Employee record should have a chatter note about TI compliance
        employee_msgs = emp.sudo().message_ids
        self.assertTrue(
            any('Conformidade TI' in (m.body or '') for m in employee_msgs),
            "Esperava nota de conformidade TI no chatter do funcionário",
        )

    def test_02_revocation_file_in_correct_directory(self):
        """Created revocation file must land in the anchor directory."""
        emp = self.env['hr.employee'].sudo().create({
            'name': 'Emp Dir Test ' + uuid.uuid4().hex[:4],
            'active': True,
        })

        files_before = self.File.sudo().search([
            ('document_type_id', '=', self.doc_type_rev.id),
        ])
        self._deactivate_employee(emp)
        files_after = self.File.sudo().search([
            ('document_type_id', '=', self.doc_type_rev.id),
        ])
        new_files = files_after - files_before
        self.assertTrue(new_files, "Nenhum arquivo de revogação criado")

        rev_file = new_files[0]
        self.assertEqual(
            rev_file.directory_id.id,
            self.anchor_dir.id,
            "Arquivo deve estar no diretório âncora 05_Gestao_Acessos",
        )

    def test_03_cron_escalates_old_drafts(self):
        """Revocation in draft for >4h triggers escalation chatter message."""
        # Create a TI_ACC_REV file manually, simulating a 5h-old record
        rev_file = self.File.sudo().create({
            'name': 'REV_TEST_CRON_' + uuid.uuid4().hex[:6] + '.md',
            'directory_id': self.anchor_dir.id,
            'content': base64.b64encode(b'# checklist'),
            'document_type_id': self.doc_type_rev.id,
        })

        # Simulate the file being 5 hours old
        five_hours_ago = fields.Datetime.now() - timedelta(hours=5)
        self.env.cr.execute(
            "UPDATE dms_file SET create_date = %s WHERE id = %s",
            (five_hours_ago, rev_file.id),
        )
        rev_file.invalidate_recordset()

        # Run cron with injected 'now' so cutoff = now - 4h lands before create_date
        escalated = self.File._cron_pending_revocations_escalate(
            now=fields.Datetime.now()
        )

        self.assertGreaterEqual(escalated, 1, "Esperava ao menos 1 escalação")

        # Verify sentinel is present in messages
        from ..models.dms_file_revocation_cron import _ESCALATION_SENTINEL
        msg_bodies = [m.body for m in rev_file.sudo().message_ids]
        self.assertTrue(
            any(_ESCALATION_SENTINEL in (body or '') for body in msg_bodies),
            "Sentinel de escalação não encontrado nas mensagens do arquivo",
        )

    def test_04_cron_skips_approved_revocations(self):
        """Approved revocation file must NOT be escalated by cron."""
        rev_file = self.File.sudo().create({
            'name': 'REV_APPROVED_' + uuid.uuid4().hex[:6] + '.md',
            'directory_id': self.anchor_dir.id,
            'content': base64.b64encode(b'# checklist done'),
            'document_type_id': self.doc_type_rev.id,
            'approval_state': 'approved',
        })

        # Make it 6 hours old
        six_hours_ago = fields.Datetime.now() - timedelta(hours=6)
        self.env.cr.execute(
            "UPDATE dms_file SET create_date = %s WHERE id = %s",
            (six_hours_ago, rev_file.id),
        )
        rev_file.invalidate_recordset()

        msgs_before = len(rev_file.sudo().message_ids)
        self.File._cron_pending_revocations_escalate(now=fields.Datetime.now())
        msgs_after = len(rev_file.sudo().message_ids)

        self.assertEqual(
            msgs_before,
            msgs_after,
            "Arquivo aprovado não deve receber mensagem de escalação",
        )

    def test_05_cron_does_not_double_escalate(self):
        """Cron must not escalate the same draft file twice."""
        rev_file = self.File.sudo().create({
            'name': 'REV_DEDUP_' + uuid.uuid4().hex[:6] + '.md',
            'directory_id': self.anchor_dir.id,
            'content': base64.b64encode(b'# checklist dedup'),
            'document_type_id': self.doc_type_rev.id,
        })

        five_hours_ago = fields.Datetime.now() - timedelta(hours=5)
        self.env.cr.execute(
            "UPDATE dms_file SET create_date = %s WHERE id = %s",
            (five_hours_ago, rev_file.id),
        )
        rev_file.invalidate_recordset()

        now = fields.Datetime.now()

        # First run — should escalate
        first = self.File._cron_pending_revocations_escalate(now=now)
        self.assertGreaterEqual(first, 1)

        msgs_after_first = len(rev_file.sudo().message_ids)

        # Second run — sentinel already present, should NOT escalate again
        second = self.File._cron_pending_revocations_escalate(now=now)

        msgs_after_second = len(rev_file.sudo().message_ids)
        self.assertEqual(
            msgs_after_first,
            msgs_after_second,
            "Segunda passagem do cron não deve adicionar mensagem de escalação",
        )
        # The second run result count for THIS file should be 0 (may have other files)
        _ = second  # result is aggregate; dedup already verified via message count
