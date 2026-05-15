"""Regression — confidentiality record rules on dms.file.

Bug histórico (descoberto durante smoke test 2026-05-15):
    As três regras (`rule_dms_file_internal`, `rule_dms_file_restricted`,
    `rule_dms_file_confidential`) compartilhavam o mesmo grupo
    `group_ecm_user`. Odoo combina regras do MESMO grupo por OR.

    Domain `!=confidential` da regra `confidential` passa quando file é
    `restricted` (porque restricted != confidential), e domain `!=restricted`
    da regra `restricted` passa quando file é `confidential` — ambas as
    regras se sabotavam mutuamente, permitindo que qualquer ecm_user
    visse documentos confidenciais ou restritos criados por outros.

Comportamento esperado (corrigido em afr_ecm 16.0.2.0.1):
    - `public` → todos com base.group_user veem (regra separada)
    - `internal` → todos com group_ecm_user veem
    - `restricted` → só dono (`create_uid=user`) ou manager
    - `confidential` → só dono (`create_uid=user`) ou manager
    - `manager` (group_ecm_manager) → vê tudo
"""
import base64
import uuid

from odoo.tests.common import TransactionCase, new_test_user, tagged


def _content():
    return base64.b64encode(b"\xff confidentiality-regression")


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_confidentiality")
class TestConfidentialityRules(TransactionCase):
    """Tests que regras de confidencialidade NÃO se sabotam por OR-collapse."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.access_group = cls.env["dms.access.group"].create({
            "name": "Confidentiality Test Access " + uuid.uuid4().hex[:6],
            "perm_create": True,
            "perm_write": True,
            "perm_unlink": True,
            "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_user").id)],
        })
        cls.storage = cls.env["dms.storage"].create({
            "name": "Confidentiality Test Storage",
            "save_type": "database",
        })
        cls.root = cls.env["dms.directory"].create({
            "name": "Confidentiality Test Root",
            "is_root_directory": True,
            "storage_id": cls.storage.id,
            "group_ids": [(4, cls.access_group.id)],
        })

        # User A — dono dos arquivos. ecm_user padrão.
        cls.user_a = new_test_user(
            cls.env,
            login="conf_user_a_" + uuid.uuid4().hex[:6],
            groups="afr_ecm.group_ecm_user",
        )
        # User B — outro ecm_user, NÃO dono.
        cls.user_b = new_test_user(
            cls.env,
            login="conf_user_b_" + uuid.uuid4().hex[:6],
            groups="afr_ecm.group_ecm_user",
        )
        # Manager — vê tudo.
        cls.user_manager = new_test_user(
            cls.env,
            login="conf_manager_" + uuid.uuid4().hex[:6],
            groups="afr_ecm.group_ecm_manager",
        )

        # Arquivos criados pelo user_a, um por nível de confidencialidade.
        def _file(name, conf):
            return cls.env["dms.file"].with_user(cls.user_a).create({
                "name": name,
                "directory_id": cls.root.id,
                "content": _content(),
                "confidentiality": conf,
            })

        cls.f_public = _file("public.txt", "public")
        cls.f_internal = _file("internal.txt", "internal")
        cls.f_restricted = _file("restricted.txt", "restricted")
        cls.f_confidential = _file("confidential.txt", "confidential")

        cls.all_ids = [
            cls.f_public.id,
            cls.f_internal.id,
            cls.f_restricted.id,
            cls.f_confidential.id,
        ]

    def _visible(self, user):
        return self.env["dms.file"].with_user(user).search([
            ("id", "in", self.all_ids),
        ])

    # ---- User A (dono) ----

    def test_owner_sees_all_levels(self):
        """Dono vê todos os 4 níveis (próprios)."""
        files = self._visible(self.user_a)
        self.assertIn(self.f_public, files)
        self.assertIn(self.f_internal, files)
        self.assertIn(self.f_restricted, files)
        self.assertIn(self.f_confidential, files)

    # ---- User B (não dono) — coração da regressão ----

    def test_non_owner_sees_internal(self):
        """User B vê arquivos internal de qualquer dono."""
        files = self._visible(self.user_b)
        self.assertIn(
            self.f_internal, files,
            "internal deve ser visível para qualquer ecm_user."
        )

    def test_non_owner_does_NOT_see_restricted(self):
        """REGRESSÃO: User B NÃO vê arquivo restricted criado por outro.

        Antes do fix de 16.0.2.0.1, a regra `confidential` (!=confidential)
        permitia ver restricted alheio via OR-collapse.
        """
        files = self._visible(self.user_b)
        self.assertNotIn(
            self.f_restricted, files,
            "BUG OR-collapse: restricted de outro dono não pode vazar."
        )

    def test_non_owner_does_NOT_see_confidential(self):
        """REGRESSÃO: User B NÃO vê confidential criado por outro.

        Antes do fix, a regra `restricted` (!=restricted) permitia ver
        confidential alheio via OR-collapse.
        """
        files = self._visible(self.user_b)
        self.assertNotIn(
            self.f_confidential, files,
            "BUG OR-collapse: confidential de outro dono não pode vazar."
        )

    # ---- Manager ----

    def test_manager_sees_all(self):
        """Manager vê todos os 4 níveis independente do dono."""
        files = self._visible(self.user_manager)
        self.assertIn(self.f_public, files)
        self.assertIn(self.f_internal, files)
        self.assertIn(self.f_restricted, files)
        self.assertIn(self.f_confidential, files)
