"""Pre-migration 16.0.2.0.3 — F4.5.

Força implied_ids de `group_ecm_area_auditor` a incluir `dms.group_dms_user`.
O ficheiro `security/security_ecm_areas.xml` tem `noupdate="1"` e por isso
o upgrade normal não escreve a alteração. Aplicamos via ORM — o write
propaga `dms.group_dms_user` (e transitivamente `base.group_user`) para
utilizadores existentes do grupo.

Sem este implied, auditor recebe AccessError no model dms.file antes
mesmo das ir.rule serem avaliadas (ver F4.5 no manifest).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    g_auditor = env.ref(
        "afr_ecm.group_ecm_area_auditor", raise_if_not_found=False
    )
    g_dms_user = env.ref("dms.group_dms_user", raise_if_not_found=False)

    if not g_auditor or not g_dms_user:
        _logger.info(
            "afr_ecm 16.0.2.0.3: grupos ainda não existem (instalação parcial?), skip."
        )
        return

    if g_dms_user in g_auditor.implied_ids:
        _logger.info(
            "afr_ecm 16.0.2.0.3: %s já implica %s — nada a fazer.",
            g_auditor.name,
            g_dms_user.name,
        )
        return

    g_auditor.write({"implied_ids": [(4, g_dms_user.id)]})
    _logger.warning(
        "afr_ecm 16.0.2.0.3: %s agora implica %s (propagado a %d utilizador(es)).",
        g_auditor.name,
        g_dms_user.name,
        len(g_auditor.users),
    )
