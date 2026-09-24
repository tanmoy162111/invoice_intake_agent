.PHONY: dev seed generate check check-api check-web eval demo-reset gen-api

dev:            ## start everything (db, api, worker, web)
	docker compose up --build

seed:           ## load demo master data into the db and stage invoices in data/inbox
	./scripts/seed.sh

generate:       ## regenerate the synthetic dataset (deterministic; existing golden files are kept)
	PYTHONPATH=data uv run --project apps/api --group generator python -m generator.generate

check: check-api check-web   ## lint + types + tests

check-api:
	cd apps/api && uv run ruff check . ../../data/generator && uv run ruff format --check . ../../data/generator && uv run mypy && uv run pytest -q --cov=intake.core --cov-fail-under=90

check-web:
	cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build

gen-api:        ## regenerate web API types (API must be running at API_OPENAPI_URL)
	cd apps/web && pnpm gen:api

eval:           ## full evaluation (costs money; arrives in M10)
	@echo "eval: not implemented yet (M10)"

demo-reset:     ## reset to the clean demo state (arrives in M13)
	@echo "demo-reset: not implemented yet (M13)"
