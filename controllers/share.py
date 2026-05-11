import base64
import logging

from odoo import http, tools
from odoo.http import request
from odoo.addons.web.controllers.utils import ensure_db

_logger = logging.getLogger(__name__)


class EcmShare(http.Controller):
    """Endpoint público de download via access_token (afr_ecm).
    Substitui dms.portal /my/dms/file/<id>/download (que requer website).
    """

    @http.route(
        ["/ecm/share/<int:file_id>/<string:token>"],
        type="http",
        auth="public",
        csrf=False,
    )
    def ecm_share_download(self, file_id, token, **kw):
        ensure_db()
        if not token:
            return request.not_found()
        dms = request.env["dms.file"].sudo().browse(file_id).exists()
        if not dms or not dms.access_token:
            return request.not_found()
        if not tools.consteq(dms.access_token, token):
            return request.not_found()
        # audit
        try:
            request.env["afr.ecm.audit.log"].sudo().log("download", dms)
        except Exception as e:
            _logger.warning("ECM share audit failed: %s", e)
        content = base64.b64decode(dms.content or b"")
        filename = dms.name or ("file_%s" % dms.id)
        return request.make_response(
            content,
            headers=[
                ("Content-Type", dms.mimetype or "application/octet-stream"),
                ("Content-Disposition", "attachment; filename=\"%s\"" % filename.replace('"', "")),
                ("Cache-Control", "no-store"),
            ],
        )
