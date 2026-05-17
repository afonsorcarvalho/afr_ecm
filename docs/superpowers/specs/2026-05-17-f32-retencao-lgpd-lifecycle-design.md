# F3.2 — Retenção LGPD / Ciclo de Vida (afr_ecm)

**Data:** 2026-05-17
**Versão alvo:** `afr_ecm 16.0.3.0.0`
**Autor:** Engenapp (spec gerada via brainstorming colaborativo)
**Status:** Aprovado para implementação

## Contexto

Documentos no ECM têm `expiration_date` (com `retention_days` por
`afr.ecm.document.type` apenas como sugestão default). Cron actual
(`_cron_check_expirations`) envia alertas em janelas 30/7/0 dias mas
**não toma ação alguma** quando o vencimento passa. Compliance LGPD/CFM/
Anvisa requer trilha de auditoria de retenção e destruição, com
justificativa documentada.

F3.2 introduz workflow manual controlado pelo gestor para arquivar,
renovar, destruir conteúdo ou purgar registros de documentos vencidos,
com legal hold para bloquear destruição.

## Decisões de design (consolidadas do brainstorming)

| Decisão | Escolha |
|---|---|
| Ação automática pós-vencimento | Nenhuma — manager decide caso a caso |
| Ações disponíveis | Archive, Renew, Hard Delete, Purge (4) |
| Justificativa obrigatória | Todas as 4 ações |
| Legal hold | Flag simples (`legal_hold` boolean + `legal_hold_reason`) |
| Permissões destrutivas | Hard Delete / Purge restritos a `group_ecm_admin` |
| UI principal | View dedicada `ECM > Ciclo de Vida > Documentos Vencidos` |
| Cron automático | Apenas marca `lifecycle_state='expired'` — sem destruição |

## Modelo de estados (`dms.file.lifecycle_state`)

```
                    +------- Manager Renew --------+
                    v                              |
[active] --cron--> [expired] --Manager Archive--> [archived]
                    |                              |
                    |                              v
                    +--Admin Hard Delete--> [content_purged]
                    |                              |
                    +--Admin Purge (unlink)--> (row removida)
                                                   ^
                                                   |
                                            [archived] também
                                            pode ser Hard Delete/Purge
```

**States:**
- `active` (default) — documento em uso normal
- `expired` — `expiration_date < today`, marcado por cron; ainda totalmente acessível
- `archived` — soft delete; `active=False`, conteúdo preservado, reversível via Renew
- `content_purged` — Hard Delete executado; `content=False`, row + metadata + audit log preservados; LGPD-compliant
- **(row removida)** — Purge unlink; só audit log com snapshot subsiste

## Novos campos `dms.file`

```python
lifecycle_state = fields.Selection([
    ('active', 'Ativo'),
    ('expired', 'Vencido'),
    ('archived', 'Arquivado'),
    ('content_purged', 'Conteúdo destruído'),
], default='active', required=True, index=True, tracking=True)

legal_hold = fields.Boolean(default=False, tracking=True)
legal_hold_reason = fields.Text(tracking=True)

last_lifecycle_action_date = fields.Datetime(readonly=True)
last_lifecycle_action_user_id = fields.Many2one('res.users', readonly=True)
last_lifecycle_action_type = fields.Char(readonly=True)  # 'archive'|'renew'|'hard_delete'
```

**Constrains:** `_check_legal_hold_reason` — se `legal_hold=True`,
`legal_hold_reason` obrigatório.

## Novo campo `afr.ecm.audit.log`

```python
metadata_snapshot = fields.Text(
    help="JSON snapshot mínimo de dms.file antes de Hard Delete/Purge "
         "(name, document_type, confidentiality, size, expiration_date, "
         "create_uid, complete_directory_name). Garante reconstituição LGPD."
)
```

Único campo novo; outras ações lifecycle (archive/renew) não precisam
porque a row dms.file ainda existe e tracking via mail.thread cobre.

## Cron `_cron_mark_expired`

```python
@api.model
def _cron_mark_expired(self, today=None):
    today = today or fields.Date.today()
    files = self.sudo().search([
        ('expiration_date', '!=', False),
        ('expiration_date', '<', today),
        ('lifecycle_state', '=', 'active'),
    ])
    files.write({'lifecycle_state': 'expired'})
    return len(files)
```

- Frequência: diário
- Idempotente: só atualiza files em `active`; `archived`/`content_purged`/já-`expired` ficam intactos
- Separado de `_cron_check_expirations` (alertas continuam funcionando)
- Configuração: `data/cron_lifecycle_mark_expired.xml`

## Wizard `afr.ecm.lifecycle.action.wizard`

```python
class AfrEcmLifecycleActionWizard(models.TransientModel):
    _name = 'afr.ecm.lifecycle.action.wizard'
    _description = 'Wizard de Ação de Ciclo de Vida'

    file_ids = fields.Many2many('dms.file', required=True)
    action = fields.Selection([
        ('archive', 'Arquivar'),
        ('renew', 'Renovar'),
        ('hard_delete', 'Destruir Conteúdo'),
        ('purge', 'Purgar Registro'),
    ], required=True)
    justification = fields.Text(required=True)
    new_expiration_date = fields.Date()

    @api.constrains('action', 'new_expiration_date')
    def _check_renew_date(self):
        for w in self:
            if w.action == 'renew' and not w.new_expiration_date:
                raise ValidationError('Renew exige nova data de expiração.')

    def action_apply(self):
        self.ensure_one()
        if self.action in ('hard_delete', 'purge'):
            if not self.env.user.has_group('afr_ecm.group_ecm_admin'):
                raise AccessError('Apenas ECM Admin pode executar Hard Delete/Purge.')
            blocked = self.file_ids.filtered('legal_hold')
            if blocked:
                raise UserError(
                    f'{len(blocked)} arquivo(s) com legal hold ativo: '
                    f'{", ".join(blocked.mapped("name"))}'
                )
        getattr(self, f'_do_{self.action}')()
```

**Handlers (cada um grava audit log + atualiza last_lifecycle_action_*):**

| Handler | Operação |
|---|---|
| `_do_archive` | `write({'lifecycle_state':'archived', 'active':False})` |
| `_do_renew` | `write({'lifecycle_state':'active', 'active':True, 'expiration_date': self.new_expiration_date})` |
| `_do_hard_delete` | Snapshot metadata em audit log → `write({'content':False, 'lifecycle_state':'content_purged', 'active':False})` |
| `_do_purge` | Snapshot completo em audit log → `self.file_ids.unlink()` |

## UI

### Menu
`ECM > Ciclo de Vida > Documentos Vencidos` (ir.actions.act_window).

### List view `view_dms_file_lifecycle_list`
- Colunas: name, document_type_id, expiration_date, **dias_vencido** (computed unstored), lifecycle_state (badge), legal_hold (boolean), last_lifecycle_action_type, last_lifecycle_action_user_id, last_lifecycle_action_date
- Default domain: `[('lifecycle_state', 'in', ['expired', 'archived'])]`
- Filtros: vencido > 30/90/365 dias; por document_type; por lifecycle_state; com/sem legal hold
- Group by: lifecycle_state, document_type_id
- Multi-select habilitado
- Action menu: "Aplicar Ação de Ciclo de Vida" → abre wizard com `file_ids = active_ids`

### Form view `dms.file` (inherit)
- Header: badge `lifecycle_state` com decorations por estado
- Aba nova "Ciclo de Vida":
  - `legal_hold` + `legal_hold_reason` (editáveis só para `group_ecm_admin`)
  - `last_lifecycle_action_*` (readonly histórico)
  - Lista de audit log entries com `action_type` lifecycle_*
- Header buttons (visíveis conforme state + grupo):
  - `Archive` — quando state ∈ {expired}
  - `Renew` — quando state ∈ {expired, archived}
  - `Hard Delete` — quando state ∈ {expired, archived}, group_ecm_admin
  - `Purge` — quando state ∈ {expired, archived, content_purged}, group_ecm_admin

## Segurança

- Wizard `afr.ecm.lifecycle.action.wizard` acessível a `group_ecm_manager+` via `ir.model.access.csv`.
- Runtime check em `action_apply` para `hard_delete`/`purge` → `has_group('afr_ecm.group_ecm_admin')`.
- Campos `legal_hold` / `legal_hold_reason` editáveis só para `group_ecm_admin` (form view via `groups`).
- Audit log: read all para manager+, write só via server-side (`@api.model_create_multi` + check no-op).
- Botão Purge no form: `groups="afr_ecm.group_ecm_admin"`.

## Audit log entries

Novos `action_type` valores em `afr.ecm.audit.log`:
- `lifecycle_archive` — payload notes = justification
- `lifecycle_renew` — payload notes = "Renovado para {new_date}\n\n{justification}"
- `lifecycle_hard_delete` — payload notes = justification; `metadata_snapshot` = JSON
- `lifecycle_purge` — payload notes = justification; `metadata_snapshot` = JSON completo

`metadata_snapshot` JSON estrutura mínima:
```json
{
  "id": 123,
  "name": "Contrato_X.pdf",
  "document_type": "Contrato",
  "confidentiality": "restricted",
  "size": 245678,
  "expiration_date": "2026-05-15",
  "create_uid": [42, "admin"],
  "complete_directory_name": "10_Comercial / Contratos / 2024"
}
```

## Migração

**Sem script de migration.** Módulo `afr_ecm` ainda não está em produção
nesta versão; deploys piloto serão tratados como fresh install. ORM do
Odoo cria colunas automaticamente no `-u afr_ecm` ou `-i afr_ecm`:
- Novas colunas em `dms.file` (lifecycle_state, legal_hold, …) recebem
  defaults declarados nos `fields.Selection/Boolean/etc.`
- Nova coluna `metadata_snapshot` em `afr.ecm.audit.log` fica NULL para
  rows pré-existentes — sem impacto (só usada por novos entries).

Backfill de `expired` para files vencidos pré-existentes: executado em
runtime na primeira corrida do cron `_cron_mark_expired` (idempotente).
Sem necessidade de SQL one-shot.

Quando módulo for a produção (deploy piloto real com dados não
descartáveis), adicionar script `migrations/<version>/pre-migration.py`
no momento. Pendência registrada no `TODO.md`.

## Tests `tests/test_lifecycle.py`

Tag `afr_ecm_lifecycle`.

1. `test_cron_marks_expired` — file vencido → state='expired' após cron
2. `test_cron_idempotent` — re-run não altera archived/purged nem duplica
3. `test_cron_only_marks_active` — não muda state de files já archived/content_purged
4. `test_archive_action` — state='archived', active=False, audit log entry
5. `test_renew_extends_and_reactivates` — expiration_date atualizada, state='active', active=True
6. `test_hard_delete_requires_admin` — manager → AccessError
7. `test_hard_delete_admin_ok` — content=False, state='content_purged', audit snapshot presente
8. `test_purge_requires_admin` — manager → AccessError
9. `test_purge_admin_ok` — dms.file unlinked, audit log preservado com snapshot
10. `test_legal_hold_blocks_hard_delete` — file legal_hold=True + hard_delete → UserError
11. `test_legal_hold_blocks_purge` — idem
12. `test_legal_hold_allows_archive` — archive funciona com hold
13. `test_legal_hold_allows_renew` — renew funciona com hold
14. `test_legal_hold_requires_reason` — constrains levanta sem reason
15. `test_justification_required` — wizard sem justification → ValidationError
16. `test_renew_requires_new_date` — renew sem new_expiration_date → ValidationError
17. `test_audit_log_metadata_snapshot_json_valid` — hard_delete grava JSON parseável
18. `test_multi_file_wizard_atomicity` — wizard multi-select: 1 file legal_hold → UserError, nenhum mutado

## Arquivos novos / modificados

**Novos:**
- `models/lifecycle.py` — extensão de `dms.file` (lifecycle_state, legal_hold, cron, action methods)
- `wizards/__init__.py` (se ainda não existir) + `wizards/lifecycle_action_wizard.py`
- `wizards/lifecycle_action_wizard_views.xml`
- `views/dms_file_lifecycle_views.xml` — list view dedicada + form inherit
- `views/menus_lifecycle.xml` — menu `ECM > Ciclo de Vida`
- `data/cron_lifecycle_mark_expired.xml`
- `security/ir.model.access.csv` — adiciona linha wizard
- `tests/test_lifecycle.py`

**Modificados:**
- `__manifest__.py` — version → 16.0.3.0.0; data list adiciona novos XML
- `models/__init__.py` — import lifecycle
- `models/dms_file.py` — adiciona campos lifecycle (ou via inherit em lifecycle.py)
- `models/audit_log.py` — adiciona `metadata_snapshot` field + selection extension de action_type

## Pontos abertos / fora de escopo

- **Restore de archived sem renovar prazo:** atualmente cobre-se via Renew (define nova data). Se utilizador quiser só "tirar do archive" sem mexer em prazo, requer botão dedicado — não incluído por YAGNI.
- **Dual approval para purge:** rejeitado em brainstorming. Pode entrar em F3.2.1.
- **Cron auto-archive pós-grace-period:** rejeitado — manager mantém controle total.
- **Modelo dedicado de legal hold** (múltiplos holds por file): rejeitado — flag simples suficiente.
- **Exportação pré-deleção** (LGPD direito de portabilidade): fora de escopo F3.2; pode entrar em F3.4.

## Critérios de aceitação

- Suite `afr_ecm_lifecycle` 18/18 GREEN
- Suite `afr_ecm` full mantém 81 passes + 0 failures + 0 errors
- Fresh install em DB nova: `-i afr_ecm` cria colunas + cron + menus sem erros
- Upgrade em `odoo_ecm_test` (DB existente, não-produção): `-u afr_ecm` sem manual ALTER; ORM aplica defaults
- Smoke test manual: admin cria file vencido → cron marca `expired` → wizard Archive → wizard Renew → wizard Hard Delete (com justificativa) → audit log inspeccionável
- Documentação: RUNBOOK_DEPLOY.md actualizado com nova menu e fluxos
