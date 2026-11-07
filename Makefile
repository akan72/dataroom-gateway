.PHONY: setup dev traffic capture index query verify inference-smoke demo-proof check
PYTHON = .venv/bin/python

setup:
	UV_CACHE_DIR=/tmp/private-usage-demo-uv-cache uv sync --locked
dev:
	$(PYTHON) -m usage_demo.cli serve
traffic:
	$(PYTHON) -m usage_demo.cli traffic --requests 1000
capture:
	$(PYTHON) -m usage_demo.cli capture
index:
	$(PYTHON) -m usage_demo.cli index
query:
	$(PYTHON) -m usage_demo.cli query
verify:
	$(PYTHON) -m usage_demo.cli verify
inference-smoke:
	$(PYTHON) -m usage_demo.inference
demo-proof:
	$(PYTHON) -m usage_demo.demo_proof
check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	$(PYTHON) -m pytest -q
