#!/usr/bin/env python3
"""M2 entry point — run the Bayesian Optimization loop over spec mutations.

Usage:
    python scripts/run_m2.py [spec_path]

Environment variables (all optional with defaults):
    CAMBRIAN_SUPERVISOR_URL     Supervisor base URL (default: http://localhost:8400)
    CAMBRIAN_BO_BUDGET          BO iteration budget (default: 20)
    CAMBRIAN_MINI_CAMPAIGN_N    Mini-campaign size (default: 2)
    CAMBRIAN_CAMPAIGN_LENGTH    Full campaign size (default: 5)
    CAMBRIAN_BO_INITIAL_POINTS  Random initial points before GP kicks in (default: 5)
    CAMBRIAN_START_GENERATION   Starting generation number (default: 1)

If spec_path is omitted, CAMBRIAN_SPEC_PATH env var is used; if that is also
unset the default spec at spec/CAMBRIAN-SPEC-005.md is used.
"""

import asyncio
import os
import sys
from pathlib import Path

# Load .env from project root if present (before any other imports that might read env vars)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())


def _resolve_spec_path(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1])
    env_path = os.environ.get("CAMBRIAN_SPEC_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).parent.parent / "spec" / "CAMBRIAN-SPEC-005.md"


async def main() -> None:
    spec_path = _resolve_spec_path(sys.argv)
    if not spec_path.exists():
        sys.exit(f"Error: spec file not found: {spec_path}")

    # Import here so .env is loaded first
    from supervisor.bo_loop import BOResult, SpecBOLoop

    start_generation = int(os.environ.get("CAMBRIAN_START_GENERATION", "1"))
    supervisor_url = os.environ.get("CAMBRIAN_SUPERVISOR_URL", "http://localhost:8400")

    print("M2 BO loop starting")
    print(f"  Spec:       {spec_path}")
    print(f"  Supervisor: {supervisor_url}")

    loop = SpecBOLoop(
        base_spec_path=spec_path,
        supervisor_url=supervisor_url,
        start_generation=start_generation,
    )

    result: BOResult = await loop.run()

    print("\nBO loop complete")
    print(f"  Iterations:   {result.iterations}")
    print(f"  Budget used:  {result.budget_used} generations")
    print(f"  Best viability: {result.best_viability:.1%}")
    print(f"  Best spec hash: {result.best_spec_hash[:16]}...")

    if result.best_viability > 0:
        out_path = Path("best-spec.md")
        out_path.write_text(result.best_spec_text)
        print(f"  Best spec written to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
