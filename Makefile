.PHONY: verify

verify:
	uv run --group dev ruff check .
	uv run --group dev ruff format --check .
	uv run --no-project python scripts/check_links.py
