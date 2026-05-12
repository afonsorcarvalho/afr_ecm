from odoo import _, api, fields, models
from odoo.exceptions import UserError


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

    def unlink(self):
        """Bloqueia exclusão de diretório não-vazio.

        OCA dms tem `ondelete="restrict"` em parent_id de files/dirs, mas isso
        só dispara via constraint SQL — frameworks podem ignorar quando a
        instância está com `active=False`. Validamos explicitamente aqui
        (considerando registros archivados também).
        """
        # `active_test=False` para incluir filhos archivados
        ctx_off = self.with_context(active_test=False)
        for rec in ctx_off:
            child_dirs = rec.child_directory_ids
            files = self.env["dms.file"].with_context(active_test=False).search_count(
                [("directory_id", "=", rec.id)]
            )
            if child_dirs or files:
                raise UserError(_(
                    "Não é possível excluir a pasta '%(name)s': contém %(d)d subpasta(s) "
                    "e %(f)d arquivo(s) (incluindo lixeira). Esvazie antes de excluir."
                ) % {"name": rec.name, "d": len(child_dirs), "f": files})
        return super().unlink()
