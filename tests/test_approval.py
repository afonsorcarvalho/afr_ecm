import base64
import uuid

from odoo.exceptions import UserError
from odoo.tests.common import Form, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "afr_ecm")
class TestApprovalWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.DocType = cls.env["afr.ecm.document.type"]
        cls.Level = cls.env["afr.ecm.approval.level"]
        cls.Action = cls.env["afr.ecm.approval.action"]
        cls.File = cls.env["dms.file"]

        cls.access_group = cls.env["dms.access.group"].create(
            {
                "name": "Test ECM Access",
                "perm_create": True,
                "perm_write": True,
                "perm_unlink": True,
                "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_user").id)],
            }
        )
        cls.storage = cls.env["dms.storage"].create(
            {"name": "Test Storage Approval", "save_type": "database"}
        )
        directory_form = Form(cls.env["dms.directory"])
        directory_form.name = uuid.uuid4().hex
        directory_form.is_root_directory = True
        directory_form.storage_id = cls.storage
        directory_form.group_ids.add(cls.access_group)
        cls.directory = directory_form.save()

        cls.alice = new_test_user(
            cls.env,
            login="alice_approver",
            groups="afr_ecm.group_ecm_user",
        )
        cls.bob = new_test_user(
            cls.env,
            login="bob_approver",
            groups="afr_ecm.group_ecm_user",
        )
        cls.outsider = new_test_user(
            cls.env,
            login="outsider_user",
            groups="afr_ecm.group_ecm_user",
        )
        cls.manager = new_test_user(
            cls.env,
            login="ecm_mgr_user",
            groups="afr_ecm.group_ecm_manager",
        )
        cls.admin = cls.env.ref("base.user_admin")

        # tipo opt-in com 2 níveis
        cls.dt_two_levels = cls.DocType.create(
            {
                "name": "Tipo Aprovação 2N",
                "code": "test_appr_2n",
                "requires_approval": True,
            }
        )
        cls.level1 = cls.Level.create(
            {
                "document_type_id": cls.dt_two_levels.id,
                "sequence": 10,
                "name": "Nível 1 (Alice)",
                "user_id": cls.alice.id,
                "consensus": "any",
            }
        )
        cls.level2 = cls.Level.create(
            {
                "document_type_id": cls.dt_two_levels.id,
                "sequence": 20,
                "name": "Nível 2 (Gestor)",
                "group_id": cls.env.ref("afr_ecm.group_ecm_manager").id,
                "consensus": "any",
            }
        )

        # tipo sem aprovação
        cls.dt_no_approval = cls.DocType.create(
            {
                "name": "Tipo Livre",
                "code": "test_appr_none",
                "requires_approval": False,
            }
        )

    # ---------- helpers ----------
    @classmethod
    def _content(cls):
        return base64.b64encode(b"\xff content")

    def _create_file(self, doc_type, name=None):
        return self.File.create(
            {
                "name": name or uuid.uuid4().hex,
                "directory_id": self.directory.id,
                "content": self._content(),
                "document_type_id": doc_type.id,
            }
        )

    # ---------- testes ----------
    def test_constraint_level_requires_approver(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.Level.create(
                {
                    "document_type_id": self.dt_two_levels.id,
                    "name": "Nível inválido",
                }
            )
            self.env.flush_all()

    def test_create_with_requires_approval_sets_draft(self):
        f = self._create_file(self.dt_two_levels)
        self.assertEqual(f.approval_state, "draft")

    def test_create_without_requires_approval_no_state(self):
        f = self._create_file(self.dt_no_approval)
        self.assertFalse(f.approval_state)

    def test_submit_no_levels_raises(self):
        dt = self.DocType.create(
            {"name": "Sem níveis", "code": "test_noniv", "requires_approval": True}
        )
        f = self._create_file(dt)
        with self.assertRaises(UserError):
            f.action_submit_for_approval()

    def test_full_flow_two_levels(self):
        f = self._create_file(self.dt_two_levels)
        f.action_submit_for_approval()
        self.assertEqual(f.approval_state, "pending")
        self.assertEqual(f.current_level_id, self.level1)

        # outsider não aprova
        with self.assertRaises(UserError):
            f.with_user(self.outsider).action_approve()

        # alice aprova nível 1 → avança
        f.with_user(self.alice).action_approve()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "pending")
        self.assertEqual(f.current_level_id, self.level2)

        # alice não pertence ao grupo manager
        with self.assertRaises(UserError):
            f.with_user(self.alice).action_approve()

        # manager aprova nível 2 → completa
        f.with_user(self.manager).action_approve()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "approved")
        self.assertFalse(f.current_level_id)

    def test_double_approve_same_level_raises(self):
        f = self._create_file(self.dt_two_levels)
        f.action_submit_for_approval()
        f.with_user(self.alice).action_approve()
        # alice já aprovou nível 1 (que avançou). Alice não está no manager.
        # criar tipo com mesmo aprovador no mesmo nível pra testar:
        dt = self.DocType.create(
            {"name": "DupAppr", "code": "test_dup", "requires_approval": True}
        )
        self.Level.create(
            {
                "document_type_id": dt.id,
                "name": "L1",
                "user_id": self.alice.id,
                "consensus": "any",
            }
        )
        f2 = self._create_file(dt)
        f2.action_submit_for_approval()
        f2.with_user(self.alice).action_approve()
        # já completou (any com 1 user). Tenta aprovar de novo:
        with self.assertRaises(UserError):
            f2.with_user(self.alice).action_approve()

    def test_consensus_all(self):
        dt = self.DocType.create(
            {"name": "Consenso", "code": "test_cons", "requires_approval": True}
        )
        # grupo com alice + bob, consensus all
        grp = self.env["res.groups"].create(
            {"name": "Grupo Aprovadores Teste", "users": [(6, 0, [self.alice.id, self.bob.id])]}
        )
        self.Level.create(
            {
                "document_type_id": dt.id,
                "name": "L1 todos",
                "group_id": grp.id,
                "consensus": "all",
            }
        )
        f = self._create_file(dt)
        f.action_submit_for_approval()
        f.with_user(self.alice).action_approve()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "pending", "ainda falta bob")
        f.with_user(self.bob).action_approve()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "approved")

    def test_reject_then_reopen(self):
        f = self._create_file(self.dt_two_levels)
        f.action_submit_for_approval()
        f.with_user(self.alice).action_reject()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "rejected")

        # outsider não pode reabrir
        with self.assertRaises(UserError):
            f.with_user(self.outsider).action_reopen()

        # autor (admin que criou) reabre
        f.action_reopen()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "draft")

    def test_write_blocked_when_approved(self):
        # tipo simples 1 nível aprovado por alice
        dt = self.DocType.create(
            {"name": "WriteBlock", "code": "test_wb", "requires_approval": True}
        )
        self.Level.create(
            {
                "document_type_id": dt.id,
                "name": "L1",
                "user_id": self.alice.id,
                "consensus": "any",
            }
        )
        f = self._create_file(dt)
        f.action_submit_for_approval()
        f.with_user(self.alice).action_approve()
        f.invalidate_cache()
        self.assertEqual(f.approval_state, "approved")

        # manager (não admin) tentando renomear → bloqueia
        with self.assertRaises(UserError):
            f.with_user(self.manager).write({"name": "novo nome"})

        # admin pode
        f.with_user(self.admin).write({"name": "novo nome admin"})
        self.assertEqual(f.name, "novo nome admin")

    def test_activity_created_on_submit(self):
        f = self._create_file(self.dt_two_levels)
        f.action_submit_for_approval()
        act_type = self.env.ref("afr_ecm.mail_activity_data_approval")
        activities = f.activity_ids.filtered(lambda a: a.activity_type_id == act_type)
        self.assertTrue(activities, "esperava activity criada")
        self.assertIn(self.alice, activities.mapped("user_id"))
