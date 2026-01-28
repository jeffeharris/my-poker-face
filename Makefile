.PHONY: help build up down logs shell test clean prod

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