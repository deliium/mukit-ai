-- Composition history: content-addressed snapshots, immutable revisions, named branches.
-- Project active pointers are nullable until ensure_project_history() bootstraps them.

CREATE TABLE IF NOT EXISTS composition_snapshots (
    fingerprint TEXT PRIMARY KEY,
    encoding_profile TEXT NOT NULL,
    compression_profile TEXT NOT NULL,
    payload_zlib BLOB NOT NULL,
    uncompressed_byte_size INTEGER NOT NULL,
    compressed_byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (uncompressed_byte_size >= 0),
    CHECK (compressed_byte_size >= 0)
);

CREATE TABLE IF NOT EXISTS project_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_revision_id TEXT,
    snapshot_fingerprint TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    name TEXT,
    operation_type TEXT NOT NULL,
    ai_provider TEXT,
    ai_model TEXT,
    user_instruction TEXT,
    affected_ranges_json TEXT NOT NULL DEFAULT '[]',
    affected_track_ids_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, parent_revision_id)
        REFERENCES project_revisions(project_id, id),
    FOREIGN KEY (snapshot_fingerprint) REFERENCES composition_snapshots(fingerprint),
    UNIQUE (project_id, id),
    UNIQUE (project_id, sequence)
);

CREATE TABLE IF NOT EXISTS project_branches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    head_revision_id TEXT NOT NULL,
    created_from_revision_id TEXT,
    working_composition_json TEXT,
    working_fingerprint TEXT NOT NULL,
    working_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, head_revision_id)
        REFERENCES project_revisions(project_id, id),
    FOREIGN KEY (project_id, created_from_revision_id)
        REFERENCES project_revisions(project_id, id),
    UNIQUE (project_id, id),
    UNIQUE (project_id, normalized_name),
    CHECK (working_version >= 0)
);

ALTER TABLE projects ADD COLUMN active_branch_id TEXT;
ALTER TABLE projects ADD COLUMN current_revision_id TEXT;

CREATE INDEX IF NOT EXISTS idx_project_revisions_project_sequence
    ON project_revisions (project_id, sequence DESC);

CREATE INDEX IF NOT EXISTS idx_project_revisions_parent
    ON project_revisions (project_id, parent_revision_id);

CREATE INDEX IF NOT EXISTS idx_project_revisions_snapshot
    ON project_revisions (snapshot_fingerprint);

CREATE INDEX IF NOT EXISTS idx_project_branches_project
    ON project_branches (project_id);

CREATE INDEX IF NOT EXISTS idx_project_branches_head
    ON project_branches (project_id, head_revision_id);

-- SQLite cannot add composite FKs to an existing projects table; enforce locality via triggers.

CREATE TRIGGER IF NOT EXISTS projects_active_branch_insert_check
BEFORE INSERT ON projects
WHEN NEW.active_branch_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'active_branch_id must reference a branch of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_branches
        WHERE id = NEW.active_branch_id AND project_id = NEW.id
    );
END;

CREATE TRIGGER IF NOT EXISTS projects_active_branch_update_check
BEFORE UPDATE OF active_branch_id ON projects
WHEN NEW.active_branch_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'active_branch_id must reference a branch of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_branches
        WHERE id = NEW.active_branch_id AND project_id = NEW.id
    );
END;

CREATE TRIGGER IF NOT EXISTS projects_current_revision_insert_check
BEFORE INSERT ON projects
WHEN NEW.current_revision_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'current_revision_id must reference a revision of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_revisions
        WHERE id = NEW.current_revision_id AND project_id = NEW.id
    );
END;

CREATE TRIGGER IF NOT EXISTS projects_current_revision_update_check
BEFORE UPDATE OF current_revision_id ON projects
WHEN NEW.current_revision_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'current_revision_id must reference a revision of this project')
    WHERE NOT EXISTS (
        SELECT 1 FROM project_revisions
        WHERE id = NEW.current_revision_id AND project_id = NEW.id
    );
END;
