# CLAUDE.md — afr_ecm

## Repositório

Este diretório é um **git submodule** referenciado pelo monorepo
`odoo_engenapp` em `addons/afr_ecm/`.

| Item | Valor |
|---|---|
| Repo standalone | `https://github.com/afonsorcarvalho/afr_ecm.git` |
| Branch padrão | `main` |
| Path no monorepo | `addons/afr_ecm` |
| Conversão para submodule | 2026-05-12 (commit monorepo `b2bc229`) |

## Regras de Commit / Push (CRÍTICO)

**Commits e pushes deste módulo SEMPRE de dentro deste diretório.** Nunca
operar via path do monorepo (`addons/afr_ecm/...`).

```bash
cd /home/afonso/docker/odoo_engenapp/addons/afr_ecm
git add <paths-relativos-ao-módulo>     # ex: models/dms_file.py
git commit -m "feat(afr_ecm): ..."
git push origin main
```

Após push, opcionalmente atualizar pointer no monorepo:
```bash
cd /home/afonso/docker/odoo_engenapp
git add addons/afr_ecm
git commit -m "chore: bump afr_ecm submodule"
git push
```

**Agentes (haiku):** invocar `git-commit-push` com `cwd` apontando pra
ESTE dir, não pro monorepo.

## Stack e contexto rápido

- Odoo 16.0
- Depende de OCA `dms` (Document Management System)
- Módulo ECM corporativo: tipos de documento, metadata, audit log,
  workflow de aprovação, OCR opt-in, restrição de download por tipo,
  expiração + alertas, share link público (`/ecm/share/<id>/<token>`)
- Versão atual: ver `__manifest__.py`
- Grupos: `group_ecm_user`, `group_ecm_manager`, `group_ecm_admin`
  (security/security.xml)

## Convenções

- Bump version em `__manifest__.py` a cada feature/fix relevante (formato
  `16.0.X.Y.Z`)
- Audit log via `afr.ecm.audit.log` (mixin `afr.ecm.audit.mixin`)
- Tipos custom: prefixar `afr.ecm.*` (ex: `afr.ecm.document.type`)
- OCR: opt-in via `document_type_id.ocr_enabled` OU
  `dms.file.ocr_enabled` per-file
