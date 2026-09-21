"""Human-readable token sequence visualization (text / lightweight HTML)."""

from __future__ import annotations

import html
import logging
from pathlib import Path

from app.tokenizer import special_tokens as st
from app.tokenizer.errors import TokenizerIOError
from app.tokenizer.schemas import TokenizerSequenceV1
from app.tokenizer.vocab import Vocab, build_vocab
from app.tokenizer.schemas import default_tokenizer_config


logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 4000


def render_sequence_text(
    sequence: TokenizerSequenceV1,
    vocab: Vocab | None = None,
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> str:
    strs = _token_strings(sequence, vocab)
    truncated = False
    if len(strs) > max_tokens:
        strs = strs[:max_tokens]
        truncated = True

    lines: list[str] = []
    current: list[str] = []
    for tok in strs:
        if tok == st.BAR and current:
            lines.append(" ".join(current))
            current = [tok]
        else:
            current.append(tok)
    if current:
        lines.append(" ".join(current))
    body = "\n".join(lines)
    if truncated:
        notice = f"\n# truncated after {max_tokens} tokens\n"
        logger.debug("Viz text truncated", extra={"max_tokens": max_tokens})
        body += notice
    return body


def render_sequence_html(
    sequence: TokenizerSequenceV1,
    vocab: Vocab | None = None,
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> str:
    strs = _token_strings(sequence, vocab)
    truncated = False
    if len(strs) > max_tokens:
        strs = strs[:max_tokens]
        truncated = True

    rows: list[str] = []
    bar = -1
    pos = ""
    track = ""
    pitch = vel = dur = ""
    for tok in strs:
        if tok == st.BAR:
            bar += 1
            continue
        if tok.startswith(st.POS_PREFIX):
            pos = tok
            continue
        if tok.startswith(st.TRACK_SLOT_PREFIX):
            track = tok
            continue
        if tok.startswith(st.PITCH_PREFIX):
            pitch = tok
            continue
        if tok.startswith(st.VEL_PREFIX):
            vel = tok
            continue
        if tok.startswith(st.DUR_PREFIX):
            dur = tok
            rows.append(
                "<tr>"
                f"<td>{bar}</td><td>{html.escape(pos)}</td>"
                f"<td>{html.escape(track)}</td><td>{html.escape(pitch)}</td>"
                f"<td>{html.escape(vel)}</td><td>{html.escape(dur)}</td>"
                "</tr>"
            )
            pitch = vel = dur = ""

    notice = (
        f"<p><em>truncated after {max_tokens} tokens</em></p>" if truncated else ""
    )
    table = (
        "<table border='1' cellpadding='4' cellspacing='0'>"
        "<thead><tr><th>bar</th><th>pos</th><th>track</th>"
        "<th>pitch</th><th>vel</th><th>dur</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>tokenizer sequence</title></head><body>"
        f"<h1>tokenizer sequence ({len(sequence.token_ids)} tokens)</h1>"
        f"{notice}{table}</body></html>"
    )


def write_visualization(
    sequence: TokenizerSequenceV1,
    out_path: Path,
    vocab: Vocab | None = None,
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> None:
    out_path = Path(out_path)
    suffix = out_path.suffix.lower()
    if suffix in {".html", ".htm"}:
        content = render_sequence_html(sequence, vocab, max_tokens=max_tokens)
    else:
        content = render_sequence_text(sequence, vocab, max_tokens=max_tokens)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise TokenizerIOError(
            "viz_write_failed",
            f"failed to write visualization {out_path.name}",
            details={"basename": out_path.name},
        ) from exc
    logger.info(
        "Token sequence visualization written",
        extra={
            "basename": out_path.name,
            "token_count": len(sequence.token_ids),
            "format": "html" if suffix in {".html", ".htm"} else "text",
        },
    )


def _token_strings(sequence: TokenizerSequenceV1, vocab: Vocab | None) -> list[str]:
    if sequence.token_strs is not None:
        return list(sequence.token_strs)
    active = vocab or build_vocab(default_tokenizer_config())
    return [active.id_to_token.get(i, st.UNK) for i in sequence.token_ids]
