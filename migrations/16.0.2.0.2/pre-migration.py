"""Pre-migration 16.0.2.0.2 — F4.4.

Duas correcções de propagação:

1. **group_ecm_area_rh_funcionario** deixou de implicar `group_ecm_user`.
   Odoo NÃO desfaz transitivamente o implied já propagado para utilizadores
   existentes — `groups_id` permanece com `group_ecm_user`. Removemos
   manualmente para utilizadores cuja única via para `group_ecm_user`
   passava por `group_ecm_area_rh_funcionario` (preserva quem tem outro
   grupo da área que ainda implica `group_ecm_user`).

2. **dms.access.group `ECM Padrão (AFR)`** ganha
   `group_ecm_area_rh_funcionario` em `group_ids`. O ficheiro
   `data/dms_access_group_data.xml` tem `noupdate="1"` e por isso o
   upgrade normal não escreve `group_ids`; aplicamos via ORM.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install — XML data carrega correctamente, nada a migrar.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    g_func = env.ref(
        "afr_ecm.group_ecm_area_rh_funcionario", raise_if_not_found=False
    )
    g_user = env.ref("afr_ecm.group_ecm_user", raise_if_not_found=False)
    g_dms_user = env.ref("dms.group_dms_user", raise_if_not_found=False)

    if not g_func or not g_user:
        _logger.info(
            "afr_ecm 16.0.2.0.2: grupos ainda não existem (instalação parcial?), skip."
        )
        return

    # --- 0. Força implied_ids de group_ecm_area_rh_funcionario --------------
    # `security_ecm_areas.xml` tem noupdate="1" → upgrade normal não escreve
    # implied_ids. Aplicamos via ORM (também sincroniza users transitivamente
    # implicados via mecanismo nativo).
    desired_implied = g_dms_user.ids if g_dms_user else []
    current_implied = g_func.implied_ids.ids
    if set(current_implied) != set(desired_implied):
        g_func.write({"implied_ids": [(6, 0, desired_implied)]})
        _logger.warning(
            "afr_ecm 16.0.2.0.2: implied_ids de %s actualizado %s → %s.",
            g_func.name,
            current_implied,
            desired_implied,
        )

    # --- 1. Limpa group_ecm_user de users que só o tinham via rh_funcionario ---
    cleaned = env["res.users"]
    for user in g_func.users:
        # Outros grupos do utilizador (sem o rh_funcionario)
        other = user.groups_id - g_func
        # Total transitivamente implicado por outros grupos
        implied_by_other = other | other.mapped("trans_implied_ids")
        if g_user not in implied_by_other:
            user.write({"groups_id": [(3, g_user.id)]})
            cleaned |= user

    if cleaned:
        _logger.warning(
            "afr_ecm 16.0.2.0.2: removido group_ecm_user de %d utilizador(es) "
            "rh_funcionario sem outra via implicada: %s",
            len(cleaned),
            cleaned.mapped("login"),
        )
    else:
        _logger.info(
            "afr_ecm 16.0.2.0.2: nenhum utilizador rh_funcionario "
            "precisava de limpeza de group_ecm_user."
        )

    # --- 2. Refresca dms_access_group_ecm_default.group_ids (noupdate=1) ---
    ag = env.ref(
        "afr_ecm.dms_access_group_ecm_default", raise_if_not_found=False
    )
    if ag and g_func.id not in ag.group_ids.ids:
        ag.write({"group_ids": [(4, g_func.id)]})
        _logger.info(
            "afr_ecm 16.0.2.0.2: %s.group_ids agora inclui %s.",
            ag.name,
            g_func.name,
        )
