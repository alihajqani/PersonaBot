# ===== IMPORTS & DEPENDENCIES =====
import os
import json
import math
import argparse
import logging
from typing import Dict, Any, Optional

# ===== CONFIGURATION & CONSTANTS =====
# The minimum number of entries (key-value pairs) a JSON answer file must have
# to be kept. This is computed DYNAMICALLY from the live survey schema so the
# cleaner never deletes valid answer files just because the questionnaire got
# shorter. A file is kept if it answers at least this fraction of the schema's
# questions — the same coverage gate answer_generator.py uses to save a file.
COVERAGE_FRACTION = 0.80

# The schema file is resolved relative to this script (../output/form_schema.json)
# so cleaner.py works when run from any working directory without importing the
# whole config (which would require BASE_FORM_URL etc. to be set).
_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "output", "form_schema.json"
)


def compute_min_entries(schema_path: str = _SCHEMA_PATH) -> Optional[int]:
    """Return the minimum entry count for a valid answer file, derived from the
    number of questions in the schema. Returns None if the schema can't be read
    (so the caller can refuse to delete anything rather than guess)."""
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Could not read schema at '{schema_path}' to compute the "
                      f"cleanup threshold: {e}")
        return None
    if not isinstance(schema, list):
        logging.error(f"Schema at '{schema_path}' is not a list of questions.")
        return None
    n = len(schema)
    if n <= 0:
        logging.error(f"Schema at '{schema_path}' has no questions.")
        return None
    return max(1, math.ceil(n * COVERAGE_FRACTION))


# --- Setup Application-wide Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# ===== CORE CLEANUP LOGIC =====

def clean_json_directory(directory_path: str, min_entries: Optional[int] = None):
    """
    Scans a directory for JSON files and deletes any that have fewer entries
    than the minimum. The minimum is computed from the schema question count
    (80% coverage) unless `min_entries` is supplied explicitly.

    Args:
        directory_path: The path to the directory containing the JSON files.
        min_entries:    Override threshold. If None, derived from the schema.
    """
    if min_entries is None:
        min_entries = compute_min_entries()
        if min_entries is None:
            logging.error("Cannot determine a safe cleanup threshold from the schema. "
                          "Aborting WITHOUT deleting anything (to avoid wiping valid files).")
            return

    logging.info(f"Starting cleanup process for directory: '{directory_path}'")
    logging.info(f"Files with fewer than {min_entries} entries will be deleted.")

    if not os.path.isdir(directory_path):
        logging.error(f"Error: Directory not found at {directory_path}. Aborting.")
        return

    # Counters for the final summary
    files_scanned = 0
    files_deleted = 0
    files_kept = 0

    # Iterate over all files in the given directory
    for filename in os.listdir(directory_path):
        # Process only files ending with .json
        if filename.lower().endswith('.json'):
            files_scanned += 1
            file_path = os.path.join(directory_path, filename)

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data: Dict[str, Any] = json.load(f)

                # Ensure the loaded data is a dictionary before counting keys
                if not isinstance(data, dict):
                    logging.warning(f"Skipping '{filename}': Content is not a valid JSON object (e.g., it's a list).")
                    continue

                num_entries = len(data)

                if num_entries < min_entries:
                    logging.warning(f"Found {num_entries} entries in '{filename}'. DELETING file.")
                    try:
                        os.remove(file_path)
                        logging.info(f"Successfully deleted '{filename}'.")
                        files_deleted += 1
                    except OSError as e:
                        logging.error(f"Failed to delete '{filename}': {e}")
                else:
                    logging.info(f"Found {num_entries} entries in '{filename}'. Keeping file.")
                    files_kept += 1

            except json.JSONDecodeError:
                logging.error(f"Skipping invalid JSON file: '{filename}'. It might be corrupted.")
            except Exception as e:
                logging.error(f"An unexpected error occurred while processing '{filename}': {e}")

    logging.info("===== Cleanup Summary =====")
    logging.info(f"Total JSON files scanned: {files_scanned}")
    logging.info(f"Files kept (>= {min_entries} entries): {files_kept}")
    logging.info(f"Files deleted (< {min_entries} entries): {files_deleted}")
    logging.info("===========================")


# ===== INITIALIZATION & STARTUP =====
if __name__ == "__main__":
    """
    Entry point for the script. Parses command-line arguments and starts the cleanup process.
    """
    threshold = compute_min_entries()
    threshold_desc = (f"{threshold} (80% of the schema question count)"
                      if threshold is not None else "UNKNOWN — schema not found")

    parser = argparse.ArgumentParser(
        description=f"Clean a directory by deleting JSON answer files with fewer than "
                    f"{threshold_desc} entries.",
        epilog="*** WARNING: This script permanently deletes files. Please back up your data first! ***"
    )

    parser.add_argument(
        "directory",
        type=str,
        help="The path to the directory containing JSON files to be cleaned (e.g., 'output/answers')."
    )

    args = parser.parse_args()

    if threshold is None:
        logging.error("Refusing to run: could not compute a threshold from the schema. "
                      "Run schema extraction first so output/form_schema.json exists.")
        raise SystemExit(1)

    # Ask for user confirmation before proceeding with deletion
    try:
        confirm = input(
            f"You are about to delete files from '{args.directory}'.\n"
            f"Files with fewer than {threshold} entries will be removed.\n"
            f"This action CANNOT be undone. Are you sure you want to continue? (yes/no): "
        )
        if confirm.lower() == 'yes':
            clean_json_directory(args.directory, min_entries=threshold)
        else:
            logging.info("Cleanup cancelled by user.")
    except KeyboardInterrupt:
        logging.info("\nProcess interrupted by user. Exiting.")