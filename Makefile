.PHONY: install install-dev venv clean lint bench bench-quick test test-runner

# Default Python
PYTHON := python3
VENV_DIR := venv

install:
	pip install --upgrade pip
	pip install .
	pip install pygments

install-dev:
	pip install --upgrade pip
	pip install -e .
	pip install pygments ruff

venv:
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --upgrade pip
	$(VENV_DIR)/bin/pip install -e .
	$(VENV_DIR)/bin/pip install pygments
	@echo ""
	@echo "  Virtualenv created. Activate with:  source $(VENV_DIR)/bin/activate"

lint:
	ruff check loop.py web_search.py memory.py out_of_the_box.py providers.py webui.py mcp_client.py

bench:
	@echo "Running all benchmarks..."
	$(PYTHON) -u -c "from benchmark.agentic_bench import run_all; run_all()"

bench-quick:
	@echo "Running quick benchmark (3 tasks)..."
	$(PYTHON) -u -c "from benchmark.agentic_bench import run_quick; run_quick()"

test-runner:
	@echo "Running agentic bench with runner..."
	$(PYTHON) -u -m benchmark.agentic_bench

# Quick smoke test: verify the agent can import and all modules are healthy
test:
	@echo "Running import tests..."
	$(PYTHON) -c "
import providers
import memory
import mcp_client
print('  ✓ providers — ok')
print('  ✓ memory — ok')
print('  ✓ mcp_client — ok')
print('All imports passed.')
" && echo ""

clean:
	rm -rf $(VENV_DIR)
	rm -rf __pycache__ .ruff_cache .pytest_cache
	rm -rf *.egg-info dist build
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
