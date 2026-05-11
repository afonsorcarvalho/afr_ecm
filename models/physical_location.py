import base64
import io
import logging
import uuid

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

try:
    import qrcode
except ImportError:
    qrcode = None
    _logger.info("qrcode lib not installed — QR generation disabled.")


class AfrEcmPhysicalLocation(models.Model):
    _name = "afr.ecm.physical.location"
    _description = "Localização Física ECM"
    _inherit = ["afr.ecm.audit.mixin", "mail.thread"]
    _parent_store = True
    _parent_name = "parent_id"
    _order = "complete_path"
    _rec_name = "complete_path"

    LOCATION_TYPES = [
        ("archive", "Arquivo"),
        ("room", "Sala"),
        ("shelf", "Estante"),
        ("box", "Caixa"),
        ("folder", "Pasta"),
        ("other", "Outro"),
    ]

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(string="Código", index=True, tracking=True)
    location_type = fields.Selection(LOCATION_TYPES, default="box", required=True, tracking=True)
    parent_id = fields.Many2one(
        "afr.ecm.physical.location",
        string="Localização Pai",
        ondelete="restrict",
        index=True,
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("afr.ecm.physical.location", "parent_id", string="Filhos")
    complete_path = fields.Char(compute="_compute_complete_path", store=True, recursive=True)
    barcode = fields.Char(
        string="Código QR/Barcode",
        copy=False,
        index=True,
        help="Identificador único usado em etiquetas QR.",
    )
    note = fields.Html(string="Observações")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )

    file_ids = fields.One2many(
        "dms.file",
        "physical_location_id",
        string="Arquivos",
    )
    file_count = fields.Integer(compute="_compute_file_count")
    qr_image = fields.Binary(compute="_compute_qr_image", string="Imagem QR")

    _sql_constraints = [
        ("barcode_uniq", "unique(barcode)", "Barcode deve ser único."),
        ("code_uniq", "unique(code, company_id)", "Código deve ser único por empresa."),
    ]

    @api.depends("name", "parent_id.complete_path")
    def _compute_complete_path(self):
        for rec in self:
            if rec.parent_id:
                rec.complete_path = f"{rec.parent_id.complete_path} / {rec.name}"
            else:
                rec.complete_path = rec.name or ""

    @api.depends("file_ids")
    def _compute_file_count(self):
        for rec in self:
            rec.file_count = len(rec.file_ids)

    @api.depends("barcode", "code")
    def _compute_qr_image(self):
        for rec in self:
            rec.qr_image = rec._render_qr_png()

    def _qr_payload(self):
        self.ensure_one()
        return self.barcode or self.code or f"ECM-LOC-{self.id}"

    def _render_qr_png(self):
        self.ensure_one()
        if not qrcode:
            return False
        try:
            img = qrcode.make(self._qr_payload())
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue())
        except Exception as exc:
            _logger.warning("QR render failed for %s: %s", self.display_name, exc)
            return False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("barcode"):
                vals["barcode"] = self._next_barcode()
        return super().create(vals_list)

    @api.model
    def _next_barcode(self):
        seq = self.env["ir.sequence"].sudo().next_by_code("afr.ecm.physical.location")
        if seq:
            return seq
        return f"ECM-LOC-{uuid.uuid4().hex[:12].upper()}"

    def action_print_label(self):
        return self.env.ref("afr_ecm.action_report_location_label").report_action(self)

    def action_view_files(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Arquivos",
            "res_model": "dms.file",
            "view_mode": "tree,form",
            "domain": [("physical_location_id", "=", self.id)],
        }
