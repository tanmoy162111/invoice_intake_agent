.PHONY: e2e dev seed ingest-inbox docs-page generate check check-api check-web eval demo-reset gen-api demo-pipeline

dev:            ## start everything (db, api, worker, web)
	./scripts/ensure-env.sh
	docker compose up --build

seed:           ## load demo master data into the db and stage invoices in data/inbox
	./scripts/seed.sh

ingest-inbox:   ## feed data/inbox through ingestion; the worker (make dev) then processes it
	./scripts/ingest-inbox.sh

docs-page:      ## build build/handbook.html from docs/report.md and docs/manual.md (publish after each milestone)
	python3 scripts/build-docs-page.py

generate:       ## regenerate the synthetic dataset (deterministic; existing golden files are kept)
	PYTHONPATH=data uv run --project apps/api --group generator python -m generator.generate

check: check-api check-web   ## lint + types + tests

check-api:
	cd apps/api && uv run ruff check . ../../data/generator && uv run ruff format --check . ../../data/generator && uv run mypy && uv run pytest -q --cov=intake.core --cov-fail-under=90

check-web:
	cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build

e2e:            ## browser test on a throwaway stack with recorded model answers (needs Docker and Chromium: cd apps/web && pnpm exec playwright install chromium)
	./scripts/e2e.sh

gen-api:        ## regenerate the web app's API types from the code (or from a running API: API_OPENAPI_URL=...)
	./scripts/gen-api.sh

demo-pipeline:  ## run the seed invoices through the real pipeline with recorded answers (needs DATABASE_URL, STORAGE_DIR)
	uv run --project apps/api python scripts/load-demo-pipeline.py

eval:           ## full evaluation on the golden set (costs money: uses the configured model backend)
	uv run --project apps/api python eval/run_eval.py

eval-ci:        ## free, deterministic eval subset with recorded answers (what CI runs)
	RECORDED_DIR=data/golden/recorded SUBSET=$${SUBSET:-3} LLM_PROVIDER=recorded \
		EXTRACTION_MODEL=qwen/qwen2.5-vl-72b-instruct \
		uv run --project apps/api python eval/run_eval.py

demo-reset:     ## reset to the clean demo state (arrives in M13)
	@echo "demo-reset: not implemented yet (M13)"
