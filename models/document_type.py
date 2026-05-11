from odoo import fields, models

CONFIDENTIALITY = [
    ("public", "Público"),
    ("internal", "Interno"),
    ("restricted", "Restrito"),
    ("confidential", "Confidencial"),
]


class AfrEcmDocumentType(models.Model):
    _name = "afr.ecm.document.type"
    _description = "Tipo de Documento ECM"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    description = fields.Text()
    active = fields.Boolean(default=True)

    default_confidentiality = fields.Selection(
        CONFIDENTIALITY,
        string="Confidencialidade Padrão",
        default="internal",
        required=True,
    )
    default_directory_id = fields.Many2one(
        "dms.directory",
        string="Pasta DMS Sugerida",
        help="Diretório DMS sugerido ao classificar um arquivo neste tipo.",
    )
    retention_days = fields.Integer(
        string="Retenção (dias)",
        help="Sugestão de prazo de retenção. Usado em fases futuras para expiração automática.",
    )
    metadata_field_ids = fields.One2many(
        "afr.ecm.metadata.field",
        "document_type_id",
        string="Campos de Metadado",
    )
    color = fields.Integer(string="Cor")

    _sql_constraints = [
        ("code_uniq", "unique(code)", "O código do tipo de documento deve ser único."),
    ]
