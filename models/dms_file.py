import base64
import hashlib
import logging
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .document_type import CONFIDENTIALITY
from ..services import ocr_engine


_logger = logging.getLogger(__name__)


APPROVAL_STATE = [
    ("draft", "Rascunho"),
    ("pending", "Em Aprovação"),
    ("approved", "Aprovado"),
    ("rejected", "Rejeitado"),
]

EXPIRATION_STATUS = [
    ("none", "Sem vencimento"),
    ("ok", "Em dia"),
    ("warning", "Atenção"),
    ("critical", "Crítico"),
    ("expired", "Vencido"),
]

OCR_STATE = [
    ("pending", "Pendente"),
    ("processing", "Processando"),
    ("done", "Concluído"),
    ("failed", "Falhou"),
    ("skipped", "Pulado"),
]

# Campos cuja escrita NÃO é considerada alteração de conteúdo
# (logging, chatter, activities, audit, próprio workflow, expiração).
_APPROVAL_META_FIELDS = frozenset(
    [
        "approval_state",
        "current_level_id",
        "approval_action_ids",
        "last_expiration_alert",
        "ocr_state",
        "ocr_text",
        "ocr_processed_at",
        "ocr_engine",
        "ocr_pages",
        "ocr_confidence",
        "ocr_error",
        "ocr_content_hash",
        "message_ids",
        "message_follower_ids",
        "message_partner_ids",
        "message_main_attachment_id",
        "message_is_follower",
        "activity_ids",
        "activity_state",
        "activity_user_id",
        "activity_type_id",
        "activity_date_deadline",
        "activity_summary",
        "activity_exception_decoration",
        "activity_exception_icon",
        "write_date",
        "write_uid",
    ]
)


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
        tracking=True,
        help="Data de vencimento do documento. Cron alerta nas janelas configuradas.",
    )
    days_to_expire = fields.Integer(
        string="Dias até Vencer",
        compute="_compute_expiration_status",
    )
    expiration_status = fields.Selection(
        EXPIRATION_STATUS,
        string="Status Vencimento",
        compute="_compute_expiration_status",
        search="_search_expiration_status",
    )
    last_expiration_alert = fields.Date(
        string="Último Alerta Vencimento",
        copy=False,
        help="Data do último alerta disparado pelo cron — anti-duplicação.",
    )

    # ----- OCR -----
    ocr_state = fields.Selection(
        OCR_STATE,
        string="Status OCR",
        index=True,
        copy=False,
        tracking=True,
    )
    ocr_text = fields.Text(string="Texto extraído (OCR)", copy=False)
    ocr_processed_at = fields.Datetime(string="OCR processado em", copy=False)
    ocr_engine = fields.Char(string="OCR engine", copy=False)
    ocr_pages = fields.Integer(string="OCR páginas", copy=False)
    ocr_confidence = fields.Float(string="OCR confiança", copy=False)
    ocr_error = fields.Text(string="OCR erro", copy=False)
    ocr_content_hash = fields.Char(
        string="OCR hash conteúdo", index=True, copy=False, size=64,
    )

    approval_state = fields.Selection(
        APPROVAL_STATE,
        string="Status Aprovação",
        index=True,
        tracking=True,
        copy=False,
    )
    current_level_id = fields.Many2one(
        "afr.ecm.approval.level",
        string="Nível Atual",
        copy=False,
        ondelete="set null",
    )
    approval_action_ids = fields.One2many(
        "afr.ecm.approval.action",
        "file_id",
        string="Histórico de Aprovações",
    )

    # ------------------------------------------------------------------
    # Onchange / overrides básicos
    # ------------------------------------------------------------------
    @api.onchange("document_type_id")
    def _onchange_document_type_id(self):
        for rec in self:
            if rec.document_type_id:
                if not rec.confidentiality or rec.confidentiality == "internal":
                    rec.confidentiality = rec.document_type_id.default_confidentiality
                if rec.document_type_id.default_directory_id and not rec.directory_id:
                    rec.directory_id = rec.document_type_id.default_directory_id
                if (
                    not rec.expiration_date
                    and rec.document_type_id.retention_days
                    and rec.document_type_id.retention_days > 0
                ):
                    rec.expiration_date = fields.Date.today() + timedelta(
                        days=rec.document_type_id.retention_days
                    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if (
                rec.document_type_id
                and rec.document_type_id.requires_approval
                and not rec.approval_state
            ):
                rec.approval_state = "draft"
        # OCR: dispara para os elegíveis
        records._ocr_dispatch()
        return records

    def write(self, vals):
        # Bloqueia edição de conteúdo em arquivos approved.
        # Bypass apenas via sudo() (workflow interno) ou superuser.
        if self and not self.env.su:
            forbidden = set(vals) - _APPROVAL_META_FIELDS
            if forbidden:
                approved = self.filtered(lambda r: r.approval_state == "approved")
                if approved:
                    raise UserError(
                        _(
                            "Arquivo aprovado é imutável. "
                            "Clique em 'Reabrir' para voltar ao rascunho antes de editar. "
                            "Campos bloqueados: %s"
                        )
                        % ", ".join(sorted(forbidden))
                    )
        res = super().write(vals)
        # OCR: re-dispatch se o conteúdo (ou tipo) mudou
        ocr_trigger_fields = {"content", "content_binary", "content_file", "document_type_id"}
        if ocr_trigger_fields & set(vals.keys()):
            self._ocr_dispatch()
        return res

    def _audit_log_view(self):
        Log = self.env["afr.ecm.audit.log"].sudo()
        for rec in self:
            Log.log("view", rec)

    # ------------------------------------------------------------------
    # Workflow de aprovação
    # ------------------------------------------------------------------
    def _approval_levels(self):
        self.ensure_one()
        return self.document_type_id.approval_level_ids.filtered("active").sorted(
            key=lambda l: (l.sequence, l.id)
        )

    def _approval_first_level(self):
        levels = self._approval_levels()
        return levels[:1]

    def _approval_next_level(self):
        self.ensure_one()
        levels = list(self._approval_levels())
        if not self.current_level_id or self.current_level_id not in levels:
            return self.env["afr.ecm.approval.level"]
        idx = levels.index(self.current_level_id)
        if idx + 1 < len(levels):
            return levels[idx + 1]
        return self.env["afr.ecm.approval.level"]

    def _approval_consensus_satisfied(self):
        """True se o nível atual já tem aprovações suficientes (any/all)."""
        self.ensure_one()
        level = self.current_level_id
        if not level:
            return False
        approvers = self.approval_action_ids.filtered(
            lambda a: a.action == "approve" and a.level_id == level
        ).user_id
        if level.consensus == "any":
            return bool(approvers)
        # all: todos os esperados (vivos no momento) devem ter aprovado
        expected = level._expected_approver_users()
        return bool(expected) and (expected <= approvers)

    def _approval_log(self, action, level=None, note=None):
        self.ensure_one()
        # captura uid ANTES do sudo (sudo() troca env.uid para SUPERUSER)
        uid = self.env.uid
        return self.env["afr.ecm.approval.action"].sudo().create(
            {
                "file_id": self.id,
                "level_id": (level or self.current_level_id).id or False,
                "action": action,
                "user_id": uid,
                "note": note or False,
            }
        )

    _APPROVAL_ACTIVITY_XMLID = "afr_ecm.mail_activity_data_approval"

    def _approval_create_activity(self):
        """Cria mail.activity 'Aprovar' para os usuários esperados do nível atual."""
        self.ensure_one()
        if not self.current_level_id:
            return
        act_type = self.env.ref(self._APPROVAL_ACTIVITY_XMLID, raise_if_not_found=False)
        if not act_type:
            return
        users = self.current_level_id._expected_approver_users()
        for user in users:
            self.activity_schedule(
                self._APPROVAL_ACTIVITY_XMLID,
                user_id=user.id,
                summary=_("Aprovar documento: %s") % (self.name or ""),
                note=_("Nível: %s") % (self.current_level_id.name or ""),
            )

    def _approval_clear_activities(self):
        """Remove activities pendentes de aprovação para todos usuários."""
        self.ensure_one()
        act_type = self.env.ref(self._APPROVAL_ACTIVITY_XMLID, raise_if_not_found=False)
        if not act_type:
            return
        self.activity_ids.filtered(lambda a: a.activity_type_id == act_type).unlink()

    # --- ações expostas no botão ---
    def action_submit_for_approval(self):
        for rec in self:
            if rec.approval_state not in ("draft",):
                raise UserError(_("Só é possível submeter rascunhos."))
            first = rec._approval_first_level()
            if not first:
                raise UserError(
                    _("Tipo de documento '%s' não possui níveis de aprovação configurados.")
                    % (rec.document_type_id.name or "")
                )
            rec.sudo().write(
                {
                    "approval_state": "pending",
                    "current_level_id": first.id,
                }
            )
            rec._approval_log("submit", level=first)
            rec.sudo()._approval_create_activity()
        return True

    def action_approve(self):
        for rec in self:
            if rec.approval_state != "pending":
                raise UserError(_("Documento não está em aprovação."))
            level = rec.current_level_id
            if not level:
                raise UserError(_("Sem nível de aprovação atual."))
            if not level._can_user_approve(self.env.user):
                raise UserError(
                    _("Você não está autorizado a aprovar o nível '%s'.") % level.name
                )
            # impede aprovação dupla pelo mesmo user no mesmo nível
            already = rec.approval_action_ids.filtered(
                lambda a: a.action == "approve"
                and a.level_id == level
                and a.user_id == self.env.user
            )
            if already:
                raise UserError(_("Você já aprovou este nível."))
            rec._approval_log("approve", level=level)
            # remove activity deste user
            act_type = self.env.ref(self._APPROVAL_ACTIVITY_XMLID, raise_if_not_found=False)
            if act_type:
                rec.sudo().activity_ids.filtered(
                    lambda a: a.activity_type_id == act_type
                    and a.user_id == self.env.user
                ).unlink()
            if rec._approval_consensus_satisfied():
                next_level = rec._approval_next_level()
                if next_level:
                    rec.sudo().write({"current_level_id": next_level.id})
                    rec.sudo()._approval_clear_activities()
                    rec.sudo()._approval_create_activity()
                else:
                    rec.sudo().write(
                        {
                            "approval_state": "approved",
                            "current_level_id": False,
                        }
                    )
                    rec.sudo()._approval_clear_activities()
        return True

    def action_reject(self):
        for rec in self:
            if rec.approval_state != "pending":
                raise UserError(_("Documento não está em aprovação."))
            level = rec.current_level_id
            if level and not level._can_user_approve(self.env.user):
                raise UserError(
                    _("Você não está autorizado a rejeitar o nível '%s'.") % level.name
                )
            rec._approval_log("reject", level=level)
            rec.sudo().write(
                {
                    "approval_state": "rejected",
                    "current_level_id": False,
                }
            )
            rec.sudo()._approval_clear_activities()
        return True

    def action_reopen(self):
        for rec in self:
            if rec.approval_state not in ("rejected", "approved"):
                raise UserError(_("Só rejeitados ou aprovados podem ser reabertos."))
            is_admin = self.env.user.has_group("afr_ecm.group_ecm_admin")
            is_manager = self.env.user.has_group("afr_ecm.group_ecm_manager")
            is_author = rec.create_uid == self.env.user
            if not (is_admin or is_manager or is_author):
                raise UserError(
                    _("Apenas o autor, gestor ou administrador ECM pode reabrir.")
                )
            rec._approval_log("reopen")
            rec.sudo().write(
                {
                    "approval_state": "draft",
                    "current_level_id": False,
                }
            )
            rec.sudo()._approval_clear_activities()
        return True

    # ------------------------------------------------------------------
    # Vencimento — computed + cron
    # ------------------------------------------------------------------
    _EXPIRATION_ACTIVITY_XMLID = "afr_ecm.mail_activity_data_expiration"
    _EXPIRATION_PARAM_KEY = "afr_ecm.expiration_alert_days"

    @api.depends("expiration_date")
    def _compute_expiration_status(self):
        today = fields.Date.today()
        for rec in self:
            if not rec.expiration_date:
                rec.days_to_expire = 0
                rec.expiration_status = "none"
                continue
            delta = (rec.expiration_date - today).days
            rec.days_to_expire = delta
            if delta < 0:
                rec.expiration_status = "expired"
            elif delta <= 7:
                rec.expiration_status = "critical"
            elif delta <= 30:
                rec.expiration_status = "warning"
            else:
                rec.expiration_status = "ok"

    def _search_expiration_status(self, operator, value):
        today = fields.Date.today()
        domains = {
            "none": [("expiration_date", "=", False)],
            "expired": [("expiration_date", "<", today)],
            "critical": [
                ("expiration_date", ">=", today),
                ("expiration_date", "<=", today + timedelta(days=7)),
            ],
            "warning": [
                ("expiration_date", ">", today + timedelta(days=7)),
                ("expiration_date", "<=", today + timedelta(days=30)),
            ],
            "ok": [("expiration_date", ">", today + timedelta(days=30))],
        }
        if operator not in ("=", "!=", "in", "not in"):
            return [("id", "=", 0)]
        wanted = value if isinstance(value, (list, tuple)) else [value]
        if operator in ("!=", "not in"):
            wanted = [k for k in domains.keys() if k not in wanted]
        if not wanted:
            return [("id", "=", 0)]
        result = []
        for i, key in enumerate(wanted):
            if key not in domains:
                continue
            sub = domains[key]
            if i == 0:
                result = sub
            else:
                result = ["|"] + result + sub
        return result or [("id", "=", 0)]

    @api.model
    def _get_expiration_alert_days(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            self._EXPIRATION_PARAM_KEY, "30,7,0"
        )
        out = []
        for part in (raw or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                _logger.warning(
                    "afr_ecm: valor inválido em %s: %r",
                    self._EXPIRATION_PARAM_KEY, part,
                )
        return sorted(set(out), reverse=True)

    def _expiration_recipients_followers(self):
        self.ensure_one()
        return self.message_partner_ids

    def _expiration_recipients_managers(self):
        group = self.env.ref("afr_ecm.group_ecm_manager", raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        return group.users.filtered("active")

    def _send_expiration_alert(self, days_left):
        """Posta no chatter (email aos followers via mail.thread) +
        cria activity para gestores ECM."""
        self.ensure_one()
        if days_left < 0:
            subject = _("Documento vencido: %s") % (self.name or "")
            body = _(
                "<p>O documento <b>%s</b> está <b>vencido</b> há %d dia(s).</p>"
                "<p>Vencimento: %s</p>"
            ) % (self.name or "", -days_left, self.expiration_date)
        elif days_left == 0:
            subject = _("Documento vence hoje: %s") % (self.name or "")
            body = _(
                "<p>O documento <b>%s</b> <b>vence hoje</b>.</p>"
            ) % (self.name or "")
        else:
            subject = _(
                "Documento vence em %d dia(s): %s"
            ) % (days_left, self.name or "")
            body = _(
                "<p>O documento <b>%s</b> vence em <b>%d dia(s)</b>.</p>"
                "<p>Vencimento: %s</p>"
            ) % (self.name or "", days_left, self.expiration_date)

        self.message_post(
            subject=subject,
            body=body,
            message_type="notification",
            subtype_xmlid="mail.mt_comment",
            partner_ids=self._expiration_recipients_followers().ids,
        )

        act_type = self.env.ref(
            self._EXPIRATION_ACTIVITY_XMLID, raise_if_not_found=False
        )
        if act_type:
            for user in self._expiration_recipients_managers():
                self.activity_schedule(
                    self._EXPIRATION_ACTIVITY_XMLID,
                    user_id=user.id,
                    summary=subject,
                    note=body,
                )

    @api.model
    def _cron_check_expirations(self, today=None):
        """Cron diário: alerta nas janelas configuradas em
        ir.config_parameter `afr_ecm.expiration_alert_days` (CSV).
        Campo `last_expiration_alert` evita duplicação no mesmo dia.
        """
        today = today or fields.Date.today()
        windows = self._get_expiration_alert_days()
        if not windows:
            return 0
        max_window = max(max(windows), 0)
        domain = [
            ("expiration_date", "!=", False),
            ("expiration_date", "<=", today + timedelta(days=max_window)),
            "|",
            ("last_expiration_alert", "=", False),
            ("last_expiration_alert", "<", today),
        ]
        domain += [
            "|", ("approval_state", "=", False),
            ("approval_state", "!=", "rejected"),
        ]
        files = self.sudo().search(domain)
        sent = 0
        for f in files:
            delta = (f.expiration_date - today).days
            if delta >= 0 and delta not in windows:
                continue
            try:
                f._send_expiration_alert(delta)
                f.last_expiration_alert = today
                sent += 1
            except Exception as e:
                _logger.exception(
                    "afr_ecm: falha ao alertar vencimento de dms.file id=%s: %s",
                    f.id, e,
                )
        return sent

    # ------------------------------------------------------------------
    # OCR — opt-in via document_type.ocr_enabled
    # ------------------------------------------------------------------
    _OCR_ENABLED_KEY = "afr_ecm.ocr.enabled"
    _OCR_LANG_KEY = "afr_ecm.ocr.languages"
    _OCR_MAX_PAGES_KEY = "afr_ecm.ocr.max_pages"
    _OCR_MIN_CHARS_KEY = "afr_ecm.ocr.min_chars_skip"
    _OCR_DPI_KEY = "afr_ecm.ocr.dpi"

    @api.model
    def _ocr_global_enabled(self):
        v = self.env["ir.config_parameter"].sudo().get_param(
            self._OCR_ENABLED_KEY, "True"
        )
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @api.model
    def _ocr_get_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "languages": ICP.get_param(self._OCR_LANG_KEY, "por+eng"),
            "max_pages": int(ICP.get_param(self._OCR_MAX_PAGES_KEY, "50")),
            "min_chars_skip": int(ICP.get_param(self._OCR_MIN_CHARS_KEY, "100")),
            "dpi": int(ICP.get_param(self._OCR_DPI_KEY, "200")),
        }

    def _ocr_get_mimetype(self):
        self.ensure_one()
        # tenta atributo direto (algumas versões dms.file têm)
        mt = getattr(self, "mimetype", None)
        if mt:
            return mt
        if self.attachment_id and getattr(self.attachment_id, "mimetype", None):
            return self.attachment_id.mimetype
        # fallback por extensão
        name = (self.name or "").lower()
        ext_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".bmp": "image/bmp",
            ".gif": "image/gif",
        }
        for ext, mt in ext_map.items():
            if name.endswith(ext):
                return mt
        return ""

    def _ocr_get_content_bytes(self):
        self.ensure_one()
        c = self.content
        if not c:
            return b""
        try:
            return base64.b64decode(c)
        except Exception:
            return b""

    @staticmethod
    def _ocr_compute_hash(content_bytes):
        if not content_bytes:
            return ""
        return hashlib.sha256(content_bytes).hexdigest()

    def _ocr_is_eligible(self):
        """True se o file deve ser processado por OCR."""
        self.ensure_one()
        if not self._ocr_global_enabled():
            return False
        if not self.document_type_id or not self.document_type_id.ocr_enabled:
            return False
        mt = self._ocr_get_mimetype()
        if not ocr_engine.is_supported_mimetype(mt):
            return False
        return True

    def _ocr_dispatch(self, force=False):
        """Marca state=pending e enfileira job (queue_job).
        Se `force`, redispara mesmo que hash não tenha mudado.
        """
        for rec in self:
            if not rec._ocr_is_eligible():
                continue
            content = rec._ocr_get_content_bytes()
            if not content:
                continue
            h = self._ocr_compute_hash(content)
            if not force and h and h == rec.ocr_content_hash and rec.ocr_state == "done":
                # cache hit: já processamos esse conteúdo
                continue
            rec.sudo().write({
                "ocr_state": "pending",
                "ocr_error": False,
            })
            # enfileira job; with_delay vem do queue_job
            try:
                rec.with_delay(
                    description="OCR dms.file id=%s" % rec.id,
                    channel="root.afr_ecm.ocr",
                )._ocr_process()
            except AttributeError:
                # fallback (sem queue_job): processa sync
                _logger.warning(
                    "afr_ecm: queue_job indisponível, processando OCR sync para id=%s",
                    rec.id,
                )
                rec._ocr_process()

    def _ocr_process(self):
        """Job OCR. Decorado @job pelo queue_job (via with_delay no dispatch).
        Roda single-record. Atualiza fields + popula
        ir.attachment.index_content para search.
        """
        self.ensure_one()
        rec = self.sudo()
        rec.write({"ocr_state": "processing"})
        cfg = rec._ocr_get_config()
        content = rec._ocr_get_content_bytes()
        mt = rec._ocr_get_mimetype()
        try:
            result = ocr_engine.extract(
                content,
                mt,
                languages=cfg["languages"],
                dpi=cfg["dpi"],
                max_pages=cfg["max_pages"],
                min_chars_skip_ocr=cfg["min_chars_skip"],
            )
        except Exception as e:
            _logger.exception("OCR job falhou para id=%s: %s", rec.id, e)
            rec.write({
                "ocr_state": "failed",
                "ocr_error": str(e),
                "ocr_processed_at": fields.Datetime.now(),
            })
            return False

        if result.skipped:
            rec.write({
                "ocr_state": "skipped",
                "ocr_engine": result.engine,
                "ocr_error": result.skipped_reason or False,
                "ocr_processed_at": fields.Datetime.now(),
            })
            return False
        if result.error:
            rec.write({
                "ocr_state": "failed",
                "ocr_error": result.error,
                "ocr_engine": result.engine,
                "ocr_processed_at": fields.Datetime.now(),
            })
            return False

        rec.write({
            "ocr_state": "done",
            "ocr_text": result.text,
            "ocr_engine": result.engine,
            "ocr_pages": result.pages,
            "ocr_confidence": result.confidence,
            "ocr_content_hash": rec._ocr_compute_hash(content),
            "ocr_processed_at": fields.Datetime.now(),
            "ocr_error": False,
        })
        # popula índice para full-text search (cobre /web/search padrão Odoo)
        if rec.attachment_id and result.text:
            try:
                rec.attachment_id.sudo().write({"index_content": result.text})
            except Exception:
                _logger.exception("Falha ao gravar index_content em attachment_id=%s",
                                  rec.attachment_id.id)
        return True

    def action_reprocess_ocr(self):
        """Botão: força reprocesso (ignora cache de hash)."""
        for rec in self:
            if not rec._ocr_is_eligible():
                raise UserError(_(
                    "Tipo do documento não tem OCR habilitado, "
                    "ou mimetype não suportado, ou OCR global desativado."
                ))
            rec._ocr_dispatch(force=True)
        return True

    @api.model
    def _cron_ocr_backlog(self):
        """Cron de fallback: pega state in (pending, failed) e re-dispatch.
        Útil quando worker estava down ou job foi perdido.
        """
        domain = [
            ("ocr_state", "in", ["pending", "failed"]),
        ]
        candidates = self.sudo().search(domain, limit=200)
        sent = 0
        for rec in candidates:
            try:
                rec._ocr_dispatch(force=False)
                sent += 1
            except Exception as e:
                _logger.exception("OCR backlog dispatch falha id=%s: %s", rec.id, e)
        return sent
