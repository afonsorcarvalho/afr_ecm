"""Tests for F4.3.9 — record rule refinements.

Coverage:
  Part A (rule_ecm_rh_funcionario_own):
    - RH funcionário vê arquivos em 40_RH/Documentos/ (políticas coletivas)
    - RH funcionário vê arquivos na pasta com seu nome em 40_RH/Registros/
    - RH funcionário NÃO vê pasta de colega
    - RH funcionário sem employee vinculado não vê nada em 40_RH/Registros/

  Part B (rule_ecm_auditor_externo_readonly):
    - Auditor vê arquivos em diretórios do seu escopo ativo
    - Auditor NÃO vê arquivos fora do escopo
    - Auditor sem escopo não vê nada
    - Escopo inativo não concede acesso
    - Escopo futuro não concede acesso
    - Escopo expirado é arquivado pelo cron e auditor perde acesso
    - Helper _check_audit_scope_active() funciona corretamente
    - Constraint de datas inválidas levanta ValidationError

LIMITATION (escrita não bloqueada nesta fase):
  As record rules com perm_write=False, perm_create=False, perm_unlink=False
  significam que a rule NÃO SE APLICA a essas operações — não que elas sejam
  negadas. O bloqueio real de escrita requer ou:
    (a) uma dms.access.group restritivo (perm_write=False) por área, ou
    (b) uma regra global de deny (global rule com perm_write=True que retorna
        domínio vazio para esses grupos).
  Isso será implementado em F4.3.10 junto com o manifest patch.

NOTE: Este arquivo testa o modelo afr.ecm.audit.scope definido em
models/audit_scope.py. Esse modelo só é registrado no Odoo após a
integração descrita em MANIFEST_PATCH_F4_3_9.md. Os testes podem ser
coletados (--collect-only) sem o modelo instalado, mas falharão em run
até a aplicação do patch.
"""
import base64
import uuid
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


def _dummy_content():
    return base64.b64encode(b"\xff test-content-f439")


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_f439")
class TestRhFuncionarioRule(TransactionCase):
    """Part A — rule_ecm_rh_funcionario_own.

    Verifica que o domínio da rule filtra corretamente os arquivos visíveis
    por nome de pasta (heurística employee.name em complete_name).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # dms.access.group permissivo para setup de dados (admin usa sudo)
        cls.access_group = cls.env["dms.access.group"].create({
            "name": "F439 RH Test Access",
            "perm_create": True,
            "perm_write": True,
            "perm_unlink": True,
            "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_user").id)],
        })
        cls.storage = cls.env["dms.storage"].create({
            "name": "F439 RH Storage",
            "save_type": "database",
        })

        # Hierarquia de pastas replicando a taxonomia real:
        # 40_RH/ > Documentos/
        # 40_RH/ > Registros/ > 01_Funcionarios_Ativos/ > MATRICULA_1_Ana_Silva/
        # 40_RH/ > Registros/ > 01_Funcionarios_Ativos/ > MATRICULA_2_Carlos_Souza/
        def _make_root(name):
            return cls.env["dms.directory"].create({
                "name": name,
                "is_root_directory": True,
                "storage_id": cls.storage.id,
                "group_ids": [(4, cls.access_group.id)],
            })

        def _make_child(name, parent):
            return cls.env["dms.directory"].create({
                "name": name,
                "parent_id": parent.id,
                "group_ids": [(4, cls.access_group.id)],
            })

        cls.dir_40_rh = _make_root("40_RH")
        cls.dir_documentos = _make_child("Documentos", cls.dir_40_rh)
        cls.dir_registros = _make_child("Registros", cls.dir_40_rh)
        cls.dir_ativos = _make_child("01_Funcionarios_Ativos", cls.dir_registros)
        cls.dir_ana = _make_child("MATRICULA_1_Ana_Silva", cls.dir_ativos)
        cls.dir_carlos = _make_child("MATRICULA_2_Carlos_Souza", cls.dir_ativos)

        # Arquivo em pasta de políticas coletivas
        cls.file_politica = cls.env["dms.file"].create({
            "name": "Politica_Qualidade.pdf",
            "directory_id": cls.dir_documentos.id,
            "content": _dummy_content(),
        })
        # Arquivo individual de cada funcionário
        cls.file_ana = cls.env["dms.file"].create({
            "name": "ASO_Ana_2026.pdf",
            "directory_id": cls.dir_ana.id,
            "content": _dummy_content(),
        })
        cls.file_carlos = cls.env["dms.file"].create({
            "name": "ASO_Carlos_2026.pdf",
            "directory_id": cls.dir_carlos.id,
            "content": _dummy_content(),
        })

        # Usuários
        cls.user_ana = new_test_user(
            cls.env,
            login="ana_silva_rh_f439",
            groups="afr_ecm.group_ecm_area_rh_funcionario",
        )
        cls.employee_ana = cls.env["hr.employee"].create({
            "name": "Ana_Silva",
            "user_id": cls.user_ana.id,
        })

        cls.user_carlos = new_test_user(
            cls.env,
            login="carlos_souza_rh_f439",
            groups="afr_ecm.group_ecm_area_rh_funcionario",
        )
        cls.employee_carlos = cls.env["hr.employee"].create({
            "name": "Carlos_Souza",
            "user_id": cls.user_carlos.id,
        })

        # Usuário sem hr.employee vinculado
        cls.user_sem_emp = new_test_user(
            cls.env,
            login="sem_employee_rh_f439",
            groups="afr_ecm.group_ecm_area_rh_funcionario",
        )

    # --- helpers ---

    def _files_for(self, user):
        """Retorna dms.file visíveis ao usuário (aplica record rules via search)."""
        return self.env["dms.file"].with_user(user).search([
            ("id", "in", [
                self.file_politica.id,
                self.file_ana.id,
                self.file_carlos.id,
            ])
        ])

    # --- testes ---

    def test_rh_funcionario_sees_own_folder(self):
        """Ana vê o arquivo em sua pasta MATRICULA_1_Ana_Silva/."""
        files = self._files_for(self.user_ana)
        self.assertIn(self.file_ana, files, "Ana deve ver arquivo na sua pasta.")

    def test_rh_funcionario_sees_documentos(self):
        """Ana vê arquivos em 40_RH/Documentos/ (políticas coletivas)."""
        files = self._files_for(self.user_ana)
        self.assertIn(self.file_politica, files, "Ana deve ver 40_RH/Documentos/.")

    def test_rh_funcionario_does_not_see_colleague(self):
        """Ana NÃO vê o arquivo de Carlos."""
        files = self._files_for(self.user_ana)
        self.assertNotIn(
            self.file_carlos, files,
            "Ana não deve ver a pasta de Carlos.",
        )

    def test_rh_funcionario_no_employee_sees_nothing_in_registros(self):
        """Usuário sem employee vinculado não vê arquivos em Registros/.

        O sentinel '__NO_MATCH_RH_FUNC__' garante que nenhum caminho real
        satisfaça o ilike quando user.employee_id é vazio.
        """
        files = self.env["dms.file"].with_user(self.user_sem_emp).search([
            ("id", "in", [self.file_ana.id, self.file_carlos.id])
        ])
        self.assertFalse(files, "Usuário sem employee não deve ver Registros/.")

    def test_carlos_sees_own_folder_not_anas(self):
        """Carlos vê a sua pasta e não a de Ana."""
        files = self._files_for(self.user_carlos)
        self.assertIn(self.file_carlos, files, "Carlos deve ver sua pasta.")
        self.assertNotIn(self.file_ana, files, "Carlos não deve ver pasta de Ana.")


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_f439")
class TestAuditorExternoRule(TransactionCase):
    """Part B — rule_ecm_auditor_externo_readonly + afr.ecm.audit.scope."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.access_group = cls.env["dms.access.group"].create({
            "name": "F439 Auditor Test Access",
            "perm_create": True,
            "perm_write": True,
            "perm_unlink": True,
            "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_user").id)],
        })
        cls.storage = cls.env["dms.storage"].create({
            "name": "F439 Auditor Storage",
            "save_type": "database",
        })

        def _make_root(name):
            return cls.env["dms.directory"].create({
                "name": name,
                "is_root_directory": True,
                "storage_id": cls.storage.id,
                "group_ids": [(4, cls.access_group.id)],
            })

        cls.dir_sgq = _make_root("F439_00_SGQ")
        cls.dir_reg = _make_root("F439_20_Regulatorio")
        cls.dir_rh = _make_root("F439_40_RH")

        def _make_file(directory, name=None):
            return cls.env["dms.file"].create({
                "name": name or (uuid.uuid4().hex + ".txt"),
                "directory_id": directory.id,
                "content": _dummy_content(),
            })

        cls.file_sgq = _make_file(cls.dir_sgq, "Manual_Qualidade.pdf")
        cls.file_reg = _make_file(cls.dir_reg, "AFE_Vigente.pdf")
        cls.file_rh = _make_file(cls.dir_rh, "Ficha_Funcionario.pdf")

        cls.user_auditor = new_test_user(
            cls.env,
            login="auditor_externo_iso_f439",
            groups="afr_ecm.group_ecm_area_auditor",
        )
        cls.user_auditor2 = new_test_user(
            cls.env,
            login="auditor_externo_anvisa_f439",
            groups="afr_ecm.group_ecm_area_auditor",
        )

        cls.today = date.today()

    # --- helpers ---

    def _create_scope(self, user_ids, dir_ids, start_offset=0, end_offset=5, active=True):
        """Cria um afr.ecm.audit.scope para os dados de teste."""
        return self.env["afr.ecm.audit.scope"].create({
            "name": "Escopo Teste " + uuid.uuid4().hex[:8],
            "auditor_user_ids": [(6, 0, user_ids)],
            "directory_ids": [(6, 0, dir_ids)],
            "start_date": self.today + timedelta(days=start_offset),
            "end_date": self.today + timedelta(days=end_offset),
            "active": active,
        })

    def _visible_files(self, user, candidates=None):
        if candidates is None:
            candidates = [self.file_sgq.id, self.file_reg.id, self.file_rh.id]
        return self.env["dms.file"].with_user(user).search([("id", "in", candidates)])

    # --- testes ---

    def test_auditor_sees_scoped_dirs(self):
        """Auditor vê arquivos somente dos diretórios no seu escopo."""
        scope = self._create_scope(
            user_ids=[self.user_auditor.id],
            dir_ids=[self.dir_sgq.id, self.dir_reg.id],
        )
        self.env.flush_all()

        files = self._visible_files(self.user_auditor)
        self.assertIn(self.file_sgq, files, "Deve ver SGQ (em escopo).")
        self.assertIn(self.file_reg, files, "Deve ver Regulatorio (em escopo).")
        self.assertNotIn(self.file_rh, files, "Não deve ver RH (fora de escopo).")

        scope.unlink()

    def test_auditor_no_scope_sees_nothing(self):
        """Auditor sem escopo ativo não vê nenhum arquivo."""
        # user_auditor2 não tem nenhum escopo
        files = self._visible_files(self.user_auditor2)
        self.assertFalse(files, "Auditor sem escopo não deve ver nada.")

    def test_auditor_inactive_scope_no_access(self):
        """Escopo inativo (active=False) não concede acesso."""
        scope = self._create_scope(
            user_ids=[self.user_auditor.id],
            dir_ids=[self.dir_sgq.id],
            active=False,
        )
        self.env.flush_all()

        files = self._visible_files(self.user_auditor)
        self.assertNotIn(self.file_sgq, files, "Escopo inativo não deve conceder acesso.")

        scope.unlink()

    def test_auditor_future_scope_no_access(self):
        """Escopo que ainda não começou não concede acesso."""
        scope = self._create_scope(
            user_ids=[self.user_auditor.id],
            dir_ids=[self.dir_sgq.id],
            start_offset=1,
            end_offset=5,
        )
        self.env.flush_all()

        files = self._visible_files(self.user_auditor)
        self.assertNotIn(self.file_sgq, files, "Escopo futuro não deve conceder acesso.")

        scope.unlink()

    def test_cron_expires_audit_scope(self):
        """Cron arquiva escopos com end_date anterior a hoje."""
        AuditScope = self.env["afr.ecm.audit.scope"]
        expired_scope = AuditScope.create({
            "name": "Escopo Vencido " + uuid.uuid4().hex[:8],
            "auditor_user_ids": [(6, 0, [self.user_auditor.id])],
            "directory_ids": [(6, 0, [self.dir_sgq.id])],
            "start_date": self.today - timedelta(days=10),
            "end_date": self.today - timedelta(days=1),  # ontem
            "active": True,
        })
        self.assertTrue(expired_scope.active, "Deve estar ativo antes do cron.")

        AuditScope._cron_expire_audit_scopes()

        self.assertFalse(
            expired_scope.active,
            "Cron deve ter arquivado o escopo vencido.",
        )

    def test_cron_does_not_expire_active_scope(self):
        """Cron não arquiva escopos ainda válidos."""
        AuditScope = self.env["afr.ecm.audit.scope"]
        valid_scope = AuditScope.create({
            "name": "Escopo Valido " + uuid.uuid4().hex[:8],
            "auditor_user_ids": [(6, 0, [self.user_auditor.id])],
            "directory_ids": [(6, 0, [self.dir_sgq.id])],
            "start_date": self.today - timedelta(days=1),
            "end_date": self.today + timedelta(days=5),
            "active": True,
        })

        AuditScope._cron_expire_audit_scopes()

        self.assertTrue(valid_scope.active, "Cron não deve arquivar escopo ainda válido.")
        valid_scope.unlink()

    def test_check_audit_scope_active_helper(self):
        """Helper _check_audit_scope_active() retorna True/False corretamente."""
        scope_ativo = self.env["afr.ecm.audit.scope"].create({
            "name": "Helper Ativo " + uuid.uuid4().hex[:8],
            "auditor_user_ids": [(6, 0, [self.user_auditor.id])],
            "directory_ids": [(6, 0, [self.dir_sgq.id])],
            "start_date": self.today - timedelta(days=1),
            "end_date": self.today + timedelta(days=1),
            "active": True,
        })
        self.assertTrue(scope_ativo._check_audit_scope_active())

        # Vencido mas ainda active=True (cron ainda não rodou)
        scope_vencido = self.env["afr.ecm.audit.scope"].create({
            "name": "Helper Vencido " + uuid.uuid4().hex[:8],
            "auditor_user_ids": [(6, 0, [self.user_auditor.id])],
            "directory_ids": [(6, 0, [self.dir_sgq.id])],
            "start_date": self.today - timedelta(days=5),
            "end_date": self.today - timedelta(days=1),
            "active": True,
        })
        self.assertFalse(scope_vencido._check_audit_scope_active())

        scope_ativo.unlink()
        scope_vencido.unlink()

    def test_date_constraint_raises(self):
        """Escopo com end_date anterior ao start_date gera ValidationError."""
        with self.assertRaises(ValidationError):
            self.env["afr.ecm.audit.scope"].create({
                "name": "Datas inválidas",
                "auditor_user_ids": [(6, 0, [self.user_auditor.id])],
                "directory_ids": [(6, 0, [self.dir_sgq.id])],
                "start_date": self.today,
                "end_date": self.today - timedelta(days=1),
                "active": True,
            })

    def test_auditor_different_users_different_scopes(self):
        """Dois auditores com escopos diferentes veem pastas diferentes."""
        scope1 = self._create_scope(
            user_ids=[self.user_auditor.id],
            dir_ids=[self.dir_sgq.id],
        )
        scope2 = self._create_scope(
            user_ids=[self.user_auditor2.id],
            dir_ids=[self.dir_reg.id],
        )
        self.env.flush_all()

        files1 = self._visible_files(self.user_auditor)
        self.assertIn(self.file_sgq, files1)
        self.assertNotIn(self.file_reg, files1)

        files2 = self._visible_files(self.user_auditor2)
        self.assertIn(self.file_reg, files2)
        self.assertNotIn(self.file_sgq, files2)

        scope1.unlink()
        scope2.unlink()
