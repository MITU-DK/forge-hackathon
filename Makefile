.PHONY: setup dev api ui test seed build demo-check check-live eval eval-live clean

PY ?= python3
PLACE ?= Nashik, Maharashtra
CROP ?= Onion
BASE ?= http://localhost:8000
VENV = backend/.venv
BIN = $(VENV)/bin

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -r backend/requirements.txt
	cd frontend && npm install
	@test -f .env || cp .env.example .env
	@echo "Setup done. Put LLM_API_KEY in .env (optional: runs offline without it), then: make dev"

api:
	cd backend && ../$(BIN)/uvicorn app.main:app --reload --port 8000

ui:
	cd frontend && npm run dev

dev:
	@$(MAKE) -j2 api ui

test:
	cd backend && ../$(BIN)/python -m pytest -q

seed:
	cd backend && ../$(BIN)/python -m scripts.seed

build:
	cd frontend && npm run build

check-live:
	cd backend && ../$(BIN)/python -m scripts.check_live "$(PLACE)" "$(CROP)"

eval:  # accuracy checks that need no LLM quota: places, crops, tool output vs source, sell/hold rule, router, guardrails
	cd backend && ../$(BIN)/python -m scripts.evaluate offline

eval-live:  # 30 real questions through a running backend (about 2,500 LLM tokens each; paced 20 s apart)
	cd backend && ../$(BIN)/python -m scripts.evaluate live --base $(BASE)

demo-check:
	@curl -sf -m 90 $(BASE)/api/health >/dev/null && echo "PASS health" || (echo "FAIL health: is the API running at $(BASE)?"; exit 1)
	@curl -sf -m 90 -X POST $(BASE)/api/chat -H 'content-type: application/json' -d '{"message":"Should I sell my onions now?","profile":{"place":"Nashik, Maharashtra","crop":"Onion"}}' | python3 -c "import sys,json; d=json.load(sys.stdin); ok=d.get('answer') and not d.get('degraded'); print('PASS chat' if ok else 'FAIL chat: degraded or empty answer'); sys.exit(0 if ok else 1)"
	@curl -sf -m 90 "$(BASE)/api/live-check" | python3 -c "import sys,json; d=json.load(sys.stdin); bad=[k for k,v in d.items() if not v['ok']]; print('PASS live data' if not bad else 'FAIL live data: '+', '.join(bad)); sys.exit(1 if bad else 0)"

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist
