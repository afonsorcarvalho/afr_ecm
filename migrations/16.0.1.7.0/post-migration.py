"""F4.3.10 post-migration: retire dynamic Auditor_Externo dms.access.group sync.

What this migration does:
  1. Finds the Auditor_Externo dms.access.group (created in F4.3.9 data files).
  2. Resets its directory_ids to the single root DOCUMENTAÇÃO directory (read-only
     pass-through so auditors can enter the tree at all — the ir.rule narrows from
     there per-user/per-scope).
  3. Ensures group_ids = [group_ecm_area_auditor] only.
  4. Forces perm_create=perm_write=perm_unlink=False on the access group so write
     denial is enforced at the dms.access.group layer.

Why:
  The old mechanism wrote per-scope directory_ids onto Auditor_Externo dynamically
  via create/write/unlink hooks on afr.ecm.audit.scope. Those hooks are removed
  in F4.3.10. Going forward, visibility is controlled purely by the stateless ir.rule
  `rule_ecm_auditor_directory_tree` (dms.directory) + `rule_ecm_auditor_externo_readonly`
  (dms.file), both keyed on user.audit_scope_directory_ids (computed in real time).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Reset Auditor_Externo dms.access.group to root read-only; remove stale dirs."""
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})

    access_group = env["dms.access.group"].search(
        [("name", "=", "Auditor_Externo")], limit=1
    )
    if not access_group:
        _logger.warning(
            "F4.3.10 migration: Auditor_Externo dms.access.group not found — skipping. "
            "The ir.rules will still work; just ensure the access group is created by "
            "data/dms_access_group_data.xml on a fresh install."
        )
        return

    # Prefer a root directory named DOCUMENTAÇÃO; fall back to any is_root_directory.
    root = env["dms.directory"].search(
        [("is_root_directory", "=", True), ("name", "ilike", "DOCUMENTA")], limit=1
    )
    if not root:
        root = env["dms.directory"].search(
            [("is_root_directory", "=", True)], limit=1
        )

    auditor_group = env.ref("afr_ecm.group_ecm_area_auditor", raise_if_not_found=False)

    vals = {
        "perm_create": False,
        "perm_write": False,
        "perm_unlink": False,
    }
    if root:
        vals["directory_ids"] = [(6, 0, [root.id])]
        _logger.info(
            "F4.3.10 migration: pointing Auditor_Externo to root directory '%s' (id=%d)",
            root.complete_name,
            root.id,
        )
    else:
        # No root at all (fresh DB without taxonomy loaded) — clear stale dirs.
        vals["directory_ids"] = [(5, 0, 0)]
        _logger.warning(
            "F4.3.10 migration: no root directory found — clearing Auditor_Externo.directory_ids."
        )

    if auditor_group:
        vals["group_ids"] = [(6, 0, [auditor_group.id])]
    else:
        _logger.warning(
            "F4.3.10 migration: afr_ecm.group_ecm_area_auditor ref not found — "
            "group_ids not updated."
        )

    access_group.write(vals)
    _logger.info(
        "F4.3.10 migration: Auditor_Externo dms.access.group reset to root read-only. "
        "Directory-level access is now controlled by ir.rule rule_ecm_auditor_directory_tree."
    )
