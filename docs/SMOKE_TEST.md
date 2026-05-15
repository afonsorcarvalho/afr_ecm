# Smoke Test — afr_ecm

**Versão:** `16.0.2.0.0`
**Tempo estimado:** 45–60 min
**Pré-req:** Deploy concluído (etapas 1-4 do [RUNBOOK_DEPLOY.md](RUNBOOK_DEPLOY.md)). Ambiente isolado (DB sandbox ou prod com 0 usuários reais ainda).

Marcar `[x]` ao passar. Bug → registrar em coluna **Obs** e abrir issue.

---

## 0. Setup do teste

- [ ] Criar 3 usuários teste:
  - `ecm_user01` — `base.group_user` + `group_ecm_user` + `group_ecm_area_rh`
  - `ecm_mgr01` — `base.group_user` + `group_ecm_manager` + `group_ecm_area_rh` + `group_ecm_area_comercial`
  - `ecm_aud01` — `base.group_user` + `group_ecm_area_auditor` (Internal User obrigatório p/ login)
- [ ] Logout admin / login `ecm_mgr01` para os testes principais

> ℹ **Setup runtime obrigatório antes:** dms.access.group `Auditor_Externo` deve ter `directory_ids` apontando para o root DMS de produção (Seção 3.3 do [RUNBOOK_DEPLOY.md](RUNBOOK_DEPLOY.md)). Sem isso, Seção 9 (auditor) inteira falha por falta de acesso de leitura na base.

---

## 1. Localização física + QR

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 1.1 | Settings → ECM → Localizações → Criar "Sala 101 / Estante A / Prateleira 1" | Salva, gera `barcode` via sequence `afr.ecm.physical.location` | [ ] | |
| 1.2 | Imprimir etiqueta (Print → Etiqueta QR) | PDF com QR code legível por celular | [ ] | |
| 1.3 | Escanear QR | Resolve URL ou identifier da localização | [ ] | |

---

## 2. Estrutura DMS + grupos de acesso

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 2.1 | DMS → Storages → criar storage `Test Storage` (save_type=database) | Cria | [ ] | |
| 2.2 | DMS → Directories → criar `Root RH` com `is_root_directory=True` + `storage_id` (deixar `group_ids` vazio) | Cria; override afr_ecm aplica `dms_access_group_ecm_default` (id seed) automaticamente | [ ] | |
| 2.3 | Adicionar manualmente o grupo `ECM_RH` em `Root RH > Groups` | Salva | [ ] | |
| 2.4 | Criar subpasta `Root RH / Admissão` | Cria; herda via `complete_group_ids` (group_ids próprio vazio) | [ ] | |
| 2.5 | Logout / login `ecm_user01` (tem `area_rh`) → vê `Root RH` + `Admissão` | Vê ambos | [ ] | |
| 2.6 | Login `ecm_aud01` (sem `area_rh`, sem `audit_scope` ativo) → NÃO vê `Root RH` | Não vê (record rule de área filtra) | [ ] | |

---

## 3. Upload + classificação + audit log

Login `ecm_mgr01`.

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 3.1 | DMS → File → Upload PDF de contrato em `Root RH / Admissão` | Cria dms.file (mimetype detectado, checksum sha1) | [ ] | |
| 3.2 | Classificar com tipo "Contrato" | `expiration_date` auto-preenchido pela UI (onchange → today + retention_days = 3650d). **Via backend write, onchange NÃO dispara — usar UI.** | [ ] | |
| 3.3 | Definir confidencialidade `restricted` | Salva | [ ] | |
| 3.4 | Definir `physical_location_id` = Sala 101 / Estante A | Salva | [ ] | |
| 3.5 | Settings → ECM → Audit Log → filtrar pelo file | Vê entries `create` + `write` (audit_mixin) | [ ] | |
| 3.6 | Login `ecm_user01` → tentar ver/baixar file restricted criado por outro user | Bloqueado: regra unificada `rule_dms_file_ecm_user` esconde restricted/confidential de outros donos (corrigido em 16.0.2.0.1) | [ ] | |

---

## 4. Download audit + restrição por tipo

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 4.1 | Settings → ECM → Tipos → "Contrato" → preencher `download_group_ids = [group_ecm_manager]` (m2m; vazio=todos, populado=só esses grupos baixam) | Salva | [ ] | |
| 4.2 | Login `ecm_user01` → file `internal` tipo Contrato → `can_download=False` no form | Botão download desabilitado | [ ] | |
| 4.3 | Login `ecm_mgr01` → mesmo file → `can_download=True` | Download OK | [ ] | |
| 4.4 | Audit Log → filtrar `event_type='download'` | Entry registrada com user + timestamp (controller `/web/content` chama `audit_log.log("download", target)`) | [ ] | |

---

## 5. Workflow aprovação multi-nível

Setup: tipo "Contrato" → `requires_approval=True`, criar 2 níveis:
- Nível 1 sequence=10, grupo=`group_ecm_manager`, consensus=`any`
- Nível 2 sequence=20, user=admin, consensus=`any`

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 5.1 | Login `ecm_user01` → upload contrato draft | `approval_state=draft` | [ ] | |
| 5.2 | Botão "Submeter aprovação" | `approval_state=pending`, `current_level=Nível 1`, activity criada pros managers | [ ] | |
| 5.3 | Login `ecm_mgr01` → ver activity em "My Activities" | Aparece | [ ] | |
| 5.4 | `ecm_mgr01` aprova | Sobe pro Nível 2, activity nova pro admin, action gravada | [ ] | |
| 5.5 | Admin aprova | `approval_state=approved`, activities resolvidas | [ ] | |
| 5.6 | Tentar editar `name` do file approved como `ecm_mgr01` | Bloqueado (UserError) | [ ] | |
| 5.7 | Tentar editar como admin | Bloqueado também (estrito) | [ ] | |
| 5.8 | `ecm_user01` (autor) clica "Reabrir" | volta `draft`, action `REOPEN` gravada | [ ] | |
| 5.9 | Editar, ressubmeter, rejeitar no Nível 1 | `approval_state=rejected`, action `REJECT` com motivo | [ ] | |

---

## 6. Vencimento + alertas

Setup: criar file com `expiration_date = today + 7d`.

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 6.1 | Settings → Technical → Scheduled Actions → `_cron_check_expirations` → Run Manually | Sem erro | [ ] | |
| 6.2 | File chatter → message_post com aviso vencimento | Mensagem criada, partner_ids = followers | [ ] | |
| 6.3 | Activity criada pros usuários `group_ecm_manager` | Sim | [ ] | |
| 6.4 | Rodar cron 2x no mesmo dia → não duplica | `last_expiration_alert` previne dup | [ ] | |
| 6.5 | Marcar file como `approval_state=rejected` → rodar cron | Não envia alerta (`last_expiration_alert` continua false) | [ ] | |
| 6.6 | Status computado (`expiration_status`): `>30d`=`ok`, `8-30d`=`warning`, `0-7d`=`critical`, `<0d`=`expired` | Thresholds reais conforme [models/dms_file.py:494-510](../models/dms_file.py#L494-L510) | [ ] | |

---

## 7. OCR pipeline

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 7.1 | Tipo "Contrato" → `ocr_enabled=True` | Salva | [ ] | |
| 7.2 | Upload PDF nativo (texto selecionável) | Após cron jobrunner, `ocr_state=done`, `ocr_engine=pdftotext`, texto em `ocr_text` | [ ] | |
| 7.3 | Upload PDF escaneado (imagem) | `ocr_state=done`, `ocr_engine=tesseract`, `ocr_pages>0`, `ocr_text` populado | [ ] | |
| 7.4 | Upload PNG/JPG | OCR roda, `ocr_engine=tesseract` | [ ] | |
| 7.5 | Buscar termo do conteúdo na barra de busca do dms.file | Acha pelo `ocr_text` (filter_domain) | [ ] | |
| 7.6 | Re-upload mesmo arquivo (mesmo sha256) | Cache hit, não reprocessa | [ ] | |
| 7.7 | Manager → botão "Reprocessar OCR" | Recoloca em pending, processa de novo | [ ] | |
| 7.8 | Tipo `ocr_enabled=False` + opt-in per-file `ocr_enabled=True` no dms.file | Processa só esse file | [ ] | |
| 7.9 | Queue Jobs → ver job criado e processado | OK | [ ] | |
| 7.10 | `ir.attachment.indexed_content` populado | Só aplicável se storage `save_type=attachment`. Para `save_type=database` (default), busca usa `ocr_text` direto (Seção 7.5) | [ ] | |

---

## 8. Share público

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 8.1 | dms.file → Gerar Share Link | URL `/ecm/share/<id>/<token>` gerada | [ ] | |
| 8.2 | Abrir URL em sessão anônima (navegador privado). **Em ambiente multi-DB acrescentar `?db=<dbname>` ou header `X-Odoo-Db`.** | Download funciona sem login; HTTP 200 + Content-Disposition attachment | [ ] | |
| 8.3 | Manager revoga share | URL retorna 403/404 | [ ] | |
| 8.4 | Audit Log → entry SHARE_DOWNLOAD | Registrada | [ ] | |

---

## 9. Auditor externo + audit scope

Login admin.

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 9.1 | Settings → ECM → Audit Scope → criar escopo "Auditoria 2026" com `directory_ids=[Root RH]`, `expire_date=today+30d`, `auditor_user_ids=[ecm_aud01]` | Salva | [ ] | |
| 9.2 | Logout / login `ecm_aud01` → vê `Root RH` e filhos somente | Sim (tree rule) | [ ] | |
| 9.3 | `ecm_aud01` tenta editar/deletar file | Bloqueado (read-only) | [ ] | |
| 9.4 | `ecm_aud01` tenta ver `Root Comercial` (não no escopo) | Não vê | [ ] | |
| 9.5 | Mudar `expire_date` para `today-1` + rodar `_cron_audit_scope_expire` | Escopo arquivado, `ecm_aud01` perde acesso | [ ] | |

---

## 10. Revogação TI (saída de funcionário)

Login admin.

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 10.1 | HR → Funcionário "Teste TI" → marcar `active=False` | dms.file vinculado (se houver) recebe activity TI | [ ] | |
| 10.2 | Sem `ecm_area_ti` configurado → no-op gracioso | Não crasha, log warning | [ ] | |

---

## 11. Bloqueio exclusão pasta não-vazia

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 11.1 | Tentar deletar `Root RH / Admissão` com files dentro | Bloqueado UserError | [ ] | |
| 11.2 | Esvaziar pasta → deletar | OK | [ ] | |

---

## 12. Performance / sanidade

| # | Passo | Esperado | Pass | Obs |
|---|---|---|---|---|
| 12.1 | Upload 20 PDFs ~5MB cada via UI | Tempo total < 60s | [ ] | |
| 12.2 | OCR processar batch: 20 jobs queue_job | Processa em paralelo, sem deadlock | [ ] | |
| 12.3 | Busca por termo OCR em 100+ files | < 2s | [ ] | |
| 12.4 | Logs sem TRACEBACK / WARNING repetido | OK | [ ] | |

---

## 13. Pós-teste

- [ ] Restaurar DB sandbox a snapshot pré-teste (ou drop e recriar)
- [ ] Documentar bugs encontrados em issues do repo
- [ ] Anotar tempos reais por seção
- [ ] Decisão GO / NO-GO produção formal por escrito

---

## Resultado final

| Seção | Total | Pass | Fail |
|---|---:|---:|---:|
| 1. Localização QR | 3 | | |
| 2. Estrutura DMS | 6 | | |
| 3. Upload+Audit | 6 | | |
| 4. Download | 4 | | |
| 5. Aprovação | 9 | | |
| 6. Vencimento | 6 | | |
| 7. OCR | 10 | | |
| 8. Share | 4 | | |
| 9. Auditor | 5 | | |
| 10. TI | 2 | | |
| 11. Delete | 2 | | |
| 12. Perf | 4 | | |
| **TOTAL** | **61** | | |

**Critério GO:** 100% pass nas seções 1-6 e 11; >=80% nas 7-10 e 12.
