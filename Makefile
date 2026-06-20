.PHONY: install install-dev venv clean lint

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
	ruff check loop.py web_search.py memory.py out_of_the_box.py

clean:
	rm -rf $(VENV_DIR)
	rm -rf __pycache__ .ruff_cache .pytest_cache
	rm -rf *.egg-info dist build
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
