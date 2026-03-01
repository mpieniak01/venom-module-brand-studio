PYTHON ?= python3
PYTHONPATH ?= ../..
PYTEST ?= PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest

.PHONY: pr-fast check-new-code-coverage lint test

pr-fast: lint test

check-new-code-coverage:
	$(PYTEST) -q --cov=venom_module_brand_studio --cov-report=term-missing:skip-covered --cov-report=xml --cov-fail-under=80

lint:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ruff check .

test:
	$(PYTEST) -q
