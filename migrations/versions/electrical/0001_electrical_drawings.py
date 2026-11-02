"""electrical: WireTwin drawings -- projects, documents, jobs, pages, profiles, characterizations.

electrical-drawings INC-1 (docs/delivery/electrical-drawings/docs/lld-inc-1.md 4.3). Seven
tables, all prefixed ``drawings_`` (HLD 8.2) in the ``electrical`` schema, which is NOT created
here but by ``platform/0006_electrical_schema.py`` (a chain cannot create the schema its own
``alembic_version`` lives in -- that file's docstring, mirroring ``platform/0004``).

Every table carries ``workspace_id`` with ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` and a
``tenant_isolation`` policy in the hardened ``NULLIF`` form (``spaces/0001_spaces.py``); RLS is
on ``workspace_id`` alone, the space and the project are filters in each repository's ``WHERE``
(01 section 3). ``trg_touch`` (``platform.touch_updated_at()``) is on every table with an
``updated_at``: projects, documents, jobs, pages, page_stages.

**Expand-only, and no lock risk on real data.** Every table and every index is born in this
revision, so no ``CREATE INDEX CONCURRENTLY`` is needed (``test_zero_downtime_migrations``): no
existing table is touched and nothing reads the schema before PR-C's code ships.

**Foreign keys stay inside the section** (HLD 5.2, 01 section 0): jobs, pages, profiles and
characterizations reference ``drawings_documents(id)``; page_stages references pages. Everything
else -- ``space_id``, ``file_id``, ``project_id`` on documents -- is a LOGICAL reference. There is
no ``ON DELETE CASCADE``: ``app.ops.purge`` empties each table with its own counted statement,
in the order stages, pages, characterizations, jobs, profile_versions, documents, projects.

**Which constraint raises ``23505`` is the contract** the SQL adapter translates:
``ux_drawings_projects_space`` -> ``electrical.project_exists``, ``ux_drawings_documents_file``
-> ``electrical.document_exists``, ``ux_drawings_documents_content`` ->
``electrical.duplicate_document``, ``ux_drawings_jobs_open`` -> ``electrical.job_conflict``.

``ck_drawings_page_stages_stage`` allows only ``'classify'`` today; each increment that adds a
stage widens it (drop, then ``ADD ... NOT VALID`` + ``VALIDATE``).

**Downgrade loses data, explicitly:** it drops all seven tables, so every project, document,
job, page, profile version and characterization is gone. The ``files.files`` rows stay (the
reference is logical) and the files can be registered again. Prefer leaving the tables in place
and switching ``ELECTRICAL_ENABLED`` off (LLD 4.4).

Revision ID: 0001_electrical_drawings
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_electrical_drawings"  # 24 chars <= 32 (test_migration_revision_ids.py)
down_revision = None
branch_labels = ("electrical",)
depends_on = None

# Creation order = FK order; downgrade drops in the reverse.
_TABLES = (
    "drawings_projects",
    "drawings_documents",
    "drawings_jobs",
    "drawings_pages",
    "drawings_page_stages",
    "drawings_profile_versions",
    "drawings_characterizations",
)
# Tables with an `updated_at` (and so a touch trigger).
_TOUCHED = (
    "drawings_projects",
    "drawings_documents",
    "drawings_jobs",
    "drawings_pages",
    "drawings_page_stages",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE electrical.drawings_projects (
          id            uuid PRIMARY KEY,
          workspace_id  uuid NOT NULL,
          space_id      uuid NOT NULL,
          name          text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
          description   text NULL CHECK (description IS NULL OR char_length(description) <= 2000),
          created_by    uuid NULL,
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          version       integer NOT NULL DEFAULT 1
        );
        """
    )
    # One project per space (SRS D-29). Plain, not partial: a project is only ever
    # hard-deleted (INC1-D-15). 23505 here -> electrical.project_exists.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_drawings_projects_space
          ON electrical.drawings_projects(workspace_id, space_id);
        """
    )

    op.execute(
        """
        CREATE TABLE electrical.drawings_documents (
          id               uuid PRIMARY KEY,
          workspace_id     uuid NOT NULL,
          space_id         uuid NOT NULL,
          project_id       uuid NOT NULL,
          file_id          uuid NOT NULL,
          status           text NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active','excluded','purging')),
          exclusion_reason text NULL
                             CHECK (exclusion_reason IS NULL
                                    OR exclusion_reason IN ('file_unreadable','duplicate_content')),
          content_sha256   text NULL
                             CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
          page_count       integer NULL CHECK (page_count IS NULL OR page_count >= 0),
          pdf_creator      text NULL CHECK (pdf_creator IS NULL OR char_length(pdf_creator) <= 512),
          pdf_producer     text NULL CHECK (pdf_producer IS NULL OR char_length(pdf_producer) <= 512),
          profile_state    text NOT NULL DEFAULT 'none'
                             CHECK (profile_state IN ('none','proposed','accepted','confirmed')),
          pipeline_version text NULL,
          created_by       uuid NULL,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          purging_at       timestamptz NULL,
          version          integer NOT NULL DEFAULT 1,
          CONSTRAINT ck_drawings_documents_exclusion
            CHECK ((status = 'excluded') = (exclusion_reason IS NOT NULL))
        );
        """
    )
    # One live document per file (HLD 8.7).   23505 -> electrical.document_exists
    op.execute(
        """
        CREATE UNIQUE INDEX ux_drawings_documents_file
          ON electrical.drawings_documents(workspace_id, file_id) WHERE status <> 'purging';
        """
    )
    # FR-ING-02: the same bytes once per project.   23505 -> electrical.duplicate_document
    op.execute(
        """
        CREATE UNIQUE INDEX ux_drawings_documents_content
          ON electrical.drawings_documents(workspace_id, project_id, content_sha256)
          WHERE status = 'active' AND content_sha256 IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX ix_drawings_documents_project
          ON electrical.drawings_documents(workspace_id, project_id, id DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_drawings_documents_space
          ON electrical.drawings_documents(workspace_id, space_id);
        """
    )

    op.execute(
        """
        CREATE TABLE electrical.drawings_jobs (
          id                  uuid PRIMARY KEY,
          workspace_id        uuid NOT NULL,
          document_id         uuid NOT NULL REFERENCES electrical.drawings_documents(id),
          kind                text NOT NULL CHECK (kind IN ('ingest','reprocess')),
          pages               integer[] NULL
                                CHECK (pages IS NULL OR cardinality(pages) BETWEEN 1 AND 1000),
          status              text NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued','running','awaiting_confirmation',
                                                  'succeeded','failed','cancelled')),
          stage               text NULL,
          failure_code        text NULL
                                CHECK (failure_code IS NULL OR char_length(failure_code) <= 64),
          failure_detail      text NULL
                                CHECK (failure_detail IS NULL OR char_length(failure_detail) <= 2000),
          attempt             integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          lease_until         timestamptz NULL,
          seq                 bigint NOT NULL DEFAULT 0 CHECK (seq >= 0),
          cancel_requested_at timestamptz NULL,
          pipeline_version    text NOT NULL,
          created_by          uuid NULL,
          created_at          timestamptz NOT NULL DEFAULT now(),
          updated_at          timestamptz NOT NULL DEFAULT now(),
          started_at          timestamptz NULL,
          finished_at         timestamptz NULL,
          version             integer NOT NULL DEFAULT 1
        );
        """
    )
    # One open job per document.   23505 -> electrical.job_conflict
    op.execute(
        """
        CREATE UNIQUE INDEX ux_drawings_jobs_open
          ON electrical.drawings_jobs(workspace_id, document_id)
          WHERE status IN ('queued','running','awaiting_confirmation');
        """
    )
    op.execute(
        """
        CREATE INDEX ix_drawings_jobs_queue
          ON electrical.drawings_jobs(workspace_id, created_at) WHERE status = 'queued';
        """
    )
    op.execute(
        """
        CREATE INDEX ix_drawings_jobs_document
          ON electrical.drawings_jobs(workspace_id, document_id, created_at DESC);
        """
    )

    op.execute(
        """
        CREATE TABLE electrical.drawings_pages (
          workspace_id uuid NOT NULL,
          document_id  uuid NOT NULL REFERENCES electrical.drawings_documents(id),
          page_no      integer NOT NULL CHECK (page_no >= 1),
          kind         text NULL CHECK (kind IS NULL OR kind IN
                         ('vector_text','vector_outlined','vector_garbled','raster','mixed','empty')),
          language     text NULL CHECK (language IS NULL OR language IN ('en','zh','mixed','none')),
          needs_ocr    boolean NULL,
          signals      jsonb NULL CHECK (signals IS NULL OR octet_length(signals::text) <= 16384),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (workspace_id, document_id, page_no)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE electrical.drawings_page_stages (
          workspace_id     uuid NOT NULL,
          document_id      uuid NOT NULL,
          page_no          integer NOT NULL,
          stage            text NOT NULL,
          status           text NOT NULL
                             CHECK (status IN ('pending','running','succeeded','failed',
                                               'needs_review')),
          reason           text NULL CHECK (reason IS NULL OR char_length(reason) BETWEEN 1 AND 64),
          attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          pipeline_version text NOT NULL,
          updated_at       timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (workspace_id, document_id, page_no, stage),
          FOREIGN KEY (workspace_id, document_id, page_no)
            REFERENCES electrical.drawings_pages(workspace_id, document_id, page_no),
          CONSTRAINT ck_drawings_page_stages_stage CHECK (stage IN ('classify')),
          CONSTRAINT ck_drawings_page_stages_reason
            CHECK ((status IN ('failed','needs_review')) = (reason IS NOT NULL))
        );
        """
    )
    op.execute(
        """
        CREATE INDEX ix_drawings_page_stages_status
          ON electrical.drawings_page_stages(workspace_id, document_id, stage, status);
        """
    )

    op.execute(
        """
        CREATE TABLE electrical.drawings_profile_versions (
          id              uuid PRIMARY KEY,
          workspace_id    uuid NOT NULL,
          project_id      uuid NOT NULL,
          document_id     uuid NULL REFERENCES electrical.drawings_documents(id),
          version         integer NOT NULL CHECK (version >= 1),
          source          text NOT NULL CHECK (source IN ('default','user','detected','confirmed')),
          body            jsonb NOT NULL CHECK (octet_length(body::text) <= 65536),
          fingerprint     text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
          confidence      real NULL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
          mixed_standards boolean NOT NULL DEFAULT false,
          detection       jsonb NULL
                          CHECK (detection IS NULL OR octet_length(detection::text) <= 16384),
          created_by      uuid NULL,
          created_at      timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    # The two owners (the project, a document) each number their versions from 1; a lost
    # race on `append` is a 23505 on one of these.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_drawings_profiles_project
          ON electrical.drawings_profile_versions(workspace_id, project_id, version)
          WHERE document_id IS NULL;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_drawings_profiles_document
          ON electrical.drawings_profile_versions(workspace_id, document_id, version)
          WHERE document_id IS NOT NULL;
        """
    )

    op.execute(
        """
        CREATE TABLE electrical.drawings_characterizations (
          workspace_id     uuid NOT NULL,
          document_id      uuid NOT NULL REFERENCES electrical.drawings_documents(id),
          project_id       uuid NOT NULL,
          report           jsonb NOT NULL CHECK (octet_length(report::text) <= 262144),
          pipeline_version text NOT NULL,
          shared_version   text NOT NULL,
          rules_version    text NOT NULL,
          created_at       timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (workspace_id, document_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX ix_drawings_characterizations_project
          ON electrical.drawings_characterizations(workspace_id, project_id);
        """
    )

    # touch trigger (01 section 0, shared platform.touch_updated_at()).
    for table in _TOUCHED:
        op.execute(
            f"""
            CREATE TRIGGER trg_touch BEFORE UPDATE ON electrical.{table}
              FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
            """
        )

    # RLS (01 section 3, hardened NULLIF form: an EMPTY `app.workspace_id` matches nothing
    # instead of raising `invalid input syntax for type uuid`).
    for table in _TABLES:
        op.execute(f"ALTER TABLE electrical.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE electrical.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON electrical.{table}
              USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
              WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
            """
        )


def downgrade() -> None:
    # DATA LOSS: every row of the seven tables goes (see the module docstring). Policies and
    # triggers first, then the tables in reverse FK order.
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON electrical.{table}")
    for table in _TOUCHED:
        op.execute(f"DROP TRIGGER IF EXISTS trg_touch ON electrical.{table}")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS electrical.{table}")
