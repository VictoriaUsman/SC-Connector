.DEFAULT_GOAL := help

# --- Help ---
help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Auth ---
check-auth:  ## Verify correct GCP account (niv@kalilos.com) is active
	@./scripts/_common.sh check

# --- Environment ---
env-staging:  ## Switch to staging environment
	@./scripts/switch-env.sh staging

env-prod:  ## Switch to production environment (requires confirmation)
	@./scripts/switch-env.sh prod

env-status:  ## Show current environment and configuration
	@./scripts/env-status.sh

# --- Initialization ---
init-staging:  ## Initialize staging stack (one-time setup)
	@./scripts/init-stack.sh staging

init-prod:  ## Initialize production stack (one-time setup)
	@./scripts/init-stack.sh prod

# --- Deployment ---
deploy-all: check-auth  ## Deploy infrastructure + MCP server + frontend
	@./scripts/deploy-infra.sh
	@./scripts/deploy-mcp.sh
	@./scripts/deploy-frontend.sh

deploy-infra: check-auth  ## Deploy Pulumi infrastructure only
	@./scripts/deploy-infra.sh

deploy-frontend: check-auth  ## Build and deploy frontend only
	@./scripts/deploy-frontend.sh

deploy-mcp: check-auth  ## Build and deploy MCP server to Cloud Run
	@./scripts/deploy-mcp.sh

preview: check-auth  ## Preview infrastructure changes (dry run)
	@./scripts/preview.sh

destroy: check-auth  ## Destroy all infrastructure (requires confirmation)
	@./scripts/destroy.sh

# --- Secrets ---
secret-set:  ## Set a secret. Usage: make secret-set NAME=x VALUE=y
	@./scripts/secret-manager.sh set $(NAME) $(VALUE)

secret-get:  ## Get a secret value. Usage: make secret-get NAME=x
	@./scripts/secret-manager.sh get $(NAME)

secret-list:  ## List all secrets in current project
	@./scripts/secret-manager.sh list

local-secret-set:  ## Set a local secret (LOCAL_MODE). Usage: make local-secret-set NAME=x VALUE=y
	@./scripts/local-secret-manager.sh set $(NAME) $(VALUE)

local-secret-get:  ## Get a local secret value. Usage: make local-secret-get NAME=x
	@./scripts/local-secret-manager.sh get $(NAME)

local-secret-list:  ## List all local secret names
	@./scripts/local-secret-manager.sh list

# --- Logs & Monitoring ---
logs-fn:  ## Tail function logs. Usage: make logs-fn NAME=create_report
	@./scripts/tail-logs.sh function $(NAME)

logs-workflow:  ## Tail workflow execution logs
	@./scripts/tail-logs.sh workflow

workflows-status:  ## List recent workflow executions
	@./scripts/workflow-status.sh list

workflows-cancel:  ## Cancel a workflow execution. Usage: make workflows-cancel ID=x
	@./scripts/workflow-status.sh cancel $(ID)

# --- Local Development ---
local-fn:  ## Run a function locally. Usage: make local-fn NAME=create_report PORT=8080
	@./scripts/run-local.sh $(NAME) $(PORT)

local-frontend:  ## Start frontend dev server
	@cd frontend && npm run dev

local-mcp:  ## Run MCP server locally (stdio mode for Cursor/Claude Code)
	@cd mcp-server && MCP_TRANSPORT=stdio python server.py

local-mcp-http:  ## Run MCP server locally (HTTP mode on port 8081)
	@cd mcp-server && KALILOS_API_URL=$(or $(API_URL),http://localhost:8080) python server.py

# --- Testing ---
test:  ## Run all tests
	@./scripts/run-tests.sh all

test-fn:  ## Test a specific function. Usage: make test-fn NAME=create_report
	@./scripts/run-tests.sh function $(NAME)

test-integration:  ## Run integration tests against current environment
	@./scripts/run-tests.sh integration

seed-firestore:  ## Seed Firestore with test client. Usage: make seed-firestore [CLIENT_ID=test-client]
	@python3 ./scripts/seed-firestore.py --client-id $(or $(CLIENT_ID),test-client)

wipe-firestore:  ## Wipe Firestore data (schedules, jobs, locks). Add ALL=1 to also wipe clients
	@python3 ./scripts/wipe-firestore.py $(if $(ALL),--all,)

workflow-trigger: check-auth  ## Trigger a workflow. Usage: make workflow-trigger API_SOURCE=sp_api CLIENT_ID=test-client MARKETPLACE=US REPORT_TYPE=GET_FLAT_FILE_OPEN_LISTINGS_DATA [START_DATE=2026-03-01] [END_DATE=2026-03-15]
	@./scripts/trigger-workflow.sh $(API_SOURCE) $(CLIENT_ID) $(MARKETPLACE) $(REPORT_TYPE) $(START_DATE) $(END_DATE)

# --- Health ---
health:  ## Run health checks against current environment
	@./scripts/health-check.sh

.PHONY: help check-auth \
	env-staging env-prod env-status \
	init-staging init-prod \
	deploy-all deploy-infra deploy-frontend deploy-mcp preview destroy \
	secret-set secret-get secret-list \
	local-secret-set local-secret-get local-secret-list \
	logs-fn logs-workflow workflows-status workflows-cancel \
	local-fn local-frontend local-mcp local-mcp-http \
	test test-fn test-integration seed-firestore wipe-firestore workflow-trigger \
	health
