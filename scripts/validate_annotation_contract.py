#!/usr/bin/env python3
"""Cross-check the frozen codebook, schema, prompt, and runtime contract."""

from __future__ import annotations

import json

from src.annotation.contract_v0_3 import validate_contract_files


def main() -> None:
    contract = validate_contract_files()
    print(
        json.dumps(
            {
                "status": "valid",
                "version": contract["codebook"]["version"],
                "fields": len(contract["schema"]["properties"]),
                "rendered_prompt_characters": len(contract["rendered_prompt"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
