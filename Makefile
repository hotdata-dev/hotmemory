export RUNTIMEDB_IMAGE ?= ghcr.io/hotdata-dev/runtimedb:latest
export LOCAL_PORT ?= 3000

STORAGE_URL := http://127.0.0.1:9000

.PHONY: verify local-up local-down local-pull

verify:
	uv run --group dev ruff check .
	uv run --group dev ruff format --check .
	uv run --no-project python scripts/check_links.py

local-up:
	docker compose up -d --wait catalog storage
	@for i in $$(seq 1 30); do \
		curl -s -o /dev/null $(STORAGE_URL)/ && break; \
		sleep 1; \
	done
	curl -fsS -o /dev/null -X PUT --aws-sigv4 "aws:amz:us-east-1:s3" \
		--user hotmemory:hotmemory-local $(STORAGE_URL)/runtimedb
	RUNTIMEDB_SECRET_KEY="$$(openssl rand -base64 32)" docker compose up -d runtimedb
	@for i in $$(seq 1 60); do \
		curl -fs -o /dev/null http://localhost:$(LOCAL_PORT)/health && \
			echo "RuntimeDB is up at http://localhost:$(LOCAL_PORT)" && exit 0; \
		sleep 1; \
	done; \
	echo "RuntimeDB did not answer /health in 60 seconds"; \
	docker compose logs --tail 50 runtimedb; \
	exit 1

local-down:
	docker compose down

local-pull:
	docker compose pull
