SHELL := /usr/bin/env bash
INFRA_DIR := infra
SERVER_DIR := server
ROOT_DIR := $(shell git -C $(CURDIR) rev-parse --show-toplevel)

IMAGE_TAG ?= $(shell git rev-parse --short HEAD)

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  %-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: test
test: ## Run the Python test suite
	cd $(SERVER_DIR) && .venv/bin/pytest tests/ -q

.PHONY: lint
lint: ## Run ruff + mypy
	cd $(SERVER_DIR) && .venv/bin/ruff check realtalk_web tests
	cd $(SERVER_DIR) && .venv/bin/mypy realtalk_web

.PHONY: install-dev
install-dev: ## Create .venv and install editable dev deps
	cd $(SERVER_DIR) && uv venv --python 3.12 .venv && uv pip install -e '.[dev]'

.PHONY: build-images
build-images: ## Build both container images via Cloud Build (no local docker needed)
	@echo "Building api + ws images with tag $(IMAGE_TAG) via Cloud Build..."
	bash scripts/deploy.sh build $(IMAGE_TAG)

.PHONY: deploy-plan
deploy-plan: ## terraform plan (HCP remote)
	cd $(INFRA_DIR) && terraform init -upgrade && terraform plan -var="image_tag=$(IMAGE_TAG)"

.PHONY: deploy
deploy: ## Build images, then terraform apply
	bash scripts/deploy.sh all $(IMAGE_TAG)

.PHONY: deploy-apply-only
deploy-apply-only: ## terraform apply only (skip image build)
	cd $(INFRA_DIR) && terraform init && terraform apply -auto-approve -var="image_tag=$(IMAGE_TAG)"

.PHONY: deploy-destroy
deploy-destroy: ## Tear down all infra (confirms before running)
	@echo "This will DESTROY all realtalk-web infra. Type 'yes' to continue:"
	@read confirm && [ "$$confirm" = "yes" ] && cd $(INFRA_DIR) && terraform destroy

.PHONY: logs-api
logs-api: ## Tail realtalk-api logs
	gcloud run services logs tail realtalk-api --region us-central1

.PHONY: logs-ws
logs-ws: ## Tail realtalk-ws logs
	gcloud run services logs tail realtalk-ws --region us-central1

.PHONY: kill-switch-on
kill-switch-on: ## Route zero traffic to realtalk-ws (instant shutoff)
	gcloud run services update realtalk-ws --region us-central1 --no-traffic

.PHONY: kill-switch-off
kill-switch-off: ## Restore traffic to realtalk-ws
	gcloud run services update-traffic realtalk-ws --region us-central1 --to-latest

.PHONY: smoke-deployed
smoke-deployed: ## Run end-to-end tests against the deployed services
	@API_URL="$$(cd $(INFRA_DIR) && terraform output -raw api_url)"; \
	WS_URL="$$(cd $(INFRA_DIR) && terraform output -raw ws_url | sed 's|^https://|wss://|')"; \
	echo "api: $$API_URL"; echo "ws:  $$WS_URL"; \
	cd $(SERVER_DIR) && REALTALK_API_URL=$$API_URL REALTALK_WS_URL=$$WS_URL \
	  .venv/bin/pytest tests_integration/ -v
