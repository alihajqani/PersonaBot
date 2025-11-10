# ===== IMPORTS & DEPENDENCIES =====
import os
import json
import argparse
import logging
from typing import Dict, Any

# ===== CONFIGURATION & CONSTANTS =====
# The minimum number of entries (key-value pairs) a JSON file must have to be kept.
# As per your request, we are checking for 169 entries.
MIN_ENTRIES_THRESHOLD = 169

# --- Setup Application-wide Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# ===== CORE CLEANUP LOGIC =====

def clean_json_directory(directory_path: str):
    """
    Scans a directory for JSON files and deletes any that have fewer entries
    than the specified MIN_ENTRIES_THRESHOLD.

    Args:
        directory_path (str): The path to the directory containing the JSON files.
    """
    logging.info(f"Starting cleanup process for directory: '{directory_path}'")
    logging.info(f"Files with fewer than {MIN_ENTRIES_THRESHOLD} entries will be deleted.")
    
    if not os.path.isdir(directory_path):
        logging.error(f"Error: Directory not found at '{directory_path}'. Aborting.")
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

                if num_entries < MIN_ENTRIES_THRESHOLD:
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
    logging.info(f"Files kept (>= {MIN_ENTRIES_THRESHOLD} entries): {files_kept}")
    logging.info(f"Files deleted (< {MIN_ENTRIES_THRESHOLD} entries): {files_deleted}")
    logging.info("===========================")


# ===== INITIALIZATION & STARTUP =====
if __name__ == "__main__":
    """
    Entry point for the script. Parses command-line arguments and starts the cleanup process.
    """
    parser = argparse.ArgumentParser(
        description=f"Clean a directory by deleting JSON files with fewer than {MIN_ENTRIES_THRESHOLD} entries.",
        epilog="*** WARNING: This script permanently deletes files. Please back up your data first! ***"
    )
    
    parser.add_argument(
        "directory",
        type=str,
        help="The path to the directory containing JSON files to be cleaned (e.g., 'output/answers')."
    )

    args = parser.parse_args()
    
    # Ask for user confirmation before proceeding with deletion
    try:
        confirm = input(
            f"You are about to delete files from '{args.directory}'.\n"
            f"This action CANNOT be undone. Are you sure you want to continue? (yes/no): "
        )
        if confirm.lower() == 'yes':
            clean_json_directory(args.directory)
        else:
            logging.info("Cleanup cancelled by user.")
    except KeyboardInterrupt:
        logging.info("\nProcess interrupted by user. Exiting.")