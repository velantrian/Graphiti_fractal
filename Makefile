VENV?=.venv
PYTHON?=$(VENV)/bin/python
PIP?=$(VENV)/bin/pip

.PHONY: venv install setup seed quality search context l1 l2 l3 l3-build viz benchmark test migrate web dedupe-entities dedupe-episodes dc-build dc-up dc-down dc-logs

venv:
	python -m venv $(VENV)

install: venv
	$(PIP) install -r requirements.txt

setup:
	$(PYTHON) main.py setup

seed:
	$(PYTHON) main.py seed

quality:
	$(PYTHON) main.py quality

search:
	$(PYTHON) main.py search-demo

context:
	$(PYTHON) main.py context "Fractal Memory"

l1:
	$(PYTHON) main.py l1 --query "Fractal Memory" --hours 24

l2:
	$(PYTHON) main.py l2 "Graphiti"

l3:
	$(PYTHON) main.py l3 "Graphiti"

l3-build:
	$(PYTHON) main.py l3-build "Graphiti"

viz:
	$(PYTHON) main.py viz-export --output static/graph_data.json

benchmark:
	$(PYTHON) main.py benchmark

test:
	$(PYTHON) -m pytest -q

migrate:
	$(PYTHON) main.py migrate

# Cleanup targets are intentionally dry-run only. Use the CLI with --apply explicitly.
dedupe-entities:
	$(PYTHON) main.py dedupe-entities

dedupe-episodes:
	$(PYTHON) main.py dedupe-episodes

web:
	$(PYTHON) -m uvicorn app:app --host 127.0.0.1 --port 8000

dc-build:
	docker compose build

dc-up:
	docker compose up -d

dc-down:
	docker compose down

dc-logs:
	docker compose logs -f
