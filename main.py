# main.py

# ===== IMPORTS & DEPENDENCIES =====
import argparse
import asyncio
import logging
import importlib.util
import sys
from typing import Optional, Any

# Import core logic modules
from core import persona_generator, answer_generator

# Import utility functions and initialize config
import utils
import config

# ===== DYNAMIC PROVIDER LOADER =====

class ProviderManager:
    """Dynamically loads and manages provider-specific modules."""
    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.schema_extractor: Optional[Any] = None
        self.form_submitter: Optional[Any] = None
        self._load_modules()

    def _load_module(self, module_name: str) -> Optional[Any]:
        """Loads a specific module from the provider's directory."""
        module_path = f"providers.{self.provider_name}.{module_name}"
        try:
            # Check if the module can be found without actually importing it yet
            spec = importlib.util.find_spec(module_path)
            if spec is None:
                logging.error(f"Provider module not found at '{module_path}'.")
                return None
            # If found, import it
            module = importlib.import_module(module_path)
            logging.info(f"Successfully loaded module: {module_path}")
            return module
        except ImportError as e:
            logging.error(f"Failed to import module '{module_path}'. Error: {e}")
            return None

    def _load_modules(self):
        """Loads all necessary modules for the selected provider."""
        logging.info(f"Attempting to load provider: '{self.provider_name}'")
        self.schema_extractor = self._load_module("schema_extractor")
        self.form_submitter = self._load_module("form_submitter")

    def is_valid(self) -> bool:
        """Checks if all required modules for the provider were loaded successfully."""
        return self.schema_extractor is not None and self.form_submitter is not None

# ===== MAIN ORCHESTRATOR LOGIC =====

async def main():
    """Parses command-line arguments and orchestrates the form-filling phases."""
    parser = argparse.ArgumentParser(
        description="AI-Powered Form Automation Framework",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Provider selection is now mandatory
    parser.add_argument(
        'provider',
        type=str,
        help="The name of the provider to use (e.g., 'avalform'). Must match a directory in 'providers/'."
    )
    
    parser.add_argument(
        '--phases',
        type=str,
        default="1,2,3,4",
        help="Comma-separated list of phases to run.\n"
             "1: Schema Extraction\n"
             "2: Persona Generation\n"
             "3: Answer Generation\n"
             "4: Form Submission\n"
             "Example: --phases 1,2"
    )
    
    parser.add_argument(
        '--num-personas',
        type=int,
        default=5,
        help="Number of personas to generate in Phase 2. Default is 5."
    )

    args = parser.parse_args()

    # --- Setup and Provider Loading ---
    utils.setup_directories()
    
    provider_manager = ProviderManager(args.provider)
    if not provider_manager.is_valid():
        logging.error(f"Provider '{args.provider}' is not configured correctly or is missing required files.")
        logging.error("Please ensure 'providers/{args.provider}/' contains 'schema_extractor.py' and 'form_submitter.py'.")
        sys.exit(1) # Exit with an error code

    # --- Phase Execution ---
    phases_to_run = [int(p.strip()) for p in args.phases.split(',')]

    if 1 in phases_to_run:
        await provider_manager.schema_extractor.run()
    
    if 2 in phases_to_run:
        await persona_generator.run(num_personas=args.num_personas)
        
    if 3 in phases_to_run:
        await answer_generator.run()

    if 4 in phases_to_run:
        await provider_manager.form_submitter.run()

    logging.info("All selected phases have been executed.")

if __name__ == "__main__":
    # The config is initialized as soon as the config.py module is imported at the top.
    # The application entry point remains the same.
    asyncio.run(main())