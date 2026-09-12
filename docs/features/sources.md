# Read-only database snapshots

The data page exposes configured sources, named queries, typed parameters, cache reuse and explicit refresh. Each successful extraction creates an immutable dataset and source_snapshot record. SQL, connection URLs and passwords are never accepted from the UI or returned by the catalogue.

## Configuration

Set ARD_DATA_SOURCES to a JSON object in the server environment. Production sources should use accounts with read-only grants. SQLite files must exist at an absolute path. PostgreSQL uses psycopg and MySQL uses PyMySQL through SQLAlchemy.

~~~json
{
  "quality": {
    "name": "Quality records",
    "driver": "sqlite",
    "path": "/srv/sources/quality.sqlite3",
    "projects": ["PROJECT_ID"],
    "queries": {
      "recent": {
        "name": "Recent inspections",
        "sql": "SELECT id, value, label FROM inspections WHERE id >= :start ORDER BY id",
        "parameters": {"start": "integer"},
        "max_rows": 5000,
        "cache_ttl_seconds": 3600,
        "timeout_seconds": 5
      }
    }
  }
}
~~~

For remote sources replace driver/path with driver postgresql or mysql and url_env naming a server environment variable holding the connection URL. URL query parameters are rejected. Project grants can be a list of project IDs or ["*"]. Supported parameter types are text, integer, number and boolean. Cache identity includes the project, source, query, parameters and endpoint fingerprint; original connection material is not stored.

| Route | Contract |
|---|---|
| GET /api/projects/{pid}/sources | Authorized aliases and query schemas, without SQL or credentials |
| POST /api/projects/{pid}/sources/{source_id}/snapshot | query_id, parameters, optional name and refresh; returns cached, dataset and snapshot |

Writers can create or refresh snapshots; readers can inspect source schemas and already imported data. Empty results, row overflow, duplicate columns, oversized cells and non-finite values fail before any dataset record is committed. A refresh creates a child version; a cache hit keeps the existing ID. Archived datasets are excluded from cache hits.

## Execution limits and evidence

Queries are parsed as a single SELECT expression; DDL, DML, INTO, locks, anonymous functions and functions outside a fixed read-only whitelist are rejected. Values use bound parameters. SQLite additionally uses URI mode=ro, query_only, a restrictive authorizer and a progress deadline. These mechanisms follow the [Python sqlite3 documentation](https://docs.python.org/3/library/sqlite3.html).

PostgreSQL uses read-only transactions with a statement timeout, following the [SQLAlchemy PostgreSQL dialect documentation](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#setting-read-only-deferrable). MySQL uses read-only transactions and a SELECT execution timeout; its read-only behavior is described in the [MySQL manual](https://dev.mysql.com/doc/refman/8.4/en/innodb-performance-ro-txn.html).

There are two simultaneous extraction slots, at most 50,000 rows, 200 columns, 20 MiB of encoded results and a configurable 0.1–30 second statement budget. Driver connection/read timeouts bound remote waiting; a database-level timeout does not guarantee an exact end-to-end deadline. No arbitrary query editor or write-back exists.

Tests execute actual SQLite extraction, verify source bytes are unchanged, cache/refresh/version semantics, project and role checks, parameter types, rejected write/side-effect SQL, limits, configuration invalidation and credential masking. PostgreSQL and MySQL adapters are implemented but require live target-server validation. MongoDB, Elasticsearch and vendor-specific domestic databases are not implemented in this release.
