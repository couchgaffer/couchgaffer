.PHONY: check contracts backend frontend models types run deploy-test infra-test hooks hooks-test

check: contracts backend frontend ## Everything CI runs, except the image and secret scan

contracts:
	uv run --no-project --with jsonschema==4.26.0 python contracts/validate.py

backend:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy watchparty tests tools && uv run pytest

frontend:
	cd frontend && npm ci --ignore-scripts && npm test && npm run build

models: ## Regenerate backend/watchparty/models from contracts/
	cd backend && uv run datamodel-codegen

types: ## Regenerate frontend/src/types from contracts/
	cd frontend && npm run gen:types

run: ## Build and serve on http://127.0.0.1:8000
	docker compose up --build

deploy-test: ## scripts/deploy.sh and scripts/grant-local-agents.sh against stubs: no Azure calls
	scripts/test_deploy.sh
	scripts/test_grant.sh

infra-test: ## scripts/apply-infra.sh, check-redis-publish.sh and grant-postgres-app.sh against stubs: no Azure calls
	scripts/test_apply_infra.sh
	scripts/test_check_redis_publish.sh
	scripts/test_grant_postgres_app.sh

hooks: ## Use the committed git hooks in .githooks/ for this clone (#93)
	git config core.hooksPath .githooks

hooks-test: ## .githooks/pre-push against throwaway repositories
	scripts/test_hooks.sh
