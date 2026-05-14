"""Tests for F4.3.10 — ir.rule-based auditor directory tree access.

Coverage:
  1. Auditor with an active scope on a LEAF folder sees the FULL path from
     root to that leaf (parent folders visible via `parent_of`).
  2. Auditor does NOT see sibling folders that are outside the scope.
  3. Auditor sees files inside the scoped folder AND its descendant subfolders
     (child_of in rule_ecm_auditor_externo_readonly).
  4. Auditor does NOT see files outside the scope.
  5. Expired scope (active=False) → audit_scope_directory_ids is empty →
     auditor sees no directories (child_of([]) and parent_of([]) → empty).
  6. perm_write=False on the ir.rule: auditor cannot write a dms.directory
     even when it is in scope (AccessError expected).

Design notes:
  - dms.directory visibility for auditors flows through TWO layers:
      (a) OCA global rule: directory must belong to a dms.access.group whose
          complete_group_ids includes the user's res.groups. After migration
          Auditor_Externo is anchored at the root. Tests must grant the auditor
          access via a separate dms.access.group attached to group_ecm_area_auditor
          OR via the Auditor_Externo group being on the storage root.
      (b) rule_ecm_auditor_directory_tree (ir.rule on dms.directory) further
          narrows to scope + parents.
    Because we cannot guarantee the migration has run in a test transaction,
    tests explicitly create a dms.access.group for group_ecm_area_auditor so
    the OCA global rule passes; our new ir.rule then provides the per-scope
    narrowing.

  - TransactionCase: each test method rolls back; setUpClass sets up shared
    data once (rolled back after the class).

  - Tagged post_install so the model is fully installed; -at_install to skip
    during installation.

  - tests/__init__.py must include: from . import test_audit_scope_tree_rule
    (see MANIFEST_PATCH_F4_3_10.md).
"""
import base64
import uuid
from datetime import date, timedelta

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


def _dummy_content():
    return base64.b64encode(b"\xff test-content-f4310")


@tagged("post_install", "-at_install", "afr_ecm", "afr_ecm_f4310")
class TestAuditorDirectoryTreeRule(TransactionCase):
    """Tests for rule_ecm_auditor_directory_tree + rule_ecm_auditor_externo_readonly.

    Hierarchy used in tests:
        root/               ← is_root_directory
          scoped_dir/       ← leaf placed in audit scope
            child_dir/      ← descendant (auditor should see via child_of)
          sibling_dir/      ← sibling outside scope (auditor must NOT see)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # dms.access.group that grants group_ecm_area_auditor into the OCA
        # global directory rule (complete_group_ids ⊇ user.groups_id).
        # This simulates the Auditor_Externo access group post-migration.
        cls.auditor_access_group = cls.env["dms.access.group"].create({
            "name": "F4310 Auditor Access Group",
            "perm_create": False,
            "perm_write": False,
            "perm_unlink": False,
            "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_area_auditor").id)],
        })

        cls.storage = cls.env["dms.storage"].create({
            "name": "F4310 Auditor Storage",
            "save_type": "database",
        })

        # Root directory — attached to the auditor access group so OCA global
        # rule passes for auditors.
        cls.dir_root = cls.env["dms.directory"].create({
            "name": "F4310_Root",
            "is_root_directory": True,
            "storage_id": cls.storage.id,
            "group_ids": [(4, cls.auditor_access_group.id)],
        })

        # Scoped leaf directory (will be placed in audit scope).
        cls.dir_scoped = cls.env["dms.directory"].create({
            "name": "F4310_Scoped",
            "parent_id": cls.dir_root.id,
            "group_ids": [(4, cls.auditor_access_group.id)],
        })

        # Descendant of scoped dir.
        cls.dir_child = cls.env["dms.directory"].create({
            "name": "F4310_Child",
            "parent_id": cls.dir_scoped.id,
            "group_ids": [(4, cls.auditor_access_group.id)],
        })

        # Sibling outside scope.
        cls.dir_sibling = cls.env["dms.directory"].create({
            "name": "F4310_Sibling",
            "parent_id": cls.dir_root.id,
            "group_ids": [(4, cls.auditor_access_group.id)],
        })

        # Files.
        cls.file_scoped = cls.env["dms.file"].create({
            "name": "file_in_scoped.pdf",
            "directory_id": cls.dir_scoped.id,
            "content": _dummy_content(),
        })
        cls.file_child = cls.env["dms.file"].create({
            "name": "file_in_child.pdf",
            "directory_id": cls.dir_child.id,
            "content": _dummy_content(),
        })
        cls.file_sibling = cls.env["dms.file"].create({
            "name": "file_in_sibling.pdf",
            "directory_id": cls.dir_sibling.id,
            "content": _dummy_content(),
        })

        # Auditor user — only group_ecm_area_auditor (+ base.group_user added
        # by new_test_user). group_ecm_area_auditor does NOT imply group_ecm_user
        # (hotfix already applied in security_ecm_areas.xml).
        cls.user_auditor = new_test_user(
            cls.env,
            login="auditor_f4310_" + uuid.uuid4().hex[:6],
            groups="afr_ecm.group_ecm_area_auditor",
        )

        cls.today = date.today()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _create_scope(self, dirs, start_offset=0, end_offset=7, active=True):
        return self.env["afr.ecm.audit.scope"].create({
            "name": "Scope F4310 " + uuid.uuid4().hex[:8],
            "auditor_user_ids": [(6, 0, [self.user_auditor.id])],
            "directory_ids": [(6, 0, [d.id for d in dirs])],
            "start_date": self.today + timedelta(days=start_offset),
            "end_date": self.today + timedelta(days=end_offset),
            "active": active,
        })

    def _visible_dirs(self, candidates=None):
        """Return dms.directory records visible to the auditor."""
        if candidates is None:
            candidates = [
                self.dir_root.id,
                self.dir_scoped.id,
                self.dir_child.id,
                self.dir_sibling.id,
            ]
        return self.env["dms.directory"].with_user(self.user_auditor).search(
            [("id", "in", candidates)]
        )

    def _visible_files(self, candidates=None):
        if candidates is None:
            candidates = [
                self.file_scoped.id,
                self.file_child.id,
                self.file_sibling.id,
            ]
        return self.env["dms.file"].with_user(self.user_auditor).search(
            [("id", "in", candidates)]
        )

    # -------------------------------------------------------------------------
    # Tests
    # -------------------------------------------------------------------------

    def test_auditor_sees_full_path_from_root_to_leaf(self):
        """Auditor with scope on scoped_dir sees root + scoped_dir (parent path)."""
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        dirs = self._visible_dirs()
        self.assertIn(
            self.dir_root, dirs,
            "Auditor deve ver o diretório raiz (parent_of scoped_dir).",
        )
        self.assertIn(
            self.dir_scoped, dirs,
            "Auditor deve ver o diretório em escopo.",
        )
        scope.unlink()

    def test_auditor_sees_descendant_directories(self):
        """Auditor sees child_dir (descendant of scoped_dir via child_of)."""
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        dirs = self._visible_dirs()
        self.assertIn(
            self.dir_child, dirs,
            "Auditor deve ver subpasta descendente do diretório em escopo (child_of).",
        )
        scope.unlink()

    def test_auditor_does_not_see_sibling_dirs(self):
        """Auditor does NOT see sibling_dir (outside scope, not ancestor/descendant)."""
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        dirs = self._visible_dirs()
        self.assertNotIn(
            self.dir_sibling, dirs,
            "Auditor NÃO deve ver diretório irmão fora do escopo.",
        )
        scope.unlink()

    def test_auditor_sees_files_in_scoped_dir(self):
        """Auditor sees files directly inside the scoped directory."""
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        files = self._visible_files()
        self.assertIn(
            self.file_scoped, files,
            "Auditor deve ver arquivos no diretório em escopo.",
        )
        scope.unlink()

    def test_auditor_sees_files_in_descendant_dirs(self):
        """Auditor sees files in child_dir (descendant of scoped via child_of)."""
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        files = self._visible_files()
        self.assertIn(
            self.file_child, files,
            "Auditor deve ver arquivos em subpastas descendentes do escopo (child_of).",
        )
        scope.unlink()

    def test_auditor_does_not_see_files_outside_scope(self):
        """Auditor does NOT see files in sibling_dir."""
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        files = self._visible_files()
        self.assertNotIn(
            self.file_sibling, files,
            "Auditor NÃO deve ver arquivos fora do escopo.",
        )
        scope.unlink()

    def test_expired_scope_auditor_sees_no_dirs(self):
        """Expired scope (active=False) → audit_scope_directory_ids = [] → no dirs visible."""
        scope = self._create_scope(dirs=[self.dir_scoped], active=False)
        self.env.flush_all()

        dirs = self._visible_dirs()
        # With an empty set, child_of([]) and parent_of([]) both return no results.
        # The auditor sees nothing even via dms.access.group because the ir.rule
        # is AND-combined on top.
        self.assertNotIn(
            self.dir_scoped, dirs,
            "Escopo inativo: auditor NÃO deve ver o diretório em escopo.",
        )
        scope.unlink()

    def test_expired_scope_by_date_auditor_sees_no_dirs(self):
        """Past end_date scope (still active=True until cron) behaves like expired.

        audit_scope_directory_ids checks start_date ≤ today ≤ end_date, so a
        scope with end_date in the past yields empty even if active=True.
        """
        scope = self._create_scope(
            dirs=[self.dir_scoped],
            start_offset=-10,
            end_offset=-1,  # ended yesterday
            active=True,
        )
        self.env.flush_all()

        dirs = self._visible_dirs()
        self.assertNotIn(
            self.dir_scoped, dirs,
            "Escopo com end_date no passado: auditor NÃO deve ver o diretório.",
        )
        scope.unlink()

    def test_auditor_cannot_write_scoped_directory(self):
        """perm_write=False on ir.rule: auditor gets AccessError on write to scoped dir.

        Note: in Odoo, perm_write=False on an ir.rule means the rule does NOT
        apply to write operations — those are checked against OTHER rules. If no
        other rule grants write, the default is deny. Additionally, the
        Auditor_Externo dms.access.group has perm_write=False (enforced by the
        migration), which denies writes at the access-group layer.

        This test verifies that the combined configuration prevents writes.
        """
        scope = self._create_scope(dirs=[self.dir_scoped])
        self.env.flush_all()

        with self.assertRaises(AccessError):
            self.env["dms.directory"].with_user(self.user_auditor).browse(
                self.dir_scoped.id
            ).write({"name": "F4310_Scoped_MODIFIED"})

        scope.unlink()

    def test_no_scope_auditor_sees_no_dirs(self):
        """Auditor with no active scope at all sees no directories."""
        # Do not create any scope for this test.
        self.env.flush_all()

        dirs = self._visible_dirs()
        self.assertFalse(
            dirs,
            "Auditor sem escopo NÃO deve ver nenhum diretório.",
        )
