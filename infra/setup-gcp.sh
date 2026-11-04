#!/usr/bin/env bash
# Provisiona (uma única vez) os recursos do GCP usados pelo CI/CD.
# Pode ser executado de novo: recursos já existentes são mantidos.
#
# Uso (Cloud Shell ou máquina com gcloud autenticado como dono do projeto):
#   bash infra/setup-gcp.sh
# Os segredos são lidos de backend/.env, sem serem exibidos.
set -euo pipefail

PROJECT_ID="rotas-medicas-fiap"
REGION="southamerica-east1"
REPO="rotas-medicas"                                   # Artifact Registry
GITHUB_REPO="PosTech-FIAP-2026/IADT-Fase2-Tech-challenge"
DEPLOY_REFS="'refs/heads/main'"   # branches que podem fazer deploy
DEPLOY_SA_NAME="github-deployer"
RUNTIME_SA_NAME="rotas-medicas-run"
POOL="github"
PROVIDER="github"
ENV_FILE="backend/.env"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> APIs"
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com firebase.googleapis.com firebasehosting.googleapis.com

echo "==> Artifact Registry"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --location "$REGION" --repository-format docker \
    --description "Imagens do backend Rotas Médicas"
# Mantém só as 5 imagens mais recentes (evita custo de armazenamento).
cat > /tmp/ar-cleanup.json <<'JSON'
[{"name":"keep-recent","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":5}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"0s"}}]
JSON
gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" --policy /tmp/ar-cleanup.json --no-dry-run

echo "==> Contas de serviço"
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" --display-name "Rotas Médicas - Cloud Run (runtime)"
gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$DEPLOY_SA_NAME" --display-name "GitHub Actions - deploy"

echo "==> Segredos (Secret Manager)"
env_value() { grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2- | tr -d '\r'; }
put_secret() { # nome, valor
  if ! gcloud secrets describe "$1" >/dev/null 2>&1; then
    gcloud secrets create "$1" --replication-policy automatic
  fi
  printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- >/dev/null
  gcloud secrets add-iam-policy-binding "$1" --member "serviceAccount:${RUNTIME_SA}" \
    --role roles/secretmanager.secretAccessor >/dev/null
  echo "    $1 atualizado"
}
put_secret google-maps-api-key "$(env_value GOOGLE_MAPS_API_KEY)"
put_secret openrouter-api-key "$(env_value OPENROUTER_API_KEY)"
put_secret auth-password "$(env_value AUTH_PASSWORD)"
# JWT de produção: novo segredo aleatório, diferente do ambiente local.
gcloud secrets describe jwt-secret >/dev/null 2>&1 ||
  put_secret jwt-secret "$( (python -c 'import secrets; print(secrets.token_urlsafe(48))' 2>/dev/null) || python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

echo "==> Permissões da conta de deploy"
for role in roles/run.admin roles/artifactregistry.writer roles/firebasehosting.admin roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${DEPLOY_SA}" --role "$role" \
    --condition None >/dev/null
done
# Permite ao deploy publicar o Cloud Run usando a conta de runtime.
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:${DEPLOY_SA}" --role roles/iam.serviceAccountUser >/dev/null

# Firebase Hosting fica no projeto criado pelo console do Firebase.
FIREBASE_PROJECT="rotas-medicas-fiap-70524"
for role in roles/firebasehosting.admin roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$FIREBASE_PROJECT" --member "serviceAccount:${DEPLOY_SA}" --role "$role" \
    --condition None >/dev/null
done

echo "==> Workload Identity Federation (GitHub Actions sem chave)"
gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location global --workload-identity-pool "$POOL" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --location global --workload-identity-pool "$POOL" --display-name "GitHub" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${GITHUB_REPO}' && assertion.ref in [${DEPLOY_REFS}]"
# Só workflows das branches em DEPLOY_REFS deste repositório podem assumir a conta de deploy.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

echo "==> Alerta de orçamento (R\$ 50, avisos em 50%, 90%, 100% e na previsão de 100%)"
BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' | sed 's#billingAccounts/##')"
gcloud services enable billingbudgets.googleapis.com
if ! gcloud billing budgets list --billing-account "$BILLING_ACCOUNT" --billing-project "$PROJECT_ID" \
     --format='value(displayName)' | grep -q "Rotas M"; then
  gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" --billing-project "$PROJECT_ID" \
    --display-name "Rotas Médicas - alerta R\$50" --budget-amount 50BRL --filter-projects "projects/${PROJECT_ID}" \
    --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0 \
    --threshold-rule percent=1.0,basis=forecasted-spend
fi

echo
echo "Pronto. Valores usados em .github/workflows/deploy.yml:"
echo "  WIF_PROVIDER = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  DEPLOY_SA    = ${DEPLOY_SA}"
echo "  RUNTIME_SA   = ${RUNTIME_SA}"
