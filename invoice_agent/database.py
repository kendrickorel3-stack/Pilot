"""A database connector the owner explicitly hands credentials to.

Pilot only ever connects with a connection string the person provides (like handing over a
key), never to a database it was not given access to. Safety, enforced here in code:

- Reads run in a read-only transaction, so a query cannot change data even if it tries.
- Writes and schema changes (INSERT/UPDATE/DELETE/DROP/...) go through db_execute, which asks
  the person for approval first — exactly like a purchase on a web page.
- The connection string (which holds the password) is never shown to the AI and is redacted
  from everything the model sees.
- Results are capped so a huge table can't flood the model or the bill.

Drivers: SQLite (built in), PostgreSQL (psycopg), MySQL/MariaDB (pymysql). The Postgres and
MySQL drivers are imported only when such a URL is used, and ship in the hosted image.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote, urlparse

MAX_ROWS = 100
MAX_CELL = 300
READ_LEADERS = {"select", "with", "explain", "show", "describe", "desc", "pragma", "values", "table"}
WRITE_LEADERS = {"insert", "update", "delete", "replace", "merge", "upsert", "truncate", "drop",
                 "create", "alter", "grant", "revoke", "call", "do", "set", "copy", "vacuum", "attach"}


class DatabaseError(Exception):
    """Something went wrong connecting to or querying the database; shown to the person/model."""


@dataclass
class DbResult:
    columns: list[str]
    rows: list[list]
    truncated: bool
    note: str = ""

    def as_text(self) -> str:
        if self.note and not self.columns:
            return self.note
        head = " | ".join(self.columns)
        body = "\n".join(" | ".join(_cell(c) for c in row) for row in self.rows)
        out = f"{head}\n{'-' * min(len(head), 80)}\n{body}" if self.columns else body
        tail = []
        if self.truncated:
            tail.append(f"(showing first {MAX_ROWS} rows; add LIMIT or a WHERE clause to narrow it)")
        if self.note:
            tail.append(self.note)
        return out + ("\n" + "\n".join(tail) if tail else "")


def _cell(v) -> str:
    if v is None:
        return "NULL"
    s = str(v)
    return s if len(s) <= MAX_CELL else s[:MAX_CELL] + "…"


def _leader(sql: str) -> str:
    s = re.sub(r"^\s*(/\*.*?\*/|--[^\n]*\n)*", "", sql, flags=re.S)  # skip leading comments
    m = re.match(r"\s*([a-zA-Z]+)", s)
    return (m.group(1).lower() if m else "")


def _single_statement(sql: str) -> str:
    """Return the one statement, or raise if it is stacked (a classic injection shape)."""
    body = sql.strip().rstrip(";").strip()
    # reject a ';' that is outside quotes (a second statement)
    depth_ok, i, n = True, 0, len(body)
    in_s = in_d = False
    while i < n:
        c = body[i]
        if c == "'" and not in_d:
            in_s = not in_s
        elif c == '"' and not in_s:
            in_d = not in_d
        elif c == ";" and not in_s and not in_d:
            depth_ok = False
            break
        i += 1
    if not depth_ok:
        raise DatabaseError("Run one statement at a time (no ';' joining several).")
    if not body:
        raise DatabaseError("Empty query.")
    return body


class Database:
    def __init__(self, url: str):
        self.url = (url or "").strip()
        if not self.url:
            raise DatabaseError("No database connection was provided.")
        self.scheme = urlparse(self.url).scheme.lower().split("+")[0]
        if self.scheme not in ("sqlite", "postgres", "postgresql", "mysql", "mariadb"):
            raise DatabaseError("Unsupported database. Use a sqlite://, postgres:// or mysql:// connection URL.")
        self.conn = None
        self._kind = "sqlite" if self.scheme == "sqlite" else ("postgres" if self.scheme in ("postgres", "postgresql") else "mysql")

    # ---- lifecycle ----
    def connect(self) -> "Database":
        try:
            if self._kind == "sqlite":
                import sqlite3
                path = self.url[len("sqlite://"):]
                path = path[1:] if path.startswith("/") and path != "/:memory:" else path
                self.conn = sqlite3.connect(path or ":memory:", check_same_thread=False)
            elif self._kind == "postgres":
                try:
                    import psycopg
                    self.conn = psycopg.connect(self.url, connect_timeout=10)
                except ImportError:
                    raise DatabaseError("PostgreSQL support is not installed in this build.")
            else:
                import pymysql
                u = urlparse(self.url)
                self.conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
                                            password=unquote(u.password or ""), database=(u.path or "/").lstrip("/"),
                                            connect_timeout=10)
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"Could not connect: {_clean(e, self.url)}")
        return self

    def close(self) -> None:
        try:
            self.conn and self.conn.close()
        except Exception:
            pass

    def secret_values(self) -> list[str]:
        """Values to redact from anything the model sees."""
        out = [self.url]
        pw = urlparse(self.url).password
        if pw:
            out.append(unquote(pw))
        return [v for v in out if v and len(v) >= 4]

    def label(self) -> str:
        u = urlparse(self.url)
        if self._kind == "sqlite":
            return f"SQLite ({self.url[len('sqlite://'):].lstrip('/') or ':memory:'})"
        db = (u.path or "").lstrip("/")
        return f"{self._kind} {db or ''}@{u.hostname or ''}".strip()

    # ---- operations ----
    def _run(self, sql: str, read_only: bool) -> DbResult:
        cur = self.conn.cursor()
        try:
            if self._kind == "sqlite":
                self.conn.execute(f"PRAGMA query_only = {'ON' if read_only else 'OFF'}")
            elif self._kind == "postgres" and read_only:
                cur.execute("SET TRANSACTION READ ONLY")
            elif self._kind == "mysql" and read_only:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(MAX_ROWS + 1)
                truncated = len(rows) > MAX_ROWS
                data = [list(r) for r in rows[:MAX_ROWS]]
                if not read_only:
                    self.conn.commit()
                return DbResult(cols, data, truncated)
            affected = cur.rowcount
            if not read_only:
                self.conn.commit()
            return DbResult([], [], False, note=f"OK. {affected if affected is not None and affected >= 0 else 0} row(s) affected.")
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise DatabaseError(_clean(e, self.url))
        finally:
            try:
                if self._kind == "mysql" and read_only:
                    cur.execute("SET SESSION TRANSACTION READ WRITE")
                if self._kind == "sqlite":
                    self.conn.execute("PRAGMA query_only = OFF")
            except Exception:
                pass
            cur.close()

    def query(self, sql: str) -> DbResult:
        """Read-only query. Writes are refused here; the transaction is read-only as a backstop."""
        stmt = _single_statement(sql)
        lead = _leader(stmt)
        if lead in WRITE_LEADERS or lead not in READ_LEADERS:
            raise DatabaseError(f"db_query only reads. '{lead.upper() or '?'}' changes data or schema; use db_execute "
                                "(which asks the person for approval).")
        return self._run(stmt, read_only=True)

    def execute(self, sql: str, approve: Callable[[str], bool]) -> DbResult:
        """A write or schema change. Always asks the person first."""
        stmt = _single_statement(sql)
        preview = stmt if len(stmt) <= 300 else stmt[:300] + "…"
        if not approve(f"The agent wants to run this on your database ({self.label()}):\n{preview}\nAllow it?"):
            raise DatabaseError("Blocked: that changes the database and was not approved. Do not try it again.")
        return self._run(stmt, read_only=False)

    def schema(self) -> str:
        if self._kind == "sqlite":
            tbls = self._run("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name", True).rows
            out = []
            for (name,) in tbls:
                cols = self._run(f'PRAGMA table_info("{name}")', True).rows
                out.append(f"{name}(" + ", ".join(f"{c[1]} {c[2]}" for c in cols) + ")")
            return "Tables:\n" + "\n".join(out) if out else "No tables found."
        q = ("SELECT table_name, column_name, data_type FROM information_schema.columns "
             "WHERE table_schema NOT IN ('information_schema','pg_catalog','mysql','performance_schema','sys') "
             "ORDER BY table_name, ordinal_position")
        rows = self._run(q, True).rows
        by: dict[str, list[str]] = {}
        for t, c, dt in rows:
            by.setdefault(t, []).append(f"{c} {dt}")
        return "Tables:\n" + "\n".join(f"{t}(" + ", ".join(cs) + ")" for t, cs in by.items()) if by else "No tables found."


def _clean(e: Exception, url: str) -> str:
    """One-line error with the connection string scrubbed out."""
    msg = str(e).splitlines()[0][:300]
    for s in (url, urlparse(url).password or ""):
        if s:
            msg = msg.replace(s, "[redacted]")
    return msg
