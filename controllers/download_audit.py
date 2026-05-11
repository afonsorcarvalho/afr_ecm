import logging

from odoo import http
from odoo.addons.web.controllers.binary import Binary

_logger = logging.getLogger(__name__)


class BinaryAudit(Binary):
    @http.route()
    def content_common(self, *args, **kwargs):
        response = super().content_common(*args, **kwargs)
        try:
            self._log_dms_download(kwargs)
        except Exception as exc:
            _logger.warning("ECM audit log failed: %s", exc)
        return response

    def _log_dms_download(self, kwargs):
        model = kwargs.get("model")
        rid = kwargs.get("id")
        if not model or not rid:
            return
        if model not in ("dms.file", "ir.attachment"):
            return
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            return
        env = http.request.env
        if model == "ir.attachment":
            att = env["ir.attachment"].sudo().browse(rid_int).exists()
            if not att or att.res_model != "dms.file":
                return
            target = env["dms.file"].sudo().browse(att.res_id).exists()
        else:
            target = env["dms.file"].sudo().browse(rid_int).exists()
        if target:
            env["afr.ecm.audit.log"].sudo().log("download", target)
