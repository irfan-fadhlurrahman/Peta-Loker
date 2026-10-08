# Shortcuts for every command in this repo. `make help` lists them.
#
# Variables (override on the command line, e.g. `make run SOURCE=dealls`):
#   SOURCE     a source name from config/sources.yaml, or all
#   MAX_PAGES  cap on detail pages fetched per source this run
#   PORT       port for dashboard-serve

SOURCE    ?= all
MAX_PAGES ?=
PORT      ?= 8000
TAILWIND  ?= bin/tailwindcss

UV := uv run python

.DEFAULT_GOAL := help
.PHONY: help install db-init reference run daily reparse dedup normalise enrich quality dashboard dashboard-css \
        dashboard-serve pipeline demo evaluate export deploy test lint

help: ## List all targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install Python deps and Playwright Chromium
	uv sync
	uv run playwright install chromium

db-init: ## Create the database and load reference data (KBJI 2026, BPS regions)
	$(UV) scripts/db_init.py

reference: ## Rebuild reference/*.csv from their public sources (KBJI PDF, BPS API)
	$(UV) scripts/build_kbji.py
	$(UV) scripts/build_regions.py

run: ## Collect postings: make run SOURCE=dealls [MAX_PAGES=50]
	$(UV) scripts/run.py $(SOURCE) $(if $(MAX_PAGES),--max-pages $(MAX_PAGES))

daily: ## Scheduled daily job: collect all sources, normalise, dedup, quality gate (log in data/logs/)
	@mkdir -p data/logs
	$(UV) scripts/run.py all >> data/logs/daily_$$(date +%F).log 2>&1; 	$(UV) scripts/normalise.py >> data/logs/daily_$$(date +%F).log 2>&1; 	$(UV) scripts/dedup.py >> data/logs/daily_$$(date +%F).log 2>&1; 	$(UV) scripts/quality_check.py >> data/logs/daily_$$(date +%F).log 2>&1

reparse: ## Re-parse stored raw pages without fetching: make reparse SOURCE=dealls
	$(UV) scripts/reparse.py $(SOURCE)

normalise: ## Normalise salary, education, experience, location, employment type
	$(UV) scripts/normalise.py

dedup: ## Group postings into vacancies (exact, repost, cross-source)
	$(UV) scripts/dedup.py

enrich: ## Code vacancies to KBJI 2026 / KBLI and extract skills with the LLM
	$(UV) scripts/enrich.py

quality: ## Data quality gate (exit 1 on a blocking failure)
	$(UV) scripts/quality_check.py

dashboard: ## Write the public, masked dashboard JSON to dashboard/data/
	$(UV) scripts/generate_dashboard_data.py --public --out-dir dashboard/data

dashboard-css: ## Rebuild dashboard/styles.css (Tailwind standalone CLI; TAILWIND=path)
	cd dashboard && ../$(TAILWIND) -c tailwind.config.js -i input.css -o styles.css --minify

dashboard-serve: ## Preview the dashboard at http://localhost:8000 (PORT=... to change)
	cd dashboard && uv run python -m http.server $(PORT) --bind 127.0.0.1

pipeline: run normalise dedup enrich quality dashboard ## Full pipeline: collect → normalise → dedup → enrich → quality → dashboard

demo: ## Run the whole pipeline on the synthetic sample (no network, no accounts)
	$(UV) scripts/demo.py

evaluate: ## Score KBJI coding against the hand-labelled set (docs/results.md)
	$(UV) scripts/evaluate.py

export: ## Export vacancies to CSV under data/export/
	$(UV) scripts/export.py

deploy: ## Deploy dashboard/ to Vercel, only after the public-data checks pass
	$(UV) scripts/deploy_check.py
	cd dashboard && vercel deploy --prod --yes

test: ## Run the test suite
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check .
