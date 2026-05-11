from odoo import api, fields, models

from .document_type import CONFIDENTIALITY


class DmsFile(models.Model):
    _name = "dms.file"
    _inherit = ["dms.file", "afr.ecm.audit.mixin"]

    document_type_id = fields.Many2one(
        "afr.ecm.document.type",
        string="Tipo de Documento",
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    confidentiality = fields.Selection(
        CONFIDENTIALITY,
        default="internal",
        required=True,
        index=True,
        tracking=True,
    )
    metadata_value_ids = fields.One2many(
        "afr.ecm.metadata.value",
        "file_id",
        string="Metadados",
    )
    physical_location_id = fields.Many2one(
        "afr.ecm.physical.location",
        string="Localização Física",
        ondelete="set null",
        index=True,
        tracking=True,
    )
    expiration_date = fields.Date(
        string="Vencimento",
        index=True,
        help="Usado em fases futuras para alertas e expiração automática.",
    )

    @api.onchange("document_type_id")
    def _onchange_document_type_id(self):
        for rec in self:
            if rec.document_type_id:
                if not rec.confidentiality or rec.confidentiality == "internal":
                    rec.confidentiality = rec.document_type_id.default_confidentiality
                if rec.document_type_id.default_directory_id and not rec.directory_id:
                    rec.directory_id = rec.document_type_id.default_directory_id

    def _audit_log_view(self):
        """Public hook used by controllers/actions to log view event."""
        Log = self.env["afr.ecm.audit.log"].sudo()
        for rec in self:
            Log.log("view", rec)
