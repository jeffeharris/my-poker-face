.PHONY: help build up down logs shell test test-quick test-strategy test-repos test-cash test-memory test-flask test-llm test-last clean prod testflight

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build all Docker images
	docker compose build

up: ## Start all services in development mode
	docker compose up -d

down: ## Stop all services
	docker compose down

logs: ## Show logs from all services
	docker compose logs -f

backend-logs: ## Show backend logs only
	docker compose logs -f backend

frontend-logs: ## Show frontend logs only
	docker compose logs -f frontend

shell: ## Access backend container shell
	docker compose exec backend bash

frontend-shell: ## Access frontend container shell
	docker compose exec frontend sh

test: ## Run tests in backend container
	docker compose exec backend python -m pytest

# --- Compartmentalized test targets (see docs/plans/TEST_WAIT_TIME_REDUCTION.md) ---
# Run the bucket that covers the code you touched. The full `test` target / CI
# remains the merge gate.

test-quick: ## Fast loop: skip slow/integration/llm/simulation tests
	docker compose exec backend python -m pytest -n auto \
		-m "not slow and not integration and not llm and not simulation"

test-strategy: ## Bot strategy, classification, exploitation
	docker compose exec backend python -m pytest tests/test_strategy/

test-repos: ## Repositories + schema/migration (incl. root schema-migration tests)
	docker compose exec backend python -m pytest tests/test_repositories/ tests/test_schema_migration_v*.py

test-cash: ## Cash mode economy + lobby (name-matched across the tree)
	docker compose exec backend python -m pytest -k cash

test-memory: ## Psychology / relationships / memory
	docker compose exec backend python -m pytest tests/test_memory/

test-flask: ## Routes / auth / Socket.IO (marker-selected)
	docker compose exec backend python -m pytest -m flask

test-llm: ## LLM client/assistant (slow, opt-in)
	docker compose exec backend python -m pytest -m llm

test-last: ## Re-run last failures only
	docker compose exec backend python -m pytest --lf

clean: ## Clean up containers, volumes, and data
	docker compose down -v
	rm -rf ./data/poker_games.db
	rm -rf ./react/react/node_modules
	rm -rf ./react/react/dist

prod: ## Start services in production mode
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

prod-down: ## Stop production services
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

restart: ## Restart all services
	docker compose restart

ps: ## Show running containers
	docker compose ps

install-local: ## Install dependencies locally (for IDE support)
	pip install -r requirements.txt
	cd react/react && npm install

# --- iOS TestFlight release (needs a paid Apple Developer account) -----------
# One shot: build the prod-pointed web bundle, archive a Release build, export an
# App Store .ipa (via react/react/ios/App/ExportOptions.plist), and upload to
# App Store Connect. The API key must be staged at
# ~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8 (Apple's standard spot).
#   make testflight ASC_KEY_ID=JCN4277U7Z ASC_ISSUER_ID=cca6acd8-...
# BUILD_NUMBER defaults to a timestamp so each upload gets a unique, increasing
# CFBundleVersion (App Store Connect rejects duplicate build numbers).
IOS_APP_DIR  := react/react/ios/App
PROD_URL     ?= https://mypokerfacegame.com
BUILD_NUMBER ?= $(shell date +%Y%m%d%H%M)

testflight: ## Build, archive & upload an App Store .ipa to TestFlight (needs ASC_KEY_ID, ASC_ISSUER_ID)
	@test -n "$(ASC_KEY_ID)"    || { echo "ERROR: ASC_KEY_ID required (App Store Connect API key id)"; exit 1; }
	@test -n "$(ASC_ISSUER_ID)" || { echo "ERROR: ASC_ISSUER_ID required (App Store Connect issuer id)"; exit 1; }
	cd react/react && VITE_API_URL=$(PROD_URL) VITE_SOCKET_URL=$(PROD_URL) npm run build
	cd react/react && npx cap copy ios
	rm -rf $(IOS_APP_DIR)/build/App.xcarchive $(IOS_APP_DIR)/build/export
	xcodebuild -workspace $(IOS_APP_DIR)/App.xcworkspace -scheme App -configuration Release \
		-destination 'generic/platform=iOS' \
		-archivePath $(IOS_APP_DIR)/build/App.xcarchive \
		CURRENT_PROJECT_VERSION=$(BUILD_NUMBER) \
		archive -allowProvisioningUpdates
	xcodebuild -exportArchive \
		-archivePath $(IOS_APP_DIR)/build/App.xcarchive \
		-exportPath $(IOS_APP_DIR)/build/export \
		-exportOptionsPlist $(IOS_APP_DIR)/ExportOptions.plist \
		-allowProvisioningUpdates
	xcrun altool --upload-app --type ios \
		--file $(IOS_APP_DIR)/build/export/App.ipa \
		--apiKey $(ASC_KEY_ID) --apiIssuer $(ASC_ISSUER_ID)
	@echo "Uploaded. Build is processing in App Store Connect -> TestFlight (~5-15 min)."
