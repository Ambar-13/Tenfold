# TENFOLD — TikTok TechJam 2026, Track 4
# Python standard library only. No pip install step exists or is needed.

KIT_REPO := https://github.com/TechJam2026/techjam-conversational-search
OUT      ?= /tmp/tenfold-official.json
export PYTHONDONTWRITEBYTECODE := 1

.DEFAULT_GOAL := help
.PHONY: help setup eval verify test clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-8s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Start with 'make setup', then 'make eval'."

setup:  ## Clone the organizer kit into kit/ (catalog download is a manual step)
	@test -d kit || git clone --depth 1 $(KIT_REPO) kit
	@echo
	@echo "Now download catalog.jsonl.gz from the participant-kit release of"
	@echo "$(KIT_REPO), place it in kit/data/, and run: gunzip kit/data/catalog.jsonl.gz"
	@test -f kit/data/catalog.jsonl && echo "catalog present: ready for 'make eval'" || true

eval:  ## Run the official evaluation and compare against the stored result
	cd run && python3 -m evaluator.local_evaluator --output $(OUT)
	@cmp $(OUT) results/e-fixed-official.json \
	  && echo "byte-identical to results/e-fixed-official.json" \
	  || (echo "output differs from the stored result"; exit 1)

verify: test eval  ## Everything a reviewer needs: tests, then the scored run

test:  ## Contract, determinism and stored-result consistency checks
	python3 -m unittest discover -s tests -v

clean:  ## Remove generated output (never touches kit/ or results/)
	rm -f $(OUT)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
