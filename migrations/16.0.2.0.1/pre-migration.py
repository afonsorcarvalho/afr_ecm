"""Pre-migration 16.0.2.0.1.

Remove as ir.rule antigas que foram substituídas por uma regra unificada
em security/record_rules.xml. Sem isso, o upgrade não as remove
(noupdate=1 não rejeita rows existentes nem reescreve xmlids antigos
quando o xmlid foi renomeado).

Bug histórico que motivou esta migration:
    As 3 regras antigas (`rule_dms_file_internal`,
    `rule_dms_file_restricted`, `rule_dms_file_confidential`)
    compartilhavam `group_ecm_user`. Odoo combina regras do mesmo grupo
    por OR — os domínios `!=restricted` / `!=confidential` se sabotavam
    mutuamente e permitiam vazamento entre donos.

    A regra `rule_dms_file_restricted_manager` (xmlid antigo) foi
    renomeada para `rule_dms_file_manager` (xmlid novo).
"""
import logging

_logger = logging.getLogger(__name__)

OLD_XMLIDS = [
    "afr_ecm.rule_dms_file_internal",
    "afr_ecm.rule_dms_file_restricted",
    "afr_ecm.rule_dms_file_confidential",
    "afr_ecm.rule_dms_file_restricted_manager",
]


def migrate(cr, version):
    if not version:
        # Instalação fresh — nada a migrar.
        return

    cr.execute(
        """
        SELECT res_id, name
          FROM ir_model_data
         WHERE module = 'afr_ecm'
           AND model = 'ir.rule'
           AND name IN %s
        """,
        (tuple(x.split(".", 1)[1] for x in OLD_XMLIDS),),
    )
    rows = cr.fetchall()
    if not rows:
        _logger.info(
            "afr_ecm 16.0.2.0.1: nenhuma ir.rule antiga encontrada — nada a remover."
        )
        return

    rule_ids = [r[0] for r in rows]
    _logger.warning(
        "afr_ecm 16.0.2.0.1: removendo %d ir.rule(s) antiga(s) "
        "antes de aplicar regra unificada: %s",
        len(rule_ids),
        [r[1] for r in rows],
    )

    # Deleta ir.model.data primeiro (FK constraint), depois ir.rule.
    cr.execute(
        "DELETE FROM ir_model_data WHERE model = 'ir.rule' AND res_id IN %s",
        (tuple(rule_ids),),
    )
    cr.execute("DELETE FROM ir_rule WHERE id IN %s", (tuple(rule_ids),))

    _logger.info(
        "afr_ecm 16.0.2.0.1: ir.rule antigas removidas. "
        "Upgrade vai criar rule_dms_file_ecm_user + rule_dms_file_manager."
    )
