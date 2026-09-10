"""Central fail-closed authorization check for annotation provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.analysis.build_gendered_argument_visibility_model_benchmark_v1 import sha256

ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION = ROOT / "config/provider_call_authorization_v1.public.example.json"


class ProviderCallNotAuthorized(RuntimeError):
    """Raised before credential access when an operation lacks exact approval."""


def load_authorization() -> dict[str, Any]:
    value = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != "1.0.0":
        raise ProviderCallNotAuthorized("provider authorization file is invalid")
    return value


def require_operation(operation: str, governing_config: Path) -> dict[str, Any]:
    authorization = load_authorization()
    if authorization.get("provider_calls_authorized") is not True:
        raise ProviderCallNotAuthorized(
            f"provider calls are not authorized; denied operation: {operation}"
        )
    if not authorization.get("approved_by") or not authorization.get("approved_at"):
        raise ProviderCallNotAuthorized("authorization is missing approver or UTC timestamp")
    approved = authorization.get("operations", {}).get(operation)
    if not isinstance(approved, dict):
        raise ProviderCallNotAuthorized(f"operation is not individually authorized: {operation}")
    actual_hash = sha256(governing_config)
    if approved.get("config_sha256") != actual_hash:
        raise ProviderCallNotAuthorized(
            f"approved config hash does not match current file for operation: {operation}"
        )
    return {
        "operation": operation,
        "approved_by": authorization["approved_by"],
        "approved_at": authorization["approved_at"],
        "config_sha256": actual_hash,
        "authorization_file_sha256": sha256(AUTHORIZATION),
        "approved_operation": approved,
    }
