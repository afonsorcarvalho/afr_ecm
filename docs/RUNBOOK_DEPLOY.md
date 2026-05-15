# Runbook de Deploy — afr_ecm

**Versão alvo:** `16.0.2.0.1` (post-SGQ-split + record-rules OR-collapse fix)
**Odoo:** 16.0
**Status:** Piloto / Pré-produção

---

## 1. Pré-requisitos de infra

### 1.1 Imagem Docker do worker Odoo
Garantir que `Dockerfile` instala:
- `tesseract-ocr`, `tesseract-ocr-por`, `tesseract-ocr-eng`
- `poppler-utils` (pdftotext + pdftoppm)
- `python3-pip` libs (pelo `requirements.txt`):
  - `pytesseract`
  - `pdf2image`
  - `Pillow`
  - `qrcode`

Verificar com:
```bash
docker compose run --rm web bash -c "tesseract --version && pdftotext -v && python3 -c 'import pytesseract, pdf2image, qrcode'"
```

### 1.2 addons_path
Conferir `conf/odoo.conf` em produção contém:
```
addons_path = ...,/mnt/oca-dms,/mnt/oca-queue
```
E `docker-compose.yml` monta:
```yaml
volumes:
  - ./OCA-dms:/mnt/oca-dms
  - ./OCA-queue:/mnt/oca-queue
```
`OCA-storage` opcional (só necessário quando F2 fs_storage entrar).

### 1.3 Sidecar OCR worker
Adicionar ao `docker-compose.prod.yml` serviço dedicado para processar fila OCR:
```yaml
ocr_worker:
  image: <mesma do web>
  command: ["odoo", "--workers=0", "--max-cron-threads=2", "--load=queue_job,web"]
  environment:
    - ODOO_QUEUE_JOB_CHANNELS=root:0
    - DB_HOST=db
    - DB_PORT=5432
    - DB_USER=odoo
    - DB_PASSWORD=...
  volumes:
    - ./conf:/etc/odoo
    - ./OCA-dms:/mnt/oca-dms
    - ./OCA-queue:/mnt/oca-queue
    - filestore:/var/lib/odoo
  depends_on:
    - db
```
Também no container `web` principal: `ODOO_QUEUE_JOB_CHANNELS=root:0` (desabilita jobrunner HTTP — usa cron threads do worker).

### 1.4 Filestore
`dms.file` usa `ir.attachment` padrão Odoo → filestore local em `/var/lib/odoo`. **Mapear em volume nomeado persistente.** Sem fs_storage remoto na 16.0.2.0.0.

---

## 2. Instalação

### 2.1 Backup pré-deploy obrigatório
```bash
docker compose exec db pg_dump -U odoo <db> | gzip > /backup/pre_afr_ecm_$(date +%Y%m%d_%H%M).sql.gz
tar czf /backup/filestore_pre_$(date +%Y%m%d_%H%M).tar.gz /var/lib/odoo/filestore/<db>
```

### 2.2 Instalar dependências OCA (se ainda não instaladas)
```bash
docker compose stop web
docker compose run --rm --no-deps web \
  -d <db> -i dms,dms_user_role,hr_dms_field,queue_job,queue_job_cron_jobrunner \
  --stop-after-init --workers=0 --max-cron-threads=0
```

### 2.3 Instalar afr_ecm
```bash
docker compose run --rm --no-deps web \
  -d <db> -i afr_ecm \
  --stop-after-init --workers=0 --max-cron-threads=0
```
Logs esperados: sem `TypeError`, sem `ForeignKeyViolation`. Se aparecer, abortar e investigar.

### 2.4 Subir serviços
```bash
docker compose up -d web ocr_worker
docker compose logs -f web ocr_worker | head -100
```

---

## 3. Setup runtime manual obrigatório

> ⚠ `data/dms_access_group_links.xml` NÃO é carregado pelo manifest (IDs hardcoded quebram fresh install). Setup manual abaixo é referência viva — sem isso record rules de áreas e auditor não filtram nada.

### 3.1 Criar dms.access.group de áreas via UI
Settings → DMS → Access Groups → criar:

| Nome | Perm Read | Perm Create | Perm Write | Perm Unlink |
|---|---|---|---|---|
| `ECM_Comercial` | ✓ | ✓ | ✓ | ✗ |
| `ECM_RH` | ✓ | ✓ | ✓ | ✗ |
| `ECM_RH_Funcionario` | ✓ | ✗ | ✗ | ✗ |
| `ECM_Financeiro` | ✓ | ✓ | ✓ | ✗ |
| `ECM_TI` | ✓ | ✓ | ✓ | ✗ |
| `ECM_Eng` | ✓ | ✓ | ✓ | ✗ |
| `ECM_SST` | ✓ | ✓ | ✓ | ✗ |
| `ECM_Diretoria` | ✓ | ✓ | ✓ | ✓ |
| `Auditor_Externo` | ✓ | ✗ | ✗ | ✗ |

Anotar os IDs gerados.

### 3.2 Vincular cada dms.access.group ao res.groups correspondente
Via Odoo shell ou UI (campo `group_ids` em dms.access.group):
```python
self.env.ref('afr_ecm.group_ecm_area_comercial')  # → vincular em ECM_Comercial
# ... mesmo para cada par
```
Lista canônica em [data/dms_access_group_links.xml](../data/dms_access_group_links.xml) (arquivo só referência).

### 3.3 Anchor Auditor_Externo na raiz
`Auditor_Externo` precisa ter `directory_ids` apontando para a raiz da storage que o auditor pode ver (read-only). Definir via UI após criar primeiro dms.directory raiz.

### 3.4 ECM Padrão já é seed
`dms_access_group_ecm_default` (criado pelo módulo) vincula `group_ecm_user/manager/admin` aos diretórios root automaticamente — não tocar.

---

## 4. Configurações pós-install

### 4.1 ir.config_parameter (Settings → Technical → Parameters → System Parameters)

| Key | Default | Descrição |
|---|---|---|
| `afr_ecm.expiration_alert_days` | `30,7,0` | Janelas de alerta vencimento (CSV) |
| `afr_ecm.ocr.enabled` | `True` | Liga pipeline OCR global |
| `afr_ecm.ocr.languages` | `por+eng` | Idiomas tesseract |
| `afr_ecm.ocr.max_pages` | `50` | Limite páginas/PDF |
| `afr_ecm.ocr.min_chars_skip` | `100` | Mín caracteres p/ pular OCR (texto nativo já suficiente) |
| `afr_ecm.ocr.dpi` | `200` | DPI pdf2image antes do tesseract |

Ajustar conforme volume real.

### 4.2 Usuários
Criar usuários e atribuir um (e só um) dos grupos:
- `group_ecm_user` — operacional, vê e cria conforme record rules
- `group_ecm_manager` — gerencia tipos, expirações, recebe activities
- `group_ecm_admin` — bypass restrição de edição em approved

Atribuir grupos de área (`group_ecm_area_*`) apenas a usuários que devem ver aquelas pastas.

### 4.3 Tipos de documento (seed entregue)
Já vem com: Contrato, Fatura, RH-Admissão, ASO, Ata, Certificado. Editar `retention_days`, `requires_approval`, `ocr_enabled`, `download_restricted` conforme política da empresa em Settings → ECM → Tipos de Documento.

### 4.4 Workflow aprovação (opcional por tipo)
Por tipo de documento com `requires_approval=True`, definir `approval_level_ids`:
- Sequence
- Grupo aprovador OU usuário específico
- Consensus `any` (qualquer um) ou `all` (todos)

### 4.5 Localizações físicas (acervo QR)
Settings → ECM → Localizações Físicas → criar. Imprimir etiquetas via report QR.

---

## 5. Smoke test obrigatório

Rodar checklist completo de [SMOKE_TEST.md](SMOKE_TEST.md) antes de liberar para usuários finais.

---

## 6. Monitoramento

### 6.1 Crons a verificar (Settings → Technical → Scheduled Actions)
| Cron | Frequência | Função |
|---|---|---|
| `afr_ecm._cron_check_expirations` | 1 dia | Alertas de vencimento |
| `afr_ecm._cron_ocr_backlog` | 15 min | Reenfileira OCR pending/failed |
| `queue_job_cron_jobrunner.queue_job_cron` | 1 min | Processa fila OCR |
| `afr_ecm._cron_ti_revocation` | 1 hora | Activities de revogação TI |
| `afr_ecm._cron_audit_scope_expire` | 1 dia | Arquiva escopos auditor expirados |

### 6.2 Logs a monitorar
```bash
docker compose logs --tail=200 -f web ocr_worker | grep -iE "afr_ecm|ERROR|TRACEBACK"
```

### 6.3 Fila queue_job
Settings → Technical → Queue Jobs. Filas a observar:
- jobs em `failed` → investigar `exc_info`
- jobs em `pending` antigos → cron jobrunner não está rodando

---

## 7. Backup / DR

### 7.1 Diário
```bash
# DB
pg_dump -U odoo <db> | gzip > backup/db_$(date +%Y%m%d).sql.gz
# Filestore
rsync -a /var/lib/odoo/filestore/<db>/ backup/filestore_$(date +%Y%m%d)/
```

### 7.2 Retenção sugerida (definir com cliente)
- Diários: 14 dias
- Semanais: 8 semanas
- Mensais: 12 meses

### 7.3 Restore drill
Testar restore em DB sandbox a cada trimestre — `dms.file.content` está em filestore, NÃO no DB.

---

## 8. Rollback

### 8.1 Versão anterior do submodule
```bash
cd /home/afonso/docker/odoo_engenapp
git submodule update --init addons/afr_ecm
cd addons/afr_ecm
git checkout <commit-anterior>   # ex: 97bf320 (pre-split, F4.3.10)
cd ../..
git add addons/afr_ecm
git commit -m "rollback: afr_ecm para <commit>"
docker compose run --rm --no-deps web -d <db> -u afr_ecm --stop-after-init
```

### 8.2 Restore DB
Se rollback de código não basta:
```bash
docker compose stop web ocr_worker
docker compose exec db psql -U odoo -c "DROP DATABASE <db>;"
docker compose exec db psql -U odoo -c "CREATE DATABASE <db>;"
gunzip -c backup/db_<data>.sql.gz | docker compose exec -T db psql -U odoo <db>
rsync -a backup/filestore_<data>/ /var/lib/odoo/filestore/<db>/
docker compose up -d web ocr_worker
```

---

## 9. Dívida conhecida (não bloqueia piloto)

- `data/dms_access_group_links.xml` não automatizado — setup manual etapa 3
- Migração F4.3.10 (Auditor_Externo→root): se DB não tiver root nomeado `DOCUMENTAÇÃO`, fallback limpa `directory_ids` → auditor não vê NADA até admin fazer setup §3.3 manualmente
- Tests fresh-install 64/81 verdes (17 falhas pré-existentes em testes de auditor/RH-funcionário que dependem de implication graph + setup runtime — runtime OK em produção desde que §3.1-§3.3 sejam feitas)
- Bug design `group_ecm_area_rh_funcionario` IMPLICA `group_ecm_user` → rh_funcionario vê internal de colegas (ver `regression` em test_record_rules_refinement.py). Decisão: tratar como F4.4 (separar rh_funcionario de ecm_user)
- fs_storage remoto não implementado — todo conteúdo no filestore local
- Assinatura eletrônica, portal externo, classificação LLM → roadmap F4
- Tipo `TI_ACC_REV` não é seedado pelo módulo — criar manualmente se quiser ativar revogação TI

## 9.1 Histórico de bugs críticos corrigidos

| Versão | Bug | Fix |
|---|---|---|
| 16.0.2.0.1 | record_rules OR-collapse: regras `internal`/`restricted`/`confidential` no mesmo `group_ecm_user` se sabotavam mutuamente → vazamento entre donos | Unificada em 1 regra `rule_dms_file_ecm_user` + migration `pre-migration.py` remove rules antigas + test regression `test_confidentiality_rules.py` (5 tests) |
| 16.0.2.0.0 | `security_ecm_areas.xml` passava string como offset ao search → `TypeError %d` em fresh install | Linha removida |
| 16.0.2.0.0 | `dms_access_group_links.xml` IDs hardcoded 32-40 → ForeignKeyViolation | Removido do manifest, mantido como referência |

---

## 10. Contatos / Escalation
- Repo: https://github.com/afonsorcarvalho/afr_ecm
- Monorepo: github.com/afonsorcarvalho/engenapp
- Autor: afonsorcarvalho@gmail.com
