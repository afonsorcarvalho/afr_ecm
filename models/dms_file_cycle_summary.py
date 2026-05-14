"""F4.3.7 — Monthly sterilization cycle summary cron.

Cross-module integration: `afr_supervisorio_ciclos` → `afr_ecm`.

Architecture:
- Soft dependency: `afr_supervisorio_ciclos` is NOT added to `depends`.
  The cron degrades gracefully if the module is not installed.
- Target folder: 10_Operacao/Registros/04_Monitoramentos_Rotineiros/Resumos_Ciclos
  (looked up by `complete_name`, no hard-coded ID).
- One HTML summary file per equipment per closed calendar month.
- Idempotent: skips if a file with the target name already exists in the folder.
- Retention: 5 years from generation date (RDC 15 art. 100).

Cycle model: `afr.supervisorio.ciclos` (note: PLURAL — this is the actual registered
_name; `afr.supervisorio.ciclo` singular does NOT exist).

Fields used for aggregation:
  - start_date  (Datetime): cycle start timestamp, used for month filter
  - equipment_id (Many2one 'engc.equipment'): equipment grouping key
  - equipment_nickname (Char, related): human label for the table
  - state (Selection): concluido=success; erro|abortado=failed; cancelado=excluded
  - ib_resultado (Selection positivo|negativo): biological indicator result
  - duration (Float, hours): cycle duration for average calculation
  - batch_number (Char): lot identifier

Note — CI (chemical indicator) field: NOT present on `afr.supervisorio.ciclos`.
The model only tracks `ib_resultado` (biological indicator). CI data is embedded in
the raw cycle TXT file, not as a dedicated Odoo field. Aggregation therefore only
covers BI positive count, not CI failures.
"""

import base64
import logging
import re
import unicodedata
from datetime import datetime

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Suffix used in idempotency check — kept short for Char field limits.
_FILE_PREFIX = "CYCLES_SUMMARY"

# Retention period for generated summaries (RDC 15 art. 100 — 5 years).
_RETENTION_YEARS = 5

# Destination folder searched by complete_name suffix (configurable here).
_DEST_FOLDER_SUFFIX = "10_Operacao/Registros/04_Monitoramentos_Rotineiros/Resumos_Ciclos"

# States that count as "completed successfully".
_STATE_SUCCESS = ("concluido",)
# States that count as "failed" (process fault, not voluntary cancellation).
_STATE_FAILED = ("erro", "abortado")
# States excluded from aggregation altogether (operator-cancelled before start).
_STATE_EXCLUDED = ("cancelado",)


def _sanitize_slug(text):
    """Return a filesystem/filename-safe slug from an arbitrary string.

    Strips accents, replaces non-alphanumeric sequences with '_', collapses
    multiple underscores, and uppercases the result.

    Example: "Autoclave 01/A" → "AUTOCLAVE_01_A"
    """
    if not text:
        return "UNKNOWN"
    # Decompose unicode → strip combining characters (accents).
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Replace non-alphanumeric runs with underscore.
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_str)
    slug = slug.strip("_").upper()
    return slug or "UNKNOWN"


def _build_html_summary(company_name, equipment_label, year_month, stats):
    """Compose the HTML content for a monthly cycle summary file.

    Args:
        company_name (str): Empresa name for the report header.
        equipment_label (str): Equipment display label.
        year_month (str): "YYYY-MM" string for the report period.
        stats (dict): Aggregated statistics with keys:
            total, success, failed, excluded, bi_positive, avg_duration_h,
            cycles (list of dicts with name/state/batch_number/start_date/ib_resultado/duration)

    Returns:
        bytes: UTF-8 encoded HTML content ready for base64 encoding.
    """
    bi_pos = stats.get("bi_positive", 0)
    bi_pos_class = "danger" if bi_pos > 0 else "ok"

    # Build individual cycle rows (capped at 500 to avoid huge files).
    row_html = ""
    for c in stats.get("cycles", [])[:500]:
        state_label = {
            "concluido": "Concluído",
            "erro": "Erro",
            "abortado": "Abortado",
            "cancelado": "Cancelado",
            "em_andamento": "Em andamento",
            "aguardando": "Aguardando",
            "pausado": "Pausado",
        }.get(c.get("state", ""), c.get("state", ""))
        ib = c.get("ib_resultado") or "—"
        ib_class = "danger" if ib == "positivo" else ""
        duration = c.get("duration", 0) or 0
        batch = c.get("batch_number") or "—"
        start = c.get("start_date", "")
        if hasattr(start, "strftime"):
            start = start.strftime("%Y-%m-%d %H:%M")
        row_html += (
            f"<tr>"
            f"<td>{c.get('name', '')}</td>"
            f"<td>{start}</td>"
            f"<td>{state_label}</td>"
            f"<td>{batch}</td>"
            f"<td class='{ib_class}'>{ib}</td>"
            f"<td>{duration:.2f} h</td>"
            f"</tr>\n"
        )

    avg_dur = stats.get("avg_duration_h", 0) or 0
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<title>Sumário Mensal de Ciclos — {equipment_label} — {year_month}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 12px; margin: 20px; color: #222; }}
  h1 {{ font-size: 16px; color: #003366; }}
  h2 {{ font-size: 13px; color: #444; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th {{ background: #003366; color: #fff; padding: 5px 8px; text-align: left; }}
  td {{ border: 1px solid #ccc; padding: 4px 8px; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  .stat-box {{ display: inline-block; margin: 6px 12px 6px 0; padding: 8px 16px;
               border-radius: 4px; background: #eef2ff; border: 1px solid #99b; font-size: 13px; }}
  .danger {{ color: #c00; font-weight: bold; }}
  .ok {{ color: #060; }}
  .footer {{ margin-top: 24px; font-size: 11px; color: #777; border-top: 1px solid #ddd; padding-top: 8px; }}
</style>
</head>
<body>
<!-- [LOGO_PLACEHOLDER] -->
<h1>Sumário Mensal de Ciclos de Esterilização</h1>
<p><strong>Empresa:</strong> {company_name} &nbsp;|&nbsp;
   <strong>Equipamento:</strong> {equipment_label} &nbsp;|&nbsp;
   <strong>Período:</strong> {year_month}</p>

<h2>Estatísticas do Período</h2>
<div>
  <div class="stat-box"><strong>Total</strong><br/>{stats.get('total', 0)}</div>
  <div class="stat-box ok"><strong>Concluídos</strong><br/>{stats.get('success', 0)}</div>
  <div class="stat-box danger"><strong>Erro / Abortado</strong><br/>{stats.get('failed', 0)}</div>
  <div class="stat-box"><strong>Cancelados</strong><br/>{stats.get('excluded', 0)}</div>
  <div class="stat-box {bi_pos_class}"><strong>BI Positivo</strong><br/>{bi_pos}</div>
  <div class="stat-box"><strong>Duração média</strong><br/>{avg_dur:.2f} h</div>
</div>

<h2>Registros Individuais</h2>
<table>
<thead>
  <tr>
    <th>Ciclo</th><th>Início</th><th>Status</th><th>Lote</th>
    <th>IB</th><th>Duração</th>
  </tr>
</thead>
<tbody>
{row_html}
</tbody>
</table>

<div class="footer">
  <p><strong>Nota CI (Indicador Químico):</strong> O campo de resultado do Indicador Químico
  não está disponível como campo estruturado no modelo <code>afr.supervisorio.ciclos</code>.
  Os dados de CI estão contidos nos arquivos TXT de fita digital, não neste sumário.</p>
  <p>Gerado automaticamente em {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} pelo
  cron <em>AFR ECM — Sumário Mensal de Ciclos</em>.
  Retenção: 5 anos (RDC 15/2012 art. 100).</p>
  <p>___________________________________<br/>
  Responsável Técnico<br/>
  (Assinatura eletrônica via sistema)</p>
</div>
</body>
</html>"""
    return html.encode("utf-8")


class DmsFileCycleSummary(models.Model):
    """Extend dms.file with the monthly cycle summary cron (F4.3.7).

    No new stored fields — the cron creates plain dms.file records in the
    destination folder using only fields already on dms.file.
    """

    _inherit = "dms.file"

    @api.model
    def _cron_monthly_cycle_summary(self):
        """Generate one HTML summary file per equipment for the previous month.

        Runs daily; on or after day 1 of each month, generates the previous
        month's summary if not already present in the destination folder.

        Idempotent: a file named ``CYCLES_SUMMARY_<EQ>_<YYYY-MM>.html``
        already present in the destination folder causes the equipment to be
        skipped.

        Graceful degradation:
          - If ``afr_supervisorio_ciclos`` is not installed: logs warning, returns [].
          - If the destination folder is not found: logs warning, returns [].

        Returns:
            list[int]: IDs of newly created dms.file records (empty when all
                       skipped or when prerequisites are missing).
        """
        # ── 1. Resolve the cycle model (soft dependency) ─────────────────────
        CycleModel = self.env.get("afr.supervisorio.ciclos")
        if CycleModel is None:
            _logger.warning(
                "afr_ecm F4.3.7: model 'afr.supervisorio.ciclos' not in registry "
                "(afr_supervisorio_ciclos not installed?). Skipping cycle summary cron."
            )
            return []

        # ── 2. Determine target month (previous calendar month) ───────────────
        today = fields.Date.today()
        # First day of current month → subtract one day → first day of prev month.
        first_of_current = today.replace(day=1)
        first_of_prev = first_of_current - relativedelta(months=1)
        first_of_next = first_of_current  # exclusive upper bound

        year_month = first_of_prev.strftime("%Y-%m")
        _logger.info(
            "afr_ecm F4.3.7: generating cycle summaries for period %s", year_month
        )

        # ── 3. Find the destination folder ────────────────────────────────────
        dest_folder = self._cycle_summary_destination_folder()
        if not dest_folder:
            _logger.warning(
                "afr_ecm F4.3.7: destination folder not found "
                "(complete_name ending '%s'). Skipping.", _DEST_FOLDER_SUFFIX
            )
            return []

        # ── 4. Aggregate cycles per equipment ─────────────────────────────────
        month_domain = [
            ("start_date", ">=", fields.Datetime.to_string(
                datetime.combine(first_of_prev, datetime.min.time())
            )),
            ("start_date", "<", fields.Datetime.to_string(
                datetime.combine(first_of_next, datetime.min.time())
            )),
        ]

        # Collect all cycles for the target month.
        all_cycles = CycleModel.sudo().search(month_domain, order="equipment_id, start_date")

        if not all_cycles:
            _logger.info(
                "afr_ecm F4.3.7: no cycles found for %s — nothing to summarise.",
                year_month,
            )
            return []

        # Group by equipment_id in Python (read_group alternative; simpler for
        # per-state counting without multiple DB queries).
        from collections import defaultdict

        by_equipment = defaultdict(list)
        for cycle in all_cycles:
            by_equipment[cycle.equipment_id.id].append(cycle)

        company = self.env.company
        company_name = company.name or "CME"

        # Expiration: today + 5 years.
        expiration_date = today + relativedelta(years=_RETENTION_YEARS)

        created_ids = []

        for equipment_id_int, cycles in by_equipment.items():
            # Use the first cycle to get equipment record.
            eq = cycles[0].equipment_id
            eq_label = eq.apelido or eq.name or str(equipment_id_int)
            eq_slug = _sanitize_slug(eq_label)

            target_name = f"{_FILE_PREFIX}_{eq_slug}_{year_month}.html"

            # ── 5. Idempotency check ──────────────────────────────────────────
            existing = self.sudo().search([
                ("name", "=", target_name),
                ("directory_id", "=", dest_folder.id),
            ], limit=1)
            if existing:
                _logger.info(
                    "afr_ecm F4.3.7: file '%s' already exists (id=%s) — skipping.",
                    target_name, existing.id,
                )
                continue

            # ── 6. Build statistics ───────────────────────────────────────────
            success_cycles = [c for c in cycles if c.state in _STATE_SUCCESS]
            failed_cycles = [c for c in cycles if c.state in _STATE_FAILED]
            excluded_cycles = [c for c in cycles if c.state in _STATE_EXCLUDED]

            bi_positive = sum(
                1 for c in cycles if c.ib_resultado == "positivo"
            )

            durations = [c.duration for c in cycles if c.duration]
            avg_duration_h = sum(durations) / len(durations) if durations else 0.0

            cycle_rows = []
            for c in cycles:
                cycle_rows.append({
                    "name": c.name,
                    "state": c.state,
                    "batch_number": c.batch_number,
                    "start_date": c.start_date,
                    "ib_resultado": c.ib_resultado,
                    "duration": c.duration,
                })

            stats = {
                "total": len(cycles),
                "success": len(success_cycles),
                "failed": len(failed_cycles),
                "excluded": len(excluded_cycles),
                "bi_positive": bi_positive,
                "avg_duration_h": avg_duration_h,
                "cycles": cycle_rows,
            }

            # ── 7. Compose HTML and encode ────────────────────────────────────
            html_bytes = _build_html_summary(company_name, eq_label, year_month, stats)
            content_b64 = base64.b64encode(html_bytes).decode("ascii")

            # ── 8. Create dms.file record ─────────────────────────────────────
            # document_type_id is intentionally omitted: the 'OP_CYCLE_SUMMARY'
            # and 'OP_MON_BD' codes are NOT seeded yet in document_type_data.xml.
            # A future task (seed OP_CYCLE_SUMMARY doc type) can back-fill this.
            vals = {
                "name": target_name,
                "directory_id": dest_folder.id,
                "content": content_b64,
                "confidentiality": "internal",
                "expiration_date": expiration_date,
            }

            try:
                new_file = self.sudo().create(vals)
                created_ids.append(new_file.id)
                _logger.info(
                    "afr_ecm F4.3.7: created summary '%s' (id=%s) for equipment '%s' "
                    "period %s.", target_name, new_file.id, eq_label, year_month,
                )
            except Exception as exc:
                _logger.error(
                    "afr_ecm F4.3.7: failed to create summary '%s' for equipment '%s': %s",
                    target_name, eq_label, exc,
                )

        _logger.info(
            "afr_ecm F4.3.7: cycle summary cron completed. Created %d file(s) for %s.",
            len(created_ids), year_month,
        )
        return created_ids

    @api.model
    def _cycle_summary_destination_folder(self):
        """Return the destination dms.directory for cycle summary files.

        Lookup strategy (in order):
          1. Search by complete_name ending with _DEST_FOLDER_SUFFIX.
          2. Search by name == 'Resumos_Ciclos' as fallback.

        Returns dms.directory record or empty recordset if not found.
        """
        Directory = self.env["dms.directory"].sudo()

        # Strategy 1: match by complete_name suffix.
        folders = Directory.search([
            ("complete_name", "ilike", "Resumos_Ciclos"),
        ])
        # Filter to the one whose complete_name ends with our expected suffix.
        for folder in folders:
            cn = (folder.complete_name or "").replace(" ", "")
            suffix_clean = _DEST_FOLDER_SUFFIX.replace(" ", "")
            if cn.endswith(suffix_clean):
                return folder

        # Strategy 2: fallback — any folder named exactly 'Resumos_Ciclos'.
        fallback = Directory.search([("name", "=", "Resumos_Ciclos")], limit=1)
        if fallback:
            _logger.warning(
                "afr_ecm F4.3.7: could not match full path '%s'; "
                "falling back to first folder named 'Resumos_Ciclos' (id=%s).",
                _DEST_FOLDER_SUFFIX, fallback.id,
            )
            return fallback

        return Directory  # empty recordset
