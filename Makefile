RUNTIMEDB_IMAGE ?= ghcr.io/hotdata-dev/runtimedb:latest
LOCAL_PORT ?= 3000
LOCAL_CONTAINER := hotmemory-runtimedb

.PHONY: verify local-up local-down

verify:
	uv run --group dev ruff check .
	uv run --group dev ruff format --check .
	uv run --no-project python scripts/check_links.py

local-up:
	docker run -d --rm --pull always --name $(LOCAL_CONTAINER) -p $(LOCAL_PORT):3000 \
		-e RUNTIMEDB_SECRET_KEY="$$(openssl rand -base64 32)" \
		-e RUNTIMEDB_AUTH__ALLOW_UNAUTHENTICATED=true \
		-e RUNTIMEDB_ENGINE__SQL_WRITES=true \
		$(RUNTIMEDB_IMAGE)
	@for i in $$(seq 1 60); do \
		curl -fsS -o /dev/null http://localhost:$(LOCAL_PORT)/health && \
			echo "RuntimeDB is up at http://localhost:$(LOCAL_PORT)" && exit 0; \
		sleep 1; \
	done; \
	echo "RuntimeDB did not answer /health in 60 seconds"; \
	docker logs --tail 50 $(LOCAL_CONTAINER); \
	exit 1

local-down:
	docker stop $(LOCAL_CONTAINER)
