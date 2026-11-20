import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from watchtower import ExplorationConfig, ExtractionMethod, explore_portal
from watchtower.service import _default_explorer
from watchtower.urls import UnsafeURL, normalize_url

EXIT_CODES = {"succeeded": 0, "failed": 1, "incomplete": 2, "blocked": 3, "cancelled": 130}
METHOD_LABELS = {
    ExtractionMethod.DOCUMENTED_API: "the documented API",
    ExtractionMethod.DISCOVERED_API: "the discovered public API",
    ExtractionMethod.HTML: "HTML extraction",
    ExtractionMethod.BROWSER: "browser rendering",
    ExtractionMethod.HYBRID: "hybrid extraction",
}


def parser() -> argparse.ArgumentParser:
    arguments = argparse.ArgumentParser(
        description="Explore a public procurement portal with Patterno Watchtower."
    )
    target = arguments.add_mutually_exclusive_group(required=True)
    target.add_argument("url", nargs="?", help="Public procurement portal URL")
    target.add_argument("--resume", metavar="RUN_ID", help="Resume with evidence from a prior run")
    arguments.add_argument(
        "--max-duration", type=float, metavar="SECONDS", help="Override this run's deadline"
    )
    arguments.add_argument(
        "--max-requests", type=int, metavar="COUNT", help="Override this run's HTTP request limit"
    )
    arguments.add_argument(
        "--max-llm-calls", type=int, metavar="COUNT", help="Override this run's OpenAI request limit"
    )
    arguments.add_argument("--db", type=Path, help="Save or load run history in this SQLite database")
    arguments.add_argument("--json", action="store_true", help="Write the complete result to standard output")
    arguments.add_argument("--verbose", action="store_true", help="Show progress and budget usage")
    return arguments


async def run(args, config: ExplorationConfig) -> int:
    if args.db is not None or args.resume:
        async with _default_explorer(args.db) as explorer:
            result = (
                await explorer.resume(args.resume, config)
                if args.resume
                else await explorer.explore(args.url, config)
            )
    else:
        result = await explore_portal(args.url, config)
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        label = METHOD_LABELS.get(result.best_candidate_method)
        if label and result.candidate_verified:
            print(f"Recommended method is {label}.")
            if url := (result.validation or {}).get("listing_url"):
                print(f"Verified listing at {url}")
        elif label:
            print(f"Candidate method is {label}. Verification is pending.")
        else:
            print("Extraction method undetermined.")
        if result.status != "succeeded":
            print(f"{result.stop_reason.value}. {result.recommended_next_step}")
        if result.status == "failed":
            error = next((item["error"] for item in reversed(result.evidence) if item.get("error")), None)
            if error:
                print(error)
        if result.portal_id is not None:
            print(f"Saved run {result.exploration_id}.")
    return EXIT_CODES[result.status]


def main() -> int:
    arguments = parser()
    args = arguments.parse_args()
    try:
        config = ExplorationConfig(
            **{
                name: value
                for name, value in {
                    "max_duration_seconds": args.max_duration,
                    "max_http_requests": args.max_requests,
                    "max_llm_calls": args.max_llm_calls,
                }.items()
                if value is not None
            }
        )
    except ValidationError:
        arguments.error("Budget overrides must be within the supported configuration ranges.")
    if args.url:
        try:
            args.url = normalize_url(args.url)
        except UnsafeURL as exc:
            arguments.error(str(exc))
    load_dotenv()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    try:
        return asyncio.run(run(args, config))
    except KeyboardInterrupt:
        return 130
    except KeyError:
        print("Exploration run was not found in this database.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
