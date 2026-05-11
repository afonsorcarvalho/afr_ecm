import logging

from odoo import http
from odoo.addons.web.controllers.binary import Binary
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


class BinaryAudit(Binary):
    @http.route()
    def content_common(self, *args, **kwargs):
        # Bloqueia antes de servir conteúdo se user não pode baixar.
        try:
            self._check_dms_download_allowed(kwargs)
        except AccessError:
            return http.request.not_found()

        response = super().content_common(*args, **kwargs)
        try:
            self._log_dms_download(kwargs)
        except Exception as exc:
            _logger.warning("ECM audit log failed: %s", exc)
        return response

    def _resolve_dms_file(self, kwargs):
        """Retorna recordset dms.file (vazio se não aplicável)."""
        model = kwargs.get("model")
        rid = kwargs.get("id")
        if not model or not rid:
            return http.request.env["dms.file"]
        if model not in ("dms.file", "ir.attachment"):
            return http.request.env["dms.file"]
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            return http.request.env["dms.file"]
        env = http.request.env
        if model == "ir.attachment":
            att = env["ir.attachment"].sudo().browse(rid_int).exists()
            if not att or att.res_model != "dms.file":
                return env["dms.file"]
            return env["dms.file"].sudo().browse(att.res_id).exists()
        return env["dms.file"].sudo().browse(rid_int).exists()

    def _check_dms_download_allowed(self, kwargs):
        """Levanta AccessError se download não autorizado.

        Aplica só quando `download=true` (clique em Baixar). View inline
        (preview, attachment img) não passa por aqui.
        """
        if not self._is_download_request(kwargs):
            return
        target = self._resolve_dms_file(kwargs)
        if not target:
            return
        user = http.request.env.user
        if not target.with_user(user)._user_can_download(user):
            _logger.info(
                "ECM: bloqueado download de dms.file id=%s para user=%s (sem grupo)",
                target.id, user.login,
            )
            raise AccessError("Download não autorizado para este tipo de documento.")

    @staticmethod
    def _is_download_request(kwargs):
        v = kwargs.get("download")
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def _log_dms_download(self, kwargs):
        target = self._resolve_dms_file(kwargs)
        if target:
            http.request.env["afr.ecm.audit.log"].sudo().log("download", target)
