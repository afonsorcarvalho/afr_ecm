"""Wrapper de OCR puro (sem Odoo) — extrai texto de PDF/imagens.

Dependências sistema: tesseract-ocr (+ langs), poppler-utils (pdftotext, pdftoppm).
Dependências Python: pytesseract, pdf2image, Pillow.

Estratégia:
- PDF nativo (texto embutido): pdftotext.
- PDF imagem (scaneado): pdf2image → tesseract por página.
- JPG/PNG/TIFF/BMP: tesseract direto.
- Outros mimetypes: caller decide (skip).
"""
import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

_logger = logging.getLogger(__name__)


SUPPORTED_IMAGE_MIMETYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/bmp",
    "image/gif",
}

PDF_MIMETYPES = {"application/pdf"}


@dataclass
class OcrResult:
    text: str = ""
    engine: str = "none"  # 'pdftotext', 'tesseract', 'native', 'none'
    pages: int = 0
    confidence: float = 0.0
    error: Optional[str] = None
    skipped: bool = False
    skipped_reason: Optional[str] = None


def is_supported_mimetype(mt: str) -> bool:
    if not mt:
        return False
    mt = mt.lower()
    return mt in PDF_MIMETYPES or mt in SUPPORTED_IMAGE_MIMETYPES


def _run(cmd: List[str], input_bytes: Optional[bytes] = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def extract_pdf_native_text(pdf_bytes: bytes, timeout: int = 60) -> str:
    """Tenta pdftotext (poppler-utils). Retorna string vazia se nada extraído."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext (poppler-utils) não encontrado no PATH.")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        proc = _run(
            ["pdftotext", "-layout", "-q", tmp.name, "-"],
            timeout=timeout,
        )
        if proc.returncode != 0:
            _logger.warning("pdftotext falhou rc=%s err=%s", proc.returncode, proc.stderr[:200])
            return ""
        return (proc.stdout or b"").decode("utf-8", errors="replace")


def ocr_image_bytes(
    image_bytes: bytes,
    languages: str = "por+eng",
    timeout: int = 120,
) -> tuple:
    """Roda tesseract numa imagem em memória. Retorna (text, mean_confidence)."""
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    text = pytesseract.image_to_string(img, lang=languages, timeout=timeout)
    # média de confidence via image_to_data (opcional, custo extra)
    try:
        data = pytesseract.image_to_data(
            img, lang=languages, output_type=pytesseract.Output.DICT, timeout=timeout
        )
        confs = [int(c) for c in data.get("conf", []) if str(c).isdigit() and int(c) >= 0]
        mean = sum(confs) / len(confs) if confs else 0.0
    except Exception:
        mean = 0.0
    return text, mean


def ocr_pdf_pages(
    pdf_bytes: bytes,
    languages: str = "por+eng",
    dpi: int = 200,
    max_pages: int = 50,
    timeout_per_page: int = 60,
) -> tuple:
    """Converte PDF em imagens (pdf2image) e roda tesseract por página.
    Retorna (text, pages_processed, mean_confidence).
    """
    from pdf2image import convert_from_bytes

    images = convert_from_bytes(
        pdf_bytes,
        dpi=dpi,
        first_page=1,
        last_page=max_pages,
        fmt="png",
    )
    parts = []
    confs = []
    for i, img in enumerate(images, start=1):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page_bytes = buf.getvalue()
        try:
            text, conf = ocr_image_bytes(
                page_bytes, languages=languages, timeout=timeout_per_page
            )
            parts.append(text)
            confs.append(conf)
        except Exception as e:
            _logger.exception("OCR falha página %d: %s", i, e)
            parts.append("")
    mean = sum(confs) / len(confs) if confs else 0.0
    return "\n\n".join(parts), len(images), mean


def extract(
    content_bytes: bytes,
    mimetype: str,
    languages: str = "por+eng",
    dpi: int = 200,
    max_pages: int = 50,
    min_chars_skip_ocr: int = 100,
) -> OcrResult:
    """Pipeline principal — decide engine e extrai texto.

    Retorna OcrResult sempre (sem raise) — error fica em result.error.
    """
    if not content_bytes:
        return OcrResult(skipped=True, skipped_reason="empty content")
    mt = (mimetype or "").lower()
    if not is_supported_mimetype(mt):
        return OcrResult(
            skipped=True,
            skipped_reason="unsupported mimetype: %s" % mt,
        )
    try:
        if mt in PDF_MIMETYPES:
            # tenta pdftotext primeiro
            try:
                native = extract_pdf_native_text(content_bytes)
            except Exception as e:
                _logger.warning("pdftotext error: %s", e)
                native = ""
            if native and len(native.strip()) >= min_chars_skip_ocr:
                # conta páginas via número de form-feeds (\f) que pdftotext insere
                pages = max(1, native.count("\f") + 1)
                return OcrResult(
                    text=native,
                    engine="pdftotext",
                    pages=pages,
                    confidence=100.0,
                )
            # cai pra OCR
            text, pages, conf = ocr_pdf_pages(
                content_bytes,
                languages=languages,
                dpi=dpi,
                max_pages=max_pages,
            )
            return OcrResult(
                text=text,
                engine="tesseract",
                pages=pages,
                confidence=conf,
            )
        # imagem
        text, conf = ocr_image_bytes(content_bytes, languages=languages)
        return OcrResult(
            text=text,
            engine="tesseract",
            pages=1,
            confidence=conf,
        )
    except Exception as e:
        _logger.exception("OCR extract falhou: %s", e)
        return OcrResult(error=str(e), engine="failed")
