#!/usr/bin/env python3
"""
Keychain Secrets Utility

Retrieves API keys and secrets from macOS Keychain with fallback to environment variables.
This is the standard way to access secrets across the ~/Developer workspace.

Usage:
    from keychain_secrets import get_secret

    api_key = get_secret("AIRTABLE_API_KEY")

Service naming convention:
    Secrets are stored with service name: developer.workspace.<KEY_NAME>
    e.g., developer.workspace.AIRTABLE_API_KEY

Adding a new secret to Keychain:
    security add-generic-password -a "$USER" -s "developer.workspace.MY_KEY" -w "secret-value" -U

Updating an existing secret:
    security add-generic-password -a "$USER" -s "developer.workspace.MY_KEY" -w "new-value" -U
    (The -U flag updates if exists)

Deleting a secret:
    security delete-generic-password -a "$USER" -s "developer.workspace.MY_KEY"
"""

import os
import subprocess
import sys
from functools import lru_cache
from typing import Optional

# Service name prefix for all workspace secrets
SERVICE_PREFIX = "developer.workspace"


def _is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def _get_from_keychain(key_name: str) -> Optional[str]:
    """
    Retrieve a secret from macOS Keychain.

    Searches both iCloud Keychain and login keychain, so secrets can sync
    across machines if added to iCloud Keychain via Keychain Access.app.

    Args:
        key_name: The key name (e.g., "AIRTABLE_API_KEY")

    Returns:
        The secret value, or None if not found or not on macOS.
    """
    if not _is_macos():
        return None

    service_name = f"{SERVICE_PREFIX}.{key_name}"
    user = os.environ.get("USER", "")

    # Search all keychains (includes iCloud Keychain if enabled)
    # The default search order is: login, System, and any iCloud keychains
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a", user,
                "-s", service_name,
                "-w",  # Output only the password
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            return result.stdout.strip()
        return None

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


@lru_cache(maxsize=32)
def get_secret(key_name: str, required: bool = False) -> Optional[str]:
    """
    Get a secret from Keychain, falling back to environment variable.

    Lookup order:
    1. macOS Keychain (service: developer.workspace.<key_name>)
    2. Environment variable with same name

    Args:
        key_name: The secret key name (e.g., "AIRTABLE_API_KEY")
        required: If True, raise ValueError if secret not found

    Returns:
        The secret value, or None if not found and not required.

    Raises:
        ValueError: If required=True and secret not found in Keychain or env.

    Example:
        >>> api_key = get_secret("AIRTABLE_API_KEY", required=True)
        >>> token = get_secret("OPTIONAL_TOKEN")  # Returns None if not found
    """
    # Try Keychain first
    value = _get_from_keychain(key_name)

    # Fall back to environment variable
    if value is None:
        value = os.environ.get(key_name)

    if required and value is None:
        service_name = f"{SERVICE_PREFIX}.{key_name}"
        raise ValueError(
            f"Secret '{key_name}' not found.\n"
            f"Add to Keychain: security add-generic-password -a \"$USER\" -s \"{service_name}\" -w \"your-secret\" -U\n"
            f"Or set environment variable: export {key_name}=\"your-secret\""
        )

    return value


def add_secret(key_name: str, value: str) -> bool:
    """
    Add or update a secret in macOS Keychain.

    Args:
        key_name: The key name (e.g., "AIRTABLE_API_KEY")
        value: The secret value

    Returns:
        True if successful, False otherwise.
    """
    if not _is_macos():
        print("Error: Keychain is only available on macOS", file=sys.stderr)
        return False

    service_name = f"{SERVICE_PREFIX}.{key_name}"

    try:
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-a", os.environ.get("USER", ""),
                "-s", service_name,
                "-w", value,
                "-U",  # Update if exists
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            # Clear cache so new value is retrieved
            get_secret.cache_clear()
            return True

        print(f"Error adding secret: {result.stderr}", file=sys.stderr)
        return False

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"Error adding secret: {e}", file=sys.stderr)
        return False


def delete_secret(key_name: str) -> bool:
    """
    Delete a secret from macOS Keychain.

    Args:
        key_name: The key name to delete

    Returns:
        True if successful or didn't exist, False on error.
    """
    if not _is_macos():
        print("Error: Keychain is only available on macOS", file=sys.stderr)
        return False

    service_name = f"{SERVICE_PREFIX}.{key_name}"

    try:
        result = subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-a", os.environ.get("USER", ""),
                "-s", service_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        # Clear cache
        get_secret.cache_clear()

        # Return code 44 means item not found, which is fine
        return result.returncode in (0, 44)

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"Error deleting secret: {e}", file=sys.stderr)
        return False


def list_workspace_secrets() -> list[str]:
    """
    List all secrets in Keychain with the developer.workspace prefix.

    Returns:
        List of key names (without the prefix).
    """
    if not _is_macos():
        return []

    try:
        # Dump all generic passwords and filter
        result = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        secrets = []
        for line in result.stdout.split("\n"):
            if f'svce"<blob>="{SERVICE_PREFIX}.' in line:
                # Extract key name from: "svce"<blob>="developer.workspace.KEY_NAME"
                start = line.find(f'{SERVICE_PREFIX}.') + len(SERVICE_PREFIX) + 1
                end = line.find('"', start)
                if start > len(SERVICE_PREFIX) and end > start:
                    secrets.append(line[start:end])

        return sorted(set(secrets))

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


# CLI interface
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Manage workspace secrets in macOS Keychain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s get AIRTABLE_API_KEY
  %(prog)s add AIRTABLE_API_KEY "pat123..."
  %(prog)s delete OLD_KEY
  %(prog)s list
        """
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # get command
    get_parser = subparsers.add_parser("get", help="Get a secret")
    get_parser.add_argument("key", help="Secret key name")

    # add command
    add_parser = subparsers.add_parser("add", help="Add or update a secret")
    add_parser.add_argument("key", help="Secret key name")
    add_parser.add_argument("value", help="Secret value")

    # delete command
    del_parser = subparsers.add_parser("delete", help="Delete a secret")
    del_parser.add_argument("key", help="Secret key name")

    # list command
    subparsers.add_parser("list", help="List all workspace secrets")

    args = parser.parse_args()

    if args.command == "get":
        value = get_secret(args.key)
        if value:
            print(value)
        else:
            print(f"Secret '{args.key}' not found", file=sys.stderr)
            sys.exit(1)

    elif args.command == "add":
        if add_secret(args.key, args.value):
            print(f"Added secret: {args.key}")
        else:
            sys.exit(1)

    elif args.command == "delete":
        if delete_secret(args.key):
            print(f"Deleted secret: {args.key}")
        else:
            sys.exit(1)

    elif args.command == "list":
        secrets = list_workspace_secrets()
        if secrets:
            for s in secrets:
                print(s)
        else:
            print("No workspace secrets found")
