.PHONY: dev seed check check-api check-web eval demo-reset gen-api

dev:            ## start everything (db, api, worker, web)
	docker compose up --build

seed:           ## load demo data (arrives in M1)
	@echo "seed: not implemented yet (M1)"

check: check-api check-web   ## lint + types + tests

check-api:
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q

check-web:
	cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build

gen-api:        ## regenerate web API types (API must be running at API_OPENAPI_URL)
	cd apps/web && pnpm gen:api

eval:           ## full evaluation (costs money; arrives in M10)
	@echo "eval: not implemented yet (M10)"

demo-reset:     ## reset to the clean demo state (arrives in M13)
	@echo "demo-reset: not implemented yet (M13)"
