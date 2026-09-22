"""Retry pending QA Memory publications without generating or accepting new replies."""

import argparse
import asyncio
import json
from dataclasses import asdict

from src.bootstrap import save_accepted_qna
from src.repositories.reply_sessions import CosmosReplySessionRepository
from src.services.reply_sessions import ReplySessionService
from src.services.reply_acceptance import recover_pending_acceptances


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="Maximum pending sessions to process")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    repository = CosmosReplySessionRepository()
    result = asyncio.run(recover_pending_acceptances(
        repository, ReplySessionService(repository), save_accepted_qna, limit=args.limit,
    ))
    print(json.dumps(asdict(result)))
    if result.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
