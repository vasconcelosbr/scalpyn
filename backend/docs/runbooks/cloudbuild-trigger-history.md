# Cloud Build Trigger & Recovery Lessons (2026-05-08)

Operational know-how moved out of `replit.md` to keep the top-level overview lean. Re-read this before touching the Cloud Build trigger, `cloudbuild.yaml` substitutions, secret bindings, or the GitHub→Replit sync flow.

## 1. Cloud Build trigger reativado em `cloudbuild.yaml` (Task #244 fechamento)

O trigger `806f4336-...` (rmgpgab-scalpyn-...) era template auto-gerado com `build:` inline (só deployava `scalpyn`, ignorava workers/beat — causa raiz da topologia quebrar 6 builds em silêncio). Reimportado via `gcloud builds triggers import` apontando pra `filename: cloudbuild.yaml`.

Detalhes que NÃO podem mudar:
- **SA real do trigger:** `330575088921-compute@developer.gserviceaccount.com` (NÃO `scalpyn-service-account@`). Precisa de `roles/secretmanager.secretAccessor` em CADA secret usado: `redis-url`, `database-url`, `jwt-secret`, `encryption-key`, `ai-keys-encryption-key`.
- **`_REPO=cloud-run-source-deploy`** (NÃO `scalpyn` — confunde com nome do service; o AR repo real foi criado pelo flow original).
- **`substitution_option: ALLOW_LOOSE`** é obrigatório porque o trigger injeta `_AR_HOSTNAME, _AR_PROJECT_ID, _AR_REPOSITORY, _DEPLOY_REGION, _PLATFORM, _SERVICE_NAME, _TRIGGER_ID` que nosso YAML não consome. **NÃO trocar pra `MUST_MATCH`** — quebra silencioso na próxima atualização do template gcloud.

## 2. Cloud Build YAML escape rules

`cloudbuild.yaml` interpreta `$VAR` e `${VAR}` como substitutions (built-ins ou `_user`). Toda var de SHELL em scripts inline precisa ser **`$$VAR`** ou **`$${VAR}`** — caso contrário o build é REJEITADO antes de iniciar com:

```
invalid value for 'build.substitutions': key in the template "VAR" is not a valid built-in substitution
```

Isso vale também pra `$(cmd)` → `$$(cmd)`. Built-ins legítimos (`${PROJECT_ID}`, `${BUILD_ID}`, `${COMMIT_SHA}`, `${SHORT_SHA}`, `${REVISION_ID}`, `${BRANCH_NAME}`, `${TAG_NAME}`, `${LOCATION}`, `${REPO_NAME}`) e user-subs (`${_REGION}`, `${_REPO}`, `${_SERVICE}`) ficam como estão.

Pre-push lint (deve retornar vazio):

```bash
grep -nP '(?<!\$)\$(?!\$|\{|\()[A-Za-z_][A-Za-z0-9_]*' cloudbuild.yaml
```

## 3. `--update-secrets` é INCREMENTAL no `gcloud run deploy`

Comentar/remover uma entrada `--update-secrets KEY=secret:latest` do `cloudbuild.yaml` **não** remove a env do spec do service — Cloud Run continua tentando montar o secret e o deploy falha com `Permission denied on secret: ...` se o secret nem existir.

Pra desligar de verdade:

```bash
gcloud run services update SVC --remove-secrets=KEY  # idempotente
```

O análogo `--update-env-vars` tem o mesmo comportamento — usa `--remove-env-vars=KEY`. Não confundir com `--set-secrets`/`--set-env-vars` que substituem a lista inteira (bem mais perigoso em prod).

## 4. `gcloud run services describe` NÃO aceita `--filter`

Só `list`/`revisions list` aceitam. Pior: a sintaxe `--format='value(field.filter("k:v").extract(x))'` em projection sobre lista **silenciosamente retorna a lista inteira concatenada** (não filtra).

Pra extrair `status.conditions[?type==Ready].status` confiável:

```bash
gcloud run services describe SVC --region=R --format=json \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(next((c.get("status","?") for c in d.get("status",{}).get("conditions",[]) if c.get("type")=="Ready"),"MISSING"))'
```

A imagem builder `gcr.io/google.com/cloudsdktool/cloud-sdk` ships python3 por default. Esse pattern é usado pelo step `topology-check` em `cloudbuild.yaml`. Em scripts bash do cloudbuild.yaml, manter o `python3 -c '...'` em UMA LINHA — heredoc/multiline quebra o YAML scalar.

## 5. GitHub é a SoT do trigger; Replit auto-pusha via gitsafe-backup

O `origin` do repo no Replit aponta pra `gitsafe-backupgit://...` (backup interno), NÃO pro GitHub `vasconcelosbr/scalpyn`. A sincronização Replit→GitHub acontece automaticamente a cada checkpoint do agent (confirmado: SHAs `058c7764`, `4221a942`, `dcafdbe0` apareceram no GitHub sem `git push` explícito).

Validar antes de qualquer trigger build:

```bash
curl -s "https://api.github.com/repos/vasconcelosbr/scalpyn/commits/main" | jq -r '.sha[:12]'
```

Deve bater com o último checkpoint do Replit. Se não bater, o usuário precisa clicar Push na sidebar Git.
