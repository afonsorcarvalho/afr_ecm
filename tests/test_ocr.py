import base64
import io
import uuid

from odoo.tests.common import Form, TransactionCase, tagged

from ..services import ocr_engine


def _make_simple_pdf_native(text="Documento de teste ECM 12345"):
    """PDF nativo com texto embutido (reportlab — já vem no Odoo)."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 700, text)
    c.save()
    return buf.getvalue()


def _make_png_with_text(text="HELLO OCR"):
    """PNG grande com texto preto sobre fundo branco — tesseract consegue ler."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(img)
    # tenta fonte do sistema
    font = None
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            font = ImageFont.truetype(path, 60)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((30, 60), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@tagged("post_install", "-at_install", "afr_ecm")
class TestOcrEngine(TransactionCase):
    """Testes do wrapper puro services/ocr_engine.py — sem Odoo orm."""

    def test_is_supported_mimetype(self):
        self.assertTrue(ocr_engine.is_supported_mimetype("application/pdf"))
        self.assertTrue(ocr_engine.is_supported_mimetype("image/png"))
        self.assertTrue(ocr_engine.is_supported_mimetype("image/jpeg"))
        self.assertFalse(ocr_engine.is_supported_mimetype("text/plain"))
        self.assertFalse(ocr_engine.is_supported_mimetype(""))
        self.assertFalse(ocr_engine.is_supported_mimetype(None))

    def test_extract_pdf_native(self):
        pdf = _make_simple_pdf_native("Texto Nativo 99887")
        # threshold baixo para garantir caminho pdftotext mesmo com texto curto
        result = ocr_engine.extract(pdf, "application/pdf", min_chars_skip_ocr=5)
        self.assertEqual(result.engine, "pdftotext")
        self.assertIn("99887", result.text)
        self.assertGreaterEqual(result.pages, 1)
        self.assertFalse(result.skipped)
        self.assertIsNone(result.error)

    def test_extract_png_tesseract(self):
        png = _make_png_with_text("HELLO OCR")
        result = ocr_engine.extract(png, "image/png", languages="eng")
        self.assertEqual(result.engine, "tesseract")
        # tesseract pode ter pequenas variações; conferimos chave parcial
        self.assertIn("HELLO", result.text.upper())

    def test_extract_unsupported_mimetype(self):
        result = ocr_engine.extract(b"some data", "text/plain")
        self.assertTrue(result.skipped)
        self.assertIsNotNone(result.skipped_reason)

    def test_extract_empty_content(self):
        result = ocr_engine.extract(b"", "application/pdf")
        self.assertTrue(result.skipped)


@tagged("post_install", "-at_install", "afr_ecm")
class TestOcrDmsFile(TransactionCase):
    """Integração: dms.file dispara OCR quando tipo ocr_enabled."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.DocType = cls.env["afr.ecm.document.type"]
        cls.File = cls.env["dms.file"]

        cls.access_group = cls.env["dms.access.group"].create({
            "name": "OCR Test ACL",
            "perm_create": True,
            "perm_write": True,
            "perm_unlink": True,
            "group_ids": [(4, cls.env.ref("afr_ecm.group_ecm_user").id)],
        })
        cls.storage = cls.env["dms.storage"].create({
            "name": "OCR Storage",
            "save_type": "database",
        })
        dir_form = Form(cls.env["dms.directory"])
        dir_form.name = uuid.uuid4().hex
        dir_form.is_root_directory = True
        dir_form.storage_id = cls.storage
        dir_form.group_ids.add(cls.access_group)
        cls.directory = dir_form.save()

        cls.dt_ocr = cls.DocType.create({
            "name": "OCR habilitado",
            "code": "test_ocr_on",
            "ocr_enabled": True,
        })
        cls.dt_no_ocr = cls.DocType.create({
            "name": "OCR desabilitado",
            "code": "test_ocr_off",
            "ocr_enabled": False,
        })

    def _create_file(self, content_bytes, name, doc_type):
        # force sync via queue_job__no_delay context
        File = self.File.with_context(queue_job__no_delay=True)
        return File.create({
            "name": name,
            "directory_id": self.directory.id,
            "content": base64.b64encode(content_bytes),
            "document_type_id": doc_type.id,
        })

    def test_pdf_ocr_done(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "afr_ecm.ocr.min_chars_skip", "5"
        )
        pdf = _make_simple_pdf_native("Numero 77665")
        f = self._create_file(pdf, "doc.pdf", self.dt_ocr)
        self.assertEqual(f.ocr_state, "done")
        self.assertEqual(f.ocr_engine, "pdftotext")
        self.assertIn("77665", f.ocr_text or "")
        self.assertTrue(f.ocr_content_hash)

    def test_png_ocr_done(self):
        png = _make_png_with_text("HELLO OCR")
        # passa idioma eng pra fonte latina simples
        self.env["ir.config_parameter"].sudo().set_param(
            "afr_ecm.ocr.languages", "eng"
        )
        f = self._create_file(png, "img.png", self.dt_ocr)
        self.assertEqual(f.ocr_state, "done")
        self.assertEqual(f.ocr_engine, "tesseract")
        self.assertIn("HELLO", (f.ocr_text or "").upper())

    def test_type_without_ocr_no_state(self):
        pdf = _make_simple_pdf_native("nao deve processar")
        f = self._create_file(pdf, "n.pdf", self.dt_no_ocr)
        self.assertFalse(f.ocr_state, "tipo sem ocr_enabled não dispara")

    def test_reprocess_ocr_button(self):
        pdf = _make_simple_pdf_native("primeiro hash")
        f = self._create_file(pdf, "rep.pdf", self.dt_ocr)
        self.assertEqual(f.ocr_state, "done")
        first_hash = f.ocr_content_hash
        # reprocess force
        f.with_context(queue_job__no_delay=True).action_reprocess_ocr()
        self.assertEqual(f.ocr_state, "done")
        self.assertEqual(f.ocr_content_hash, first_hash, "hash mesmo conteúdo")

    def test_reprocess_raises_when_not_eligible(self):
        pdf = _make_simple_pdf_native("x")
        f = self._create_file(pdf, "noteligible.pdf", self.dt_no_ocr)
        # ocr_state é False — botão exige eligible
        with self.assertRaises(Exception):
            f.action_reprocess_ocr()

    def test_global_disabled_skips(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "afr_ecm.ocr.enabled", "False"
        )
        try:
            pdf = _make_simple_pdf_native("global off")
            f = self._create_file(pdf, "off.pdf", self.dt_ocr)
            self.assertFalse(f.ocr_state)
        finally:
            self.env["ir.config_parameter"].sudo().set_param(
                "afr_ecm.ocr.enabled", "True"
            )

    def test_cron_backlog_redispatches_failed(self):
        pdf = _make_simple_pdf_native("backlog test")
        f = self._create_file(pdf, "b.pdf", self.dt_ocr)
        # força state=failed
        f.sudo().write({"ocr_state": "failed", "ocr_text": False, "ocr_content_hash": False})
        self.File.with_context(queue_job__no_delay=True)._cron_ocr_backlog()
        f.invalidate_recordset()
        self.assertEqual(f.ocr_state, "done")
