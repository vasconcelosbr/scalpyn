# GitHub Secrets Necessários

Vá em: Settings → Secrets and variables → Actions → New repository secret

## Google Cloud Run (Backend)
- `GCP_PROJECT_ID` — ID do projeto GCP (ex: scalpyn-prod)
- `GCP_SA_KEY` — JSON da Service Account com permissões: Cloud Run Admin, Storage Admin, Container Registry
- `DATABASE_URL` — URL completa do PostgreSQL/CloudSQL (ex: postgresql+asyncpg://user:pass@/dbname?host=/cloudsql/project:region:instance)
- `REDIS_URL` — URL do Redis (ex: redis://10.x.x.x:6379/0)
- `JWT_SECRET` — String aleatória segura (min 32 chars)
- `ENCRYPTION_KEY` — String de exatamente 32 chars para Fernet

## Vercel (Frontend)
- `VERCEL_TOKEN` — Token da conta Vercel (https://vercel.com/account/tokens)
- `VERCEL_ORG_ID` — ID da org Vercel (em .vercel/project.json após `vercel link`)
- `VERCEL_PROJECT_ID` — ID do projeto Vercel

## Variáveis de ambiente do Frontend (configurar no painel Vercel)
- `NEXT_PUBLIC_API_URL` — URL do Cloud Run em produção (ex: https://scalpyn-api-xxxx-uc.a.run.app/api)
