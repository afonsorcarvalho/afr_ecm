"""F4.3.3 — TI Access Revocation on Employee Termination.

When an employee is set inactive (active True → False), this module:
  1. Creates a draft dms.file of type TI_ACC_REV in the designated folder.
  2. Creates a mail.activity assigned to the TI group (or ECM manager fallback).
  3. Posts a chatter message on the employee record.
"""
import base64
import logging
from datetime import date

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_REVOCATION_CHECKLIST_TEMPLATE = """\
# Checklist de Revogação de Acesso TI
**Funcionário:** {name}
**Matrícula:** {matricula}
**Data:** {date}

## Acessos a Revogar

- [ ] Odoo (usuário interno) — desativar conta
- [ ] ECM (dms.file / afr_ecm) — remover do grupo ECM User/Manager
- [ ] Supervisório (afr_supervisorio) — revogar login
- [ ] E-mail corporativo — desativar caixa / redirecionamento
- [ ] VPN / WireGuard — remover peer / credencial
- [ ] AD / LDAP (se integrado) — desabilitar conta no diretório
- [ ] Outros sistemas específicos — verificar com gestor de TI

## Observações
<!-- Registrar aqui qualquer exceção ou nota durante o processo. -->

## Assinatura Responsável TI
Nome: ________________
Data conclusão: ________________
"""


class HrEmployeeRevocation(models.Model):
    _inherit = 'hr.employee'

    def write(self, vals):
        # Capture employees that are currently active BEFORE the write,
        # so we can detect the True → False transition.
        triggered = self.env['hr.employee']
        if 'active' in vals and not vals.get('active'):
            triggered = self.filtered(lambda e: e.active)
        res = super().write(vals)
        for emp in triggered:
            try:
                emp._afr_ecm_dispatch_ti_revocation()
            except Exception:
                _logger.exception(
                    "afr_ecm: failed to dispatch TI revocation for employee id=%s (%s)",
                    emp.id, emp.name,
                )
        return res

    def _afr_ecm_dispatch_ti_revocation(self):
        """Create a draft TI_ACC_REV dms.file and assign an activity to the TI group."""
        self.ensure_one()
        env = self.env

        # --- Locate document type ---
        doc_type = env['afr.ecm.document.type'].sudo().search(
            [('code', '=', 'TI_ACC_REV')], limit=1
        )
        if not doc_type:
            _logger.warning(
                "afr_ecm: document type TI_ACC_REV not found — skipping revocation "
                "file for employee id=%s (%s)", self.id, self.name
            )
            return

        # --- Resolve destination directory ---
        directory = self._afr_ecm_resolve_revocation_directory(doc_type)
        if not directory:
            _logger.warning(
                "afr_ecm: no target directory found for TI revocation (employee id=%s). "
                "File will not be created.", self.id
            )
            return

        # --- Build file name and content ---
        matricula = getattr(self, 'matricula', None) or str(self.id)
        today_str = date.today().strftime('%Y-%m-%d')
        file_name = "REV_{matricula}_{name}_{date}.md".format(
            matricula=matricula,
            name=self.name or 'SEM_NOME',
            date=today_str,
        ).replace('/', '_').replace(' ', '_')

        checklist_body = _REVOCATION_CHECKLIST_TEMPLATE.format(
            name=self.name or '',
            matricula=matricula,
            date=today_str,
        )
        content_b64 = base64.b64encode(checklist_body.encode('utf-8')).decode('ascii')

        # --- Create the dms.file (sudo: HR may not have ECM write rights) ---
        file_vals = {
            'name': file_name,
            'directory_id': directory.id,
            'content': content_b64,
            'document_type_id': doc_type.id,
        }
        if doc_type.requires_approval:
            file_vals['approval_state'] = 'draft'

        dms_file = env['dms.file'].sudo().create(file_vals)
        _logger.info(
            "afr_ecm: created TI revocation file id=%s for employee id=%s (%s)",
            dms_file.id, self.id, self.name,
        )

        # --- Resolve TI activity recipient ---
        recipient = self._afr_ecm_resolve_ti_recipient()

        # --- Create mail.activity on the dms.file ---
        if recipient:
            try:
                dms_file.sudo().activity_schedule(
                    'mail.mail_activity_data_warning',
                    user_id=recipient.id,
                    summary=_("Revogar acesso TI: %s") % (self.name or ''),
                    note=_(
                        "<p>Funcionário <b>%s</b> foi desativado em %s.</p>"
                        "<p>Verificar e revogar TODOS os acessos de TI até o "
                        "prazo LGPD/ISO-27001 (4 horas).</p>"
                    ) % (self.name or '', today_str),
                    date_deadline=fields.Date.today(),
                )
            except Exception:
                _logger.exception(
                    "afr_ecm: failed to create TI revocation activity for file id=%s",
                    dms_file.id,
                )

        # --- Post chatter on the employee (via sudo: different model perms) ---
        try:
            self.sudo().message_post(
                body=_(
                    "<p><b>Alerta de Conformidade TI (LGPD/ISO-27001)</b></p>"
                    "<p>Funcionário desativado. "
                    "Documento de revogação de acessos criado: "
                    "<a href='/web#id=%s&amp;model=dms.file&amp;view_type=form'>%s</a></p>"
                    "<p>Prazo: 4 horas para revogação completa.</p>"
                ) % (dms_file.id, file_name),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
        except Exception:
            _logger.exception(
                "afr_ecm: failed to post chatter on employee id=%s after revocation",
                self.id,
            )

    def _afr_ecm_resolve_revocation_directory(self, doc_type):
        """Return the dms.directory to use for the revocation file.

        Priority:
          1. doc_type.default_directory_id (configured on the type)
          2. dms.directory whose complete_name ends with
             '60_TI/Registros/05_Gestao_Acessos'  (anchor folder)
             or sub-directory named 'Revogacoes' inside it.
        """
        env = self.env

        # Priority 1 — doc type configured directory
        if doc_type.default_directory_id:
            return doc_type.default_directory_id

        # Priority 2 — search by naming convention
        # Try sub-folder 'Revogacoes' first, then the anchor directly
        anchor_suffix = '60_TI/Registros/05_Gestao_Acessos'

        anchors = env['dms.directory'].sudo().search(
            [('complete_name', 'like', anchor_suffix)]
        )
        for anchor in anchors:
            # Look for a child named 'Revogacoes'
            revogacoes = env['dms.directory'].sudo().search(
                [('parent_id', '=', anchor.id), ('name', 'ilike', 'Revogacoes')],
                limit=1,
            )
            if revogacoes:
                return revogacoes
            # Use the anchor itself
            return anchor

        _logger.warning(
            "afr_ecm: anchor folder '%s' not found in dms.directory", anchor_suffix
        )
        return env['dms.directory']

    def _afr_ecm_resolve_ti_recipient(self):
        """Return the first active user from group_ecm_area_ti, fallback group_ecm_manager."""
        env = self.env

        # Try F4.3.1 area group first
        ti_group = env.ref('afr_ecm.group_ecm_area_ti', raise_if_not_found=False)
        if ti_group:
            users = ti_group.users.filtered('active')
            if users:
                return users[0]

        # Fallback: ECM Manager group
        mgr_group = env.ref('afr_ecm.group_ecm_manager', raise_if_not_found=False)
        if mgr_group:
            users = mgr_group.users.filtered('active')
            if users:
                return users[0]

        _logger.warning(
            "afr_ecm: no TI group users found — revocation activity will not be assigned"
        )
        return env['res.users']
