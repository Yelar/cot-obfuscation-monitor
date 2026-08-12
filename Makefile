VENV_PYTHON := .venv/bin/python
PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)
UV ?= uv

.PHONY: help setup setup-data setup-full reproduce verify results test plots prepare-author prepare-ratios train-stage1

help:
	@echo "setup          Sync the locked offline verification, test, and plot environment"
	@echo "setup-data     Add dependencies for reconstructing pinned public datasets"
	@echo "setup-full     Add generation, evaluation, and training dependencies"
	@echo "reproduce      Run the complete credential-free audit, tests, and plot build"
	@echo "verify         Audit release contents and require a current checksum manifest"
	@echo "results        Recompute checks over the sanitized frozen result summaries"
	@echo "test           Run offline tests"
	@echo "plots          Regenerate plots from frozen public summaries"
	@echo "prepare-author Download and verify pinned public training datasets"
	@echo "prepare-ratios Download author datasets and construct the ratio mixtures"
	@echo "train-stage1   Start Stage 1 Tinker training (requires prepared data and API key)"

setup:
	$(UV) sync --extra dev --locked

setup-data:
	$(UV) sync --extra data --extra dev --locked

setup-full:
	$(UV) sync --all-extras --locked

reproduce: verify results test plots

verify:
	$(PYTHON) -m cot_obfuscation_repro.audit --check-manifest

results:
	$(PYTHON) -m cot_obfuscation_repro.summarize --validate-summary \
	  data/results/five_condition_corrected.json \
	  data/results/behavioral_ratio_corrected.json

test:
	$(PYTHON) -m pytest

plots:
	$(PYTHON) -m cot_obfuscation_repro.plots

prepare-author:
	$(PYTHON) -m cot_obfuscation_repro.prepare --download-author

prepare-ratios:
	$(PYTHON) -m cot_obfuscation_repro.prepare --download-author --build-ratios

train-stage1:
	bash scripts/train_stage1.sh
