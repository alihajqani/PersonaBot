# Makefile for PersonaBot Project Automation

# ==============================================================================
# ✨ Configuration Variables
# These can be overridden from the command line, e.g., make run PROVIDER=google_forms
# ==============================================================================
PYTHON_INTERPRETER ?= python3
PROVIDER           ?= avalform
PHASES             ?= 1,2,3,4
NUM_PERSONAS       ?= 5
RUN_COUNT          ?= 10
DELAY_SECONDS      ?= 120

# ==============================================================================
# 🎯 Core Targets
# ==============================================================================

.PHONY: help install clean run-all schema persona answer submit loop

help:
	@echo "PersonaBot Makefile - Available Commands:"
	@echo "-------------------------------------------"
	@echo "  make install         -> Install all project dependencies from requirements.txt."
	@echo "  make clean           -> Remove all generated output files and __pycache__."
	@echo ""
	@echo "  --- Workflow Commands (customizable) ---"
	@echo "  make run-all         -> Run all phases (1-4) for the specified PROVIDER."
	@echo "                         Example: make run-all PROVIDER=google_forms NUM_PERSONAS=20"
	@echo "  make schema          -> Run only Phase 1: Schema Extraction."
	@echo "                         Example: make schema PROVIDER=google_forms"
	@echo "  make persona         -> Run only Phase 2: Persona Generation."
	@echo "                         Example: make persona NUM_PERSONAS=50"
	@echo "  make answer          -> Run only Phase 3: Answer Generation."
	@echo "  make submit          -> Run only Phase 4: Form Submission."
	@echo ""
	@echo "  --- Advanced Looping ---"
	@echo "  make loop            -> Run a specific phase in a loop with a delay."
	@echo "                         Default: Runs phase 3, 10 times, with 120s delay."
	@echo "                         Example: make loop PHASES=2,3 RUN_COUNT=20 DELAY_SECONDS=60"
	@echo "-------------------------------------------"

# ==============================================================================
# 🛠️ Task Implementations
# ==============================================================================

install:
	@echo "Installing project dependencies from requirements.txt..."
	$(PYTHON_INTERPRETER) -m pip install -r requirements.txt
	@echo "Installing Playwright browsers..."
	playwright install
	@echo "Installation complete."

clean:
	@echo "Cleaning up generated files and caches..."
	rm -rf output/
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	@echo "Cleanup complete."

run-all:
	@echo "Running all phases [$(PHASES)] for provider [$(PROVIDER)] with [$(NUM_PERSONAS)] personas..."
	$(PYTHON_INTERPRETER) main.py $(PROVIDER) --phases=$(PHASES) --num-personas=$(NUM_PERSONAS)

schema:
	@echo "Running Phase 1 (Schema Extraction) for provider [$(PROVIDER)]..."
	$(PYTHON_INTERPRETER) main.py $(PROVIDER) --phases=1

persona:
	@echo "Running Phase 2 (Persona Generation) for provider [$(PROVIDER)] with [$(NUM_PERSONAS)] personas..."
	$(PYTHON_INTERPRETER) main.py $(PROVIDER) --phases=2 --num-personas=$(NUM_PERSONAS)

answer:
	@echo "Running Phase 3 (Answer Generation) for provider [$(PROVIDER)]..."
	$(PYTHON_INTERPRETER) main.py $(PROVIDER) --phases=3

submit:
	@echo "Running Phase 4 (Form Submission) for provider [$(PROVIDER)]..."
	$(PYTHON_INTERPRETER) main.py $(PROVIDER) --phases=4

# --- Loop Implementation ---
# This is a shell loop embedded within a Makefile target.
# It uses .SHELLFLAGS to allow multiline shell commands.
.SHELLFLAGS := -c
loop:
	@echo "Starting loop: Running phases [$(PHASES)] for [$(RUN_COUNT)] times with a [$(DELAY_SECONDS)s] delay..."
	@for i in `seq 1 $(RUN_COUNT)`; do \
		echo ""; \
		echo "========================================="; \
		echo "--- Starting Run $$i of $(RUN_COUNT) ---"; \
		echo "========================================="; \
		$(PYTHON_INTERPRETER) main.py $(PROVIDER) --phases=$(PHASES) --num-personas=$(NUM_PERSONAS); \
		if [ $$i -lt $(RUN_COUNT) ]; then \
			echo ""; \
			echo "--- Run $$i Finished. Waiting for $(DELAY_SECONDS) seconds... ---"; \
			sleep $(DELAY_SECONDS); \
		fi; \
	done
	@echo ""; \
	echo "========================================="; \
	echo "--- All $(RUN_COUNT) runs completed. ---"; \
	echo "=========================================";
