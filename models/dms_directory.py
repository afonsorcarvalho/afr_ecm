from odoo import api, fields, models


class DmsDirectory(models.Model):
    _name = "dms.directory"
    _inherit = ["dms.directory", "afr.ecm.audit.mixin"]

    default_document_type_id = fields.Many2one(
        "afr.ecm.document.type",
        string="Tipo de Documento Padrão",
        help="Tipo sugerido para arquivos criados neste diretório.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        default_group = self.env.ref("afr_ecm.dms_access_group_ecm_default", raise_if_not_found=False)
        if default_group:
            for vals in vals_list:
                if vals.get("is_root_directory") and not vals.get("group_ids"):
                    vals["group_ids"] = [(4, default_group.id)]
        return super().create(vals_list)
