#!/usr/bin/env python3
"""
Pre-push secret scanner for eBay Claw repository.

Run before pushing to GitHub to detect accidentally staged secrets.
Usage: python check_secrets.py
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Patterns for common secrets
SECRET_PATTERNS = {
    "AWS Access Key": r"AKIA[A-Z0-9]{16}",
    "AWS Secret Key": r"aws_secret_access_key\s*=\s*['\"]?[A-Za-z0-9/+=]{40}",
    "OpenAI API Key": r"sk-[a-zA-Z0-9]{48,}",
    "Google API Key": r"AIza[a-zA-Z0-9_-]{35}",
    "Generic API Key": r"api[_-]?key\s*[=:]\s*['\"]?[a-zA-Z0-9]{32,}",
    "Bearer Token": r"bearer\s+[a-zA-Z0-9_\-\.]{20,}",
    "Client Secret": r"client[_-]?secret\s*[=:]\s*['\"]?[a-zA-Z0-9\-]{20,}",
    "eBay Client ID": r"RobertJa-[a-zA-Z0-9\-]+",
    "eBay Cert/Secret": r"PRD-[a-f0-9]{12}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}",
    "Private Key Header": r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    "Generic Token": r"token\s*[=:]\s*['\"]?[a-zA-Z0-9_\-\.]{40,}",
}

# File patterns that should never be committed
FORBIDDEN_FILES = {
    "api.env",
    ".env",
    ".env.local",
}

# File patterns that contain operational state (should not be committed)
OPERATIONAL_FILES = {
    "*.jsonl",
    "*.log",
    ".ebay_claw_sync_state.json",
    ".ebay_claw_review_queue.json",
}

# Files/dirs to skip
SKIP_PATTERNS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".env.example",  # This is intentionally safe
    "check_secrets.py",  # This file (contains pattern examples)
}

# Files that may contain test/mock secrets (intentionally safe)
TEST_SECRET_FILES = {
    "test_secret_redaction.py",  # Tests the redaction logic with fake tokens
}


def should_skip(path: Path) -> bool:
    """Check if file/dir should be skipped."""
    parts = path.parts
    if any(skip in parts for skip in SKIP_PATTERNS):
        return True
    # Skip test files with intentional mock secrets
    if path.name in TEST_SECRET_FILES:
        return True
    return False


def check_file(path: Path) -> List[Tuple[str, int, str]]:
    """
    Check a single file for secrets.

    Returns list of (pattern_name, line_number, line_content) tuples.
    """
    findings = []

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                for pattern_name, pattern in SECRET_PATTERNS.items():
                    if re.search(pattern, line, re.IGNORECASE):
                        findings.append((pattern_name, line_num, line.strip()))
    except Exception as e:
        print(f"[WARNING] Could not read {path}: {e}", file=sys.stderr)

    return findings


def check_forbidden_files(root: Path) -> List[str]:
    """Check for forbidden files that should never be committed."""
    found = []

    for pattern in FORBIDDEN_FILES:
        matches = list(root.rglob(pattern))
        for match in matches:
            if not should_skip(match):
                found.append(str(match.relative_to(root)))

    return found


def check_operational_files(root: Path) -> List[str]:
    """Check for operational/local state files."""
    found = []

    for pattern in OPERATIONAL_FILES:
        matches = list(root.rglob(pattern))
        for match in matches:
            if not should_skip(match) and match.is_file():
                found.append(str(match.relative_to(root)))

    return found


def main():
    """Run secret scanning checks."""
    root = Path(__file__).parent
    print("eBay Claw Secret Scanner")
    print(f"Scanning: {root}")
    print()

    errors = []
    warnings = []

    # Check for forbidden files
    print("Checking for forbidden files...")
    forbidden = check_forbidden_files(root)
    if forbidden:
        errors.append("\n[ERROR] FORBIDDEN FILES FOUND (must not be committed):")
        for f in forbidden:
            errors.append(f"   * {f}")

    # Check for operational files
    print("Checking for operational/state files...")
    operational = check_operational_files(root)
    if operational:
        warnings.append("\n[WARNING] OPERATIONAL FILES FOUND (should not be committed):")
        for f in operational:
            warnings.append(f"   * {f}")

    # Scan tracked files for secret patterns
    print("Scanning files for secret patterns...")

    # Get all Python files and docs
    scan_patterns = ["*.py", "*.md", "*.txt", "*.yml", "*.yaml", "*.sh", "*.ps1"]
    files_to_scan = []

    for pattern in scan_patterns:
        files_to_scan.extend(root.rglob(pattern))

    secret_findings = []

    for file_path in files_to_scan:
        if should_skip(file_path):
            continue

        findings = check_file(file_path)
        if findings:
            secret_findings.append((file_path, findings))

    if secret_findings:
        errors.append("\n[ERROR] POTENTIAL SECRETS DETECTED:")
        for file_path, findings in secret_findings:
            rel_path = file_path.relative_to(root)
            errors.append(f"\n   FILE: {rel_path}")
            for pattern_name, line_num, line_content in findings:
                # Truncate long lines
                if len(line_content) > 80:
                    line_content = line_content[:77] + "..."
                errors.append(f"      Line {line_num}: {pattern_name}")
                errors.append(f"         {line_content}")

    # Print results
    print("\n" + "=" * 70)

    if errors:
        for error in errors:
            print(error)
        print("\n[FAIL] SECRET SCAN FAILED - DO NOT PUSH")
        print("\nActions to take:")
        print("  1. Remove forbidden files (api.env, .env, etc.)")
        print("  2. Remove or redact any detected secrets")
        print("  3. Verify .gitignore is properly configured")
        print("  4. Run: git status")
        print("  5. Run this script again before pushing")
        return 1

    if warnings:
        for warning in warnings:
            print(warning)
        print("\n[WARNING] Warnings found - review before pushing")
        print("   Operational files should generally not be committed.")

    if not errors and not warnings:
        print("[PASS] No secrets or forbidden files detected")
        print("[PASS] Repository appears safe to push")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
