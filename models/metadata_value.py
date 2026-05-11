from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AfrEcmMetadataValue(models.Model):
    _name = "afr.ecm.metadata.value"
    _description = "Valor de Metadado ECM"
    _order = "field_id"
    _rec_name = "display_value"

    file_id = fields.Many2one(
        "dms.file",
        required=True,
        ondelete="cascade",
        index=True,
    )
    field_id = fields.Many2one(
        "afr.ecm.metadata.field",
        required=True,
        ondelete="restrict",
    )
    field_type = fields.Selection(related="field_id.field_type", store=True)
    label = fields.Char(related="field_id.label", store=False)

    value_char = fields.Char()
    value_text = fields.Text()
    value_integer = fields.Integer()
    value_float = fields.Float()
    value_date = fields.Date()
    value_datetime = fields.Datetime()
    value_boolean = fields.Boolean()
    value_selection = fields.Char()

    display_value = fields.Char(compute="_compute_display_value", store=True)

    @api.depends(
        "field_type",
        "value_char", "value_text", "value_integer", "value_float",
        "value_date", "value_datetime", "value_boolean", "value_selection",
    )
    def _compute_display_value(self):
        for rec in self:
            ft = rec.field_type
            mapping = {
                "char": rec.value_char,
                "text": rec.value_text,
                "integer": rec.value_integer and str(rec.value_integer) or "",
                "float": rec.value_float and str(rec.value_float) or "",
                "date": rec.value_date and str(rec.value_date) or "",
                "datetime": rec.value_datetime and str(rec.value_datetime) or "",
                "boolean": "Sim" if rec.value_boolean else "Não",
                "selection": rec.value_selection,
            }
            rec.display_value = mapping.get(ft) or ""

    @api.constrains("file_id", "field_id")
    def _check_field_type_match(self):
        for rec in self:
            if rec.field_id.document_type_id != rec.file_id.document_type_id:
                raise ValidationError(
                    "O campo de metadado pertence a um tipo diferente do arquivo."
                )
