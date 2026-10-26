.PHONY: up down docker-down

up:
	docker compose build
	docker compose pull neo4j

down docker-down:
	docker compose down
