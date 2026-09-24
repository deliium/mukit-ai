"""Add neural_audio_stem_sets + neural_audio_stems tables (PCM stays on filesystem).

Revision ID: 20260924_0007
Revises: 20260924_0006
Create Date: 2026-09-24

Stem-aware neural egress: parent stem sets and member stems. Never stores PCM
in SQLite and never touches composition_snapshots.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "20260924_0007"
down_revision: Union[str, Sequence[str], None] = "20260924_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_STEM_SETS_DDL = """
CREATE TABLE IF NOT EXISTS neural_audio_stem_sets (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    source_revision_id TEXT,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    model_id TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'neural',
    origin_tick INTEGER NOT NULL DEFAULT 0,
    duration_ticks INTEGER NOT NULL DEFAULT 0,
    tempo_bpm REAL,
    sample_rate INTEGER NOT NULL DEFAULT 22050,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (status IN ('queued', 'running', 'failed', 'complete')),
    CHECK (engine IN ('neural', 'fluidsynth')),
    CHECK (origin_tick >= 0),
    CHECK (duration_ticks >= 0),
    CHECK (sample_rate >= 8000)
)
"""

_STEMS_DDL = """
CREATE TABLE IF NOT EXISTS neural_audio_stems (
    id TEXT PRIMARY KEY,
    stem_set_id TEXT NOT NULL,
    project_id TEXT,
    stem_role TEXT NOT NULL,
    source_track_ids_json TEXT NOT NULL DEFAULT '[]',
    source_revision_id TEXT,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT,
    adapter_kind TEXT,
    fidelity_class TEXT NOT NULL,
    capability_used TEXT NOT NULL,
    sync_class TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'neural',
    instructions TEXT NOT NULL DEFAULT '',
    instruction_chars INTEGER NOT NULL DEFAULT 0,
    genre TEXT,
    mood TEXT,
    tempo_bpm REAL,
    seed INTEGER,
    sample_rate INTEGER,
    origin_tick INTEGER NOT NULL DEFAULT 0,
    duration_ticks INTEGER NOT NULL DEFAULT 0,
    bar_start INTEGER,
    bar_end INTEGER,
    supersedes_stem_id TEXT,
    error_code TEXT,
    error_message TEXT,
    audio_relpath TEXT,
    content_type TEXT,
    byte_size INTEGER,
    sha256_prefix TEXT,
    adapter_warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (stem_set_id) REFERENCES neural_audio_stem_sets(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (supersedes_stem_id) REFERENCES neural_audio_stems(id) ON DELETE SET NULL,
    CHECK (status IN ('queued', 'running', 'failed', 'complete')),
    CHECK (stem_role IN ('piano', 'bass', 'strings', 'drums', 'vocals', 'other')),
    CHECK (capability_used IN (
        'direct_stems', 'per_track', 'grouped_tracks',
        'section_symbolic_filter', 'fluidsynth_deterministic'
    )),
    CHECK (sync_class IN (
        'deterministic_midi', 'timeline_aligned', 'generative_independent'
    )),
    CHECK (engine IN ('neural', 'fluidsynth')),
    CHECK (
        fidelity_class IN ('deterministic', 'neural_instrument', 'generative')
    ),
    CHECK (
        adapter_kind IS NULL OR adapter_kind IN (
            'midi_projection', 'melody_conditioning', 'text_prompt'
        )
    ),
    CHECK (byte_size IS NULL OR byte_size >= 0),
    CHECK (instruction_chars >= 0),
    CHECK (origin_tick >= 0),
    CHECK (duration_ticks >= 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_stem_sets_project_created "
    "ON neural_audio_stem_sets (project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_stem_sets_status "
    "ON neural_audio_stem_sets (status)",
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_stems_set "
    "ON neural_audio_stems (stem_set_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_stems_project "
    "ON neural_audio_stems (project_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_neural_audio_stems_role "
    "ON neural_audio_stems (stem_set_id, stem_role)",
)


def upgrade() -> None:
    logger.info(
        "Applying neural_audio stems migration",
        extra={"revision": revision, "down_revision": down_revision},
    )
    op.execute(_STEM_SETS_DDL)
    op.execute(_STEMS_DDL)
    for ddl in _INDEXES:
        op.execute(ddl)
    logger.info("neural_audio stem tables ready", extra={"revision": revision})


def downgrade() -> None:
    logger.info("Downgrading neural_audio stems migration", extra={"revision": revision})
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_stems_role")
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_stems_project")
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_stems_set")
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_stem_sets_status")
    op.execute("DROP INDEX IF EXISTS idx_neural_audio_stem_sets_project_created")
    op.execute("DROP TABLE IF EXISTS neural_audio_stems")
    op.execute("DROP TABLE IF EXISTS neural_audio_stem_sets")
