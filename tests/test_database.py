"""The database connector: authorized connection only, reads free, writes approval-gated,
credentials never exposed, stacked/injection statements refused."""
import sqlite3
from pathlib import Path

import pytest

from invoice_agent.database import Database, DatabaseError

import os
CHROMIUM = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"
CHROMIUM = CHROMIUM if os.path.exists(CHROMIUM) else None


@pytest.fixture
def db_url(tmp_path):
    f = tmp_path / "shop.db"
    con = sqlite3.connect(f)
    con.executescript("""
        CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, email TEXT);
        INSERT INTO customers(name,email) VALUES ('Ada','ada@x.com'),('Bo','bo@x.com');
        CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INT, total REAL);
        INSERT INTO orders(customer_id,total) VALUES (1,42.0),(1,8.0),(2,5.0);
    """)
    con.commit(); con.close()
    return f"sqlite:///{f}"


def test_read_query_works(db_url):
    db = Database(db_url).connect()
    r = db.query("SELECT name, email FROM customers ORDER BY name")
    assert r.columns == ["name", "email"] and r.rows[0] == ["Ada", "ada@x.com"]
    assert "Ada" in r.as_text()
    # a WHERE / aggregate works
    assert db.query("SELECT COUNT(*) FROM orders WHERE customer_id = 1").rows[0][0] == 2
    db.close()


def test_query_refuses_writes(db_url):
    db = Database(db_url).connect()
    for bad in ["DELETE FROM customers", "UPDATE customers SET name='x'", "DROP TABLE orders",
                "INSERT INTO customers(name) VALUES ('z')"]:
        with pytest.raises(DatabaseError):
            db.query(bad)
    # the data is untouched
    assert db.query("SELECT COUNT(*) FROM customers").rows[0][0] == 2
    db.close()


def test_query_blocks_stacked_statements(db_url):
    db = Database(db_url).connect()
    with pytest.raises(DatabaseError):
        db.query("SELECT 1; DROP TABLE customers")
    assert db.query("SELECT COUNT(*) FROM customers").rows[0][0] == 2   # second statement never ran
    db.close()


def test_read_only_transaction_backstops(db_url):
    """Even if a write keyword slips past the leader check, the read-only txn stops it."""
    db = Database(db_url).connect()
    # 'WITH' is an allowed read leader; a CTE that tries to delete must still fail to change data
    with pytest.raises(DatabaseError):
        db.query("WITH x AS (DELETE FROM customers RETURNING *) SELECT * FROM x")
    assert db.query("SELECT COUNT(*) FROM customers").rows[0][0] == 2
    db.close()


def test_execute_requires_approval(db_url):
    db = Database(db_url).connect()
    asked = []
    with pytest.raises(DatabaseError):          # declined
        db.execute("DELETE FROM orders WHERE id = 3", approve=lambda m: asked.append(m) or False)
    assert len(asked) == 1 and "database" in asked[0].lower()
    assert db.query("SELECT COUNT(*) FROM orders").rows[0][0] == 3
    # approved
    r = db.execute("DELETE FROM orders WHERE id = 3", approve=lambda m: True)
    assert "1 row" in r.as_text()
    assert db.query("SELECT COUNT(*) FROM orders").rows[0][0] == 2
    db.close()


def test_schema_listing(db_url):
    db = Database(db_url).connect()
    sch = db.schema()
    assert "customers(" in sch and "orders(" in sch and "email" in sch
    db.close()


def test_credentials_scrubbed_from_errors():
    db = Database("postgres://user:sup3rsecret@db.example:5432/app")
    with pytest.raises(DatabaseError) as ei:
        db.connect()   # psycopg not installed here -> clean error, no password
    assert "sup3rsecret" not in str(ei.value)
    assert db.secret_values()[0] == "postgres://user:sup3rsecret@db.example:5432/app"
    assert "sup3rsecret" in db.secret_values()


def test_rejects_unknown_scheme():
    with pytest.raises(DatabaseError):
        Database("redis://x")
    with pytest.raises(DatabaseError):
        Database("")


def test_session_can_use_connected_db(tmp_path):
    """A scripted run shows db tools appear only with a db, reads work, a write is approval-gated."""
    import sqlite3, time
    from invoice_agent.demo_portal import MockPortal
    from invoice_agent.session import AgentSession
    from anthropic.types.beta import BetaMessage

    f = tmp_path / "s.db"
    con = sqlite3.connect(f); con.executescript(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT); INSERT INTO t(v) VALUES ('a'),('b');"); con.commit(); con.close()

    class DbScript:
        def __init__(self): self.i = 0; self.tools_seen = None; self.beta = self; self.messages = self
        def create(self, **kw):
            self.tools_seen = [t["name"] for t in kw["tools"] if "name" in t]
            plan = [("db_query", {"sql": "SELECT COUNT(*) FROM t"}),
                    ("db_execute", {"sql": "DELETE FROM t WHERE id=1"}),
                    ("finish", {"status": "success", "summary": "done"})]
            name, args = plan[min(self.i, len(plan)-1)]; self.i += 1
            return BetaMessage.model_validate({"id": f"m{self.i}", "type": "message", "role": "assistant",
                "model": kw["model"], "stop_reason": "tool_use", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "tool_use", "id": f"t{self.i}", "name": name, "input": args}]})

    with MockPortal() as portal:
        sc = DbScript()
        s = AgentSession(url=portal.url + "/login", username="", password="", task="use the db",
                         client=sc, runs_dir=tmp_path/"r", out_dir=tmp_path/"o",
                         executable_path=CHROMIUM, db_url=f"sqlite:///{f}")
        s.start()
        end = time.time() + 60
        while time.time() < end and not any(e["kind"] == "done" for e in s.events):
            if s.status == "needs approval":   # the DELETE asks; decline it
                s.decide(False); time.sleep(0.2)
            time.sleep(0.1)
        s.stop(); s.join(20)
    assert sc.tools_seen and "db_query" in sc.tools_seen and "db_execute" in sc.tools_seen
    con = sqlite3.connect(f); n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]; con.close()
    assert n == 2   # the declined DELETE did not run
