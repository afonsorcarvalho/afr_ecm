from odoo import fields, models


class AfrEcmMetadataField(models.Model):
    _name = "afr.ecm.metadata.field"
    _description = "Campo de Metadado ECM"
    _order = "document_type_id, sequence, name"

    FIELD_TYPES = [
        ("char", "Texto"),
        ("text", "Texto longo"),
        ("integer", "Inteiro"),
        ("float", "Decimal"),
        ("date", "Data"),
        ("datetime", "Data/Hora"),
        ("boolean", "Booleano"),
        ("selection", "Seleção"),
    ]

    document_type_id = fields.Many2one(
        "afr.ecm.document.type",
        required=True,
        ondelete="cascade",
        string="Tipo de Documento",
    )
    name = fields.Char(string="Identificador", required=True, help="Chave técnica (sem espaços)")
    label = fields.Char(string="Rótulo", required=True, translate=True)
    field_type = fields.Selection(FIELD_TYPES, required=True, default="char")
    sequence = fields.Integer(default=10)
    required = fields.Boolean()
    selection_values = fields.Text(
        help="Para tipo Seleção. Uma opção por linha no formato chave:rótulo.",
    )
    help_text = fields.Char(string="Ajuda", translate=True)

    _sql_constraints = [
        (
            "type_name_uniq",
            "unique(document_type_id, name)",
            "Identificador deve ser único por tipo de documento.",
        ),
    ]
