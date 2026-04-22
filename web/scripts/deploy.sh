#!/usr/bin/env bash
# Build + push both realtalk-web images via Cloud Build, then apply
# Terraform. No local docker daemon required.
#
# Usage:
#   scripts/deploy.sh build <tag>     — build both images only
#   scripts/deploy.sh apply <tag>     — terraform apply only
#   scripts/deploy.sh all <tag>       — build + apply (default)

set -euo pipefail

MODE="${1:-all}"
TAG="${2:-$(git rev-parse --short HEAD)}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"
INFRA_DIR="$ROOT_DIR/infra"

# Read project id from tfvars so one source of truth.
if [ ! -f "$INFRA_DIR/terraform.tfvars" ]; then
  echo "error: $INFRA_DIR/terraform.tfvars missing (copy from terraform.tfvars.example)" >&2
  exit 1
fi

PROJECT_ID="$(awk -F '=' '/^gcp_project_id[[:space:]]*=/{ gsub(/[" ]/, "", $2); print $2 }' "$INFRA_DIR/terraform.tfvars")"
REGION="$(awk -F '=' '/^gcp_region[[:space:]]*=/{ gsub(/[" ]/, "", $2); print $2 }' "$INFRA_DIR/terraform.tfvars")"
REGION="${REGION:-us-central1}"

if [ -z "$PROJECT_ID" ]; then
  echo "error: gcp_project_id not found in terraform.tfvars" >&2
  exit 1
fi

AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="realtalk"
IMAGE_API="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/realtalk-api:${TAG}"
IMAGE_WS="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/realtalk-ws:${TAG}"

build_images() {
  echo "==> staging realtalk source into ws build context"
  rm -rf "$SERVER_DIR/realtalk-src"
  # Stage a minimal copy of the realtalk package (no web/, no tests, no .venv).
  rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
        --exclude='web' --exclude='tests' \
        --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
        "$REPO_ROOT/" "$SERVER_DIR/realtalk-src/"

  echo "==> building realtalk-api via Cloud Build → $IMAGE_API"
  gcloud builds submit "$SERVER_DIR" \
    --project="$PROJECT_ID" \
    --config=<(cat <<EOF
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build', '-f', 'Dockerfile.api', '-t', '$IMAGE_API', '.']
images: ['$IMAGE_API']
EOF
)

  echo "==> building realtalk-ws via Cloud Build → $IMAGE_WS"
  gcloud builds submit "$SERVER_DIR" \
    --project="$PROJECT_ID" \
    --config=<(cat <<EOF
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build', '-f', 'Dockerfile.ws', '-t', '$IMAGE_WS', '.']
images: ['$IMAGE_WS']
EOF
)

  rm -rf "$SERVER_DIR/realtalk-src"
  echo "==> image build complete"
}

apply_terraform() {
  echo "==> terraform apply (tag=$TAG)"
  cd "$INFRA_DIR"
  terraform init -upgrade
  terraform apply -auto-approve -var="image_tag=$TAG"
}

health_check() {
  echo "==> waiting for /_health"
  API_URL="$(cd "$INFRA_DIR" && terraform output -raw api_url)"
  WS_URL="$(cd "$INFRA_DIR" && terraform output -raw ws_url)"
  for _ in $(seq 1 20); do
    if curl -sSf "$API_URL/_health" >/dev/null; then
      echo "==> api healthy: $API_URL"
      break
    fi
    sleep 3
  done
  for _ in $(seq 1 20); do
    if curl -sSf "$WS_URL/_health" >/dev/null; then
      echo "==> ws healthy: $WS_URL"
      break
    fi
    sleep 3
  done
  echo ""
  echo "api_url: $API_URL"
  echo "ws_url:  $WS_URL"
}

case "$MODE" in
  build)
    build_images
    ;;
  apply)
    apply_terraform
    health_check
    ;;
  all)
    build_images
    apply_terraform
    health_check
    ;;
  *)
    echo "usage: $0 [build|apply|all] [tag]" >&2
    exit 1
    ;;
esac
