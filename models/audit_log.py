from odoo import api, fields, models


class AfrEcmAuditLog(models.Model):
    _name = "afr.ecm.audit.log"
    _description = "ECM Audit Log"
    _order = "create_date desc, id desc"
    _rec_name = "summary"

    EVENT_TYPES = [
        ("create", "Criação"),
        ("write", "Alteração"),
        ("unlink", "Exclusão"),
        ("view", "Visualização"),
        ("download", "Download"),
        ("acl_change", "Mudança de Permissão"),
    ]

    user_id = fields.Many2one(
        "res.users",
        string="Usuário",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
        index=True,
    )
    event_type = fields.Selection(EVENT_TYPES, required=True, index=True)
    model = fields.Char(string="Modelo", required=True, index=True)
    res_id = fields.Integer(string="ID do Registro", required=True, index=True)
    res_name = fields.Char(string="Nome do Registro")
    ip = fields.Char(string="IP")
    details = fields.Text(string="Detalhes")
    summary = fields.Char(compute="_compute_summary", store=True)
    record_ref = fields.Reference(
        selection="_selection_record_ref",
        compute="_compute_record_ref",
        string="Registro",
    )

    @api.model
    def _selection_record_ref(self):
        return [
            ("dms.file", "Arquivo"),
            ("dms.directory", "Diretório"),
            ("afr.ecm.physical.location", "Localização Física"),
        ]

    @api.depends("model", "res_id")
    def _compute_record_ref(self):
        for rec in self:
            if rec.model and rec.res_id and rec.model in self.env:
                rec.record_ref = f"{rec.model},{rec.res_id}"
            else:
                rec.record_ref = False

    @api.depends("event_type", "model", "res_name", "res_id")
    def _compute_summary(self):
        labels = dict(self.EVENT_TYPES)
        for rec in self:
            ev = labels.get(rec.event_type, rec.event_type or "")
            rec.summary = f"{ev} · {rec.model} #{rec.res_id} {rec.res_name or ''}".strip()

    @api.model
    def log(self, event_type, record, details=None, ip=None):
        """Convenience helper to write a log entry."""
        if not record:
            return self.browse()
        vals = {
            "event_type": event_type,
            "model": record._name,
            "res_id": record.id,
            "res_name": record.display_name if "display_name" in record._fields else False,
            "details": details or False,
            "ip": ip or self._get_request_ip(),
        }
        return self.sudo().create(vals)

    @api.model
    def _get_request_ip(self):
        try:
            from odoo.http import request
            if request and request.httprequest:
                return request.httprequest.remote_addr
        except Exception:
            return False
        return False
