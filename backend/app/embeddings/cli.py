"""CLI: ``python -m app.embeddings.cli`` — offline eval fixtures (optional).

Never required for the web path. Never writes to ``DATASET_ROOT`` or
``PROJECT_DB_PATH``. Similarity is affinity only — ``musical_quality_claim: false``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.embeddings.features import embed_composition_scope, symbolic_features_v1_dims
from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_PROFILE_ID,
)
from app.embeddings.vector import cosine_similarity, feature_vector_digest
from app.ready import configure_logging


logger = logging.getLogger(__name__)

_DEFAULT_FIXTURES = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "embeddings"
)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.embeddings.cli",
        description=(
            "Symbolic composition embeddings (handcrafted). "
            "Eval helpers only — not a training loop."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    eval_p = sub.add_parser(
        "eval-examples",
        help="Rank fixture nearest neighbors under symbolic.features.v1",
    )
    eval_p.add_argument(
        "--fixtures-dir",
        type=Path,
        default=_DEFAULT_FIXTURES,
        help="Directory with similar_rhythm_a/b and dense_texture_c JSON",
    )
    eval_p.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON to stdout",
    )

    info_p = sub.add_parser("info", help="Print profile / model constants")
    info_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "info":
        return _cmd_info(args)
    if args.command == "eval-examples":
        return _cmd_eval_examples(args)
    parser.error(f"Unknown command {args.command}")
    return 2


def _cmd_info(args: argparse.Namespace) -> int:
    payload = {
        "profile_id": EMBEDDING_PROFILE_ID,
        "algorithm_version": EMBEDDING_ALGORITHM_VERSION,
        "default_model_id": EMBEDDING_DEFAULT_MODEL_ID,
        "dims": symbolic_features_v1_dims(),
        "musical_quality_claim": False,
        "artist_as_style_id": False,
    }
    logger.info(
        "Embedding profile info",
        extra={
            "profile_id": payload["profile_id"],
            "dims": payload["dims"],
            "default_model_id": payload["default_model_id"],
        },
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"profile={payload['profile_id']} dims={payload['dims']} "
            f"model={payload['default_model_id']} musical_quality_claim=false"
        )
    return 0


def _cmd_eval_examples(args: argparse.Namespace) -> int:
    fixtures_dir: Path = args.fixtures_dir
    names = ("similar_rhythm_a.json", "similar_rhythm_b.json", "dense_texture_c.json")
    missing = [name for name in names if not (fixtures_dir / name).is_file()]
    if missing:
        logger.error(
            "Eval fixtures missing",
            extra={"fixtures_dir_name": fixtures_dir.name, "missing": missing},
        )
        print(f"Missing fixtures: {missing}", file=sys.stderr)
        return 1

    cards = {}
    for name in names:
        composition = CompositionV2.model_validate(
            json.loads((fixtures_dir / name).read_text(encoding="utf-8"))
        )
        cards[name] = embed_composition_scope(composition)

    query = cards["similar_rhythm_a.json"]
    ranked = sorted(
        (
            (
                name,
                cosine_similarity(query.vector, card.vector),
                feature_vector_digest(card.vector),
            )
            for name, card in cards.items()
            if name != "similar_rhythm_a.json"
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    expected_order = ["similar_rhythm_b.json", "dense_texture_c.json"]
    actual_order = [name for name, _, _ in ranked]
    ok = actual_order == expected_order

    summary = {
        "musical_quality_claim": False,
        "profile_id": EMBEDDING_PROFILE_ID,
        "query": "similar_rhythm_a.json",
        "query_digest": feature_vector_digest(query.vector),
        "ranked": [
            {"fixture": name, "cosine_similarity": round(score, 6), "digest": digest}
            for name, score, digest in ranked
        ],
        "expected_order": expected_order,
        "passed": ok,
    }
    logger.info(
        "Eval examples ranked",
        extra={
            "profile_id": EMBEDDING_PROFILE_ID,
            "passed": ok,
            "top_fixture": actual_order[0] if actual_order else None,
            "hit_count": len(ranked),
        },
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("musical_quality_claim: false")
        print(f"query digest: {summary['query_digest']}")
        for item in summary["ranked"]:
            print(f"  {item['fixture']}: cosine={item['cosine_similarity']}")
        print(f"expected nearest: {expected_order[0]}")
        print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
