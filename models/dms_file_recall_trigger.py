# -*- coding: utf-8 -*-
"""Auto-spawn an `afr.ecm.recall` when a `dms.file` of doc type code
`OP_BI_POS` is created OR transitions to `approved`.

Idempotent: if a recall already exists with `bi_positive_file_id` pointing at
this file, no new recall is created.

Bypass with `self.with_context(afr_ecm_skip_recall_trigger=True)` for tests
or bulk data loads.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


BI_POS_CODE = "OP_BI_POS"
RECALL_CODE = "OP_RECALL"


class DmsFileRecallTrigger(models.Model):
    _inherit = "dms.file"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_bi_positive(self):
        self.ensure_one()
        dt = self.document_type_id
        return bool(dt and dt.code == BI_POS_CODE)

    @api.model
    def _recall_default_directory_id(self):
        doc_type = self.env["afr.ecm.document.type"].sudo().search(
            [("code", "=", RECALL_CODE)], limit=1
        )
        if doc_type and doc_type.default_directory_id:
            return doc_type.default_directory_id.id
        return False

    def _spawn_recall_for_bi_positive(self):
        """Create a draft Recall linked to this BI+ file if none exists yet.
        Uses sudo for both lookup and create — the user filing the BI+ may not
        have create rights on `afr.ecm.recall`."""
        self.ensure_one()
        if self.env.context.get("afr_ecm_skip_recall_trigger"):
            return False
        if not self._is_bi_positive():
            return False
        Recall = self.env["afr.ecm.recall"].sudo()
        existing = Recall.search([
            ("bi_positive_file_id", "=", self.id),
        ], limit=1)
        if existing:
            return existing
        vals = {
            "title": _("Recall automático — BI+ %s") % (self.name or self.id),
            "trigger_type": "bi_positive",
            "trigger_event_ref": _("dms.file id=%s") % self.id,
            "bi_positive_file_id": self.id,
            "responsible_id": (
                self.create_uid.id if self.create_uid else self.env.user.id
            ),
            "severity": "critical",
            "attachment_ids": [(4, self.id)],
        }
        directory_id = self._recall_default_directory_id()
        if directory_id:
            vals["directory_id"] = directory_id
        recall = Recall.create(vals)
        # Chatter back-link on the BI+ file (best-effort, swallow if mail
        # not available for this record).
        try:
            self.message_post(
                body=_(
                    "Recall <a href=# data-oe-model=afr.ecm.recall "
                    "data-oe-id=%(id)d>%(name)s</a> criado automaticamente "
                    "por este BI Positivo."
                ) % {"id": recall.id, "name": recall.name},
            )
        except Exception:  # pragma: no cover - defensive
            _logger.warning(
                "Recall %s criado mas message_post no dms.file %s falhou.",
                recall.name, self.id, exc_info=True,
            )
        return recall

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("afr_ecm_skip_recall_trigger"):
            return records
        for rec in records:
            try:
                rec._spawn_recall_for_bi_positive()
            except Exception:  # pragma: no cover - defensive
                _logger.exception(
                    "Falha ao auto-criar Recall para dms.file %s", rec.id,
                )
        return records

    def write(self, vals):
        # Snapshot approval_state per record BEFORE super, so we can detect
        # the transition into 'approved' and trigger the recall on it.
        prev_states = {}
        if "approval_state" in vals and not self.env.context.get(
            "afr_ecm_skip_recall_trigger"
        ):
            for rec in self:
                prev_states[rec.id] = rec.approval_state
        res = super().write(vals)
        if "approval_state" in vals and not self.env.context.get(
            "afr_ecm_skip_recall_trigger"
        ):
            for rec in self:
                was = prev_states.get(rec.id)
                if (
                    rec.approval_state == "approved"
                    and was != "approved"
                    and rec._is_bi_positive()
                ):
                    try:
                        rec._spawn_recall_for_bi_positive()
                    except Exception:  # pragma: no cover - defensive
                        _logger.exception(
                            "Falha ao auto-criar Recall na aprovação do "
                            "dms.file %s", rec.id,
                        )
        return res
