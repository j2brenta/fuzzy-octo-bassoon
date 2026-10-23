.PHONY: up

up:
	docker compose build
	docker compose pull neo4j
