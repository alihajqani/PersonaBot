# core/services.py

# ===== IMPORTS & DEPENDENCIES =====
import os
import logging
import threading
from typing import List

# ===== API KEY MANAGEMENT =====

class APIKeyManager:
    """
    A thread-safe manager for rotating and handling a pool of API keys.
    It loads keys from environment variables following a specific pattern (e.g., PREFIX_1, PREFIX_2).
    """
    def __init__(self, env_prefix: str = "GOOGLE_API_KEY"):
        """
        Initializes the key manager.
        Args:
            env_prefix: The prefix for environment variables to load keys from.
        """
        self.keys: List[str] = self._load_keys_from_env(env_prefix)
        if not self.keys:
            # This is a critical failure. If no keys are found, we must raise an error.
            raise ValueError(f"No API keys found in environment variables with prefix '{env_prefix}'.")
        
        self.current_index = 0
        self._lock = threading.Lock()  # Ensures that key rotation is thread-safe
        logging.info(f"APIKeyManager initialized successfully with {len(self.keys)} keys for prefix '{env_prefix}'.")

    def _load_keys_from_env(self, prefix: str) -> List[str]:
        """Loads keys from environment variables like GOOGLE_API_KEY_1, GOOGLE_API_KEY_2, etc."""
        loaded_keys = []
        i = 1
        while True:
            key = os.getenv(f"{prefix}_{i}")
            if key:
                loaded_keys.append(key)
                i += 1
            else:
                # Stop when the first key in the sequence is not found
                break
        return loaded_keys

    def get_next_key(self) -> str:
        """
        Atomically gets the next key from the pool in a round-robin fashion.
        This method is thread-safe, making it suitable for concurrent operations.
        """
        with self._lock:
            key = self.keys[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.keys)
            # We log only the last 4 characters for security reasons.
            logging.debug(f"Providing API key ending with '...{key[-4:]}'. Next index: {self.current_index}")
            return key

    def get_key_count(self) -> int:
        """Returns the number of active API keys."""
        return len(self.keys)