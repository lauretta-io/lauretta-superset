"""Tests of the audit trail's transaction rules (docker/pythonpath_dev/lauretta_audit.py).

The rules: an audit row is written in the transaction of the change it describes, and
its stdout (SIEM) line is printed only after that transaction commits; never for a
rollback, a rolled-back savepoint, a closed session or an outer transaction that a
joined session does not own. Plus redaction and the node name.

No Superset app or database server: SQLite with the ORM, run inside the Superset image
(it has SQLAlchemy 1.4, sqlalchemy_utils and superset), from the repo root:

  docker run --rm --network none -v "$PWD":/src:ro -w /src \
    -e PYTHONPATH=/src/docker/pythonpath_dev --entrypoint python \
    <superset image> -m unittest discover -s lauretta/tests -v

PostgreSQL-only cases run when AUDIT_TEST_PG_URL is set (see PostgresTest).
"""

from __future__ import annotations

import json  # noqa: TID251
import logging
import os
import tempfile
import unittest
from unittest import mock

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, Session

import lauretta_audit as la

Base = declarative_base()


class Thing(Base):
    __tablename__ = "things"
    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String(64))
    sqlalchemy_uri = sa.Column(sa.String(256))
    extra = sa.Column(sa.Text)


class Note(Base):
    """Written by a Session joined to the outer flush's connection, as Superset's tag
    listeners do (superset/tags/models.py)."""

    __tablename__ = "notes"
    id = sa.Column(sa.Integer, primary_key=True)
    text = sa.Column(sa.String(64))


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(record.getMessage()))


def _sqlite_engine(path: str) -> sa.engine.Engine:
    # pysqlite needs this to do SAVEPOINTs (SQLAlchemy's documented recipe).
    engine = sa.create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, _rec):  # noqa: ANN001
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conn):  # noqa: ANN001
        conn.exec_driver_sql("BEGIN")

    return engine


LINES = _Lines()


def setUpModule() -> None:  # noqa: N802
    la._state.key = b"k" * 32
    la._state.key_id = la._key_id(la._state.key)
    la._state.db_enabled = True
    la._state.initialised = True
    la._listen()
    la.audit_logger.addHandler(LINES)
    la.audit_logger.setLevel(logging.INFO)
    la.audit_logger.propagate = False


class AuditTransactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = _sqlite_engine(os.path.join(self.tmp.name, "meta.db"))
        Base.metadata.create_all(self.engine)
        la.audit_table.create(self.engine)
        with self.engine.begin() as conn:
            la._sqlite_triggers(conn)
        patcher = mock.patch.object(la, "_is_metadata", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        LINES.lines.clear()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    # helpers
    def rows(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [
                dict(r._mapping)
                for r in conn.execute(sa.select(la.audit_table).order_by("id"))
            ]

    def names(self, table: str = "things") -> list[str]:
        with self.engine.connect() as conn:
            col = "name" if table == "things" else "text"
            return [r[0] for r in conn.exec_driver_sql(f"SELECT {col} FROM {table}")]

    def printed(self) -> list[str]:
        return [line["target_name"] for line in LINES.lines]

    # the rules
    def test_commit_writes_row_then_prints_it(self) -> None:
        with Session(self.engine) as s:
            s.add(Thing(name="a"))
            s.flush()
            self.assertEqual(len(self.rows()), 0)  # not committed: not visible
            self.assertEqual(self.printed(), [])  # nor printed
            s.commit()
        rows = self.rows()
        self.assertEqual([r["action"] for r in rows], ["data.create"])
        self.assertEqual(self.printed(), ["a"])
        self.assertEqual(LINES.lines[0]["id"], rows[0]["id"])
        self.assertEqual(la._sign(rows[0], la._state.key), rows[0]["row_hmac"])

    def test_rollback_prints_nothing(self) -> None:
        with Session(self.engine) as s:
            s.add(Thing(name="a"))
            s.flush()
            s.rollback()
            s.add(Thing(name="b"))
            s.commit()
        self.assertEqual(self.names(), ["b"])
        self.assertEqual([r["target_name"] for r in self.rows()], ["b"])
        self.assertEqual(self.printed(), ["b"])

    def test_savepoint_rollback_forgets_only_its_records(self) -> None:
        with Session(self.engine) as s:
            s.add(Thing(name="a"))
            s.flush()
            sp = s.begin_nested()
            s.add(Thing(name="b"))
            s.flush()
            sp.rollback()
            s.commit()
        self.assertEqual(self.names(), ["a"])
        self.assertEqual(self.printed(), ["a"])

    def test_connection_savepoint_in_owning_session(self) -> None:
        # Codex finding: a savepoint opened on the connection, not the session.
        with Session(self.engine) as s:
            s.add(Thing(name="kept"))
            s.flush()
            sp = s.connection().begin_nested()
            s.add(Thing(name="never committed"))
            s.flush()
            sp.rollback()
            s.commit()
        self.assertEqual(self.names(), ["kept"])
        self.assertEqual(self.printed(), ["kept"])

    def test_released_inner_savepoint_then_outer_rolled_back(self) -> None:
        with Session(self.engine) as s:
            outer = s.connection().begin_nested()
            inner = s.begin_nested()
            s.add(Thing(name="inner"))
            s.flush()
            inner.commit()
            outer.rollback()
            s.add(Thing(name="after"))
            s.commit()
        self.assertEqual(self.names(), ["after"])
        self.assertEqual(self.printed(), ["after"])

    def test_commit_of_server_aborted_transaction_prints_nothing(self) -> None:
        # Codex finding: an error the code caught aborted the transaction on the server;
        # the later commit is a rollback without an exception.
        with Session(self.engine) as s:
            s.add(Thing(id=1, name="lost"))
            s.flush()
            with self.assertRaises(IntegrityError):
                s.execute(sa.text("INSERT OR ROLLBACK INTO things (id, name) VALUES (1, 'dup')"))
            s.commit()
        self.assertEqual((self.names(), self.rows(), self.printed()), ([], [], []))

    def test_same_uuid_as_object_and_text_is_no_change(self) -> None:
        import uuid

        u = uuid.uuid4()
        self.assertTrue(la._unchanged(u, str(u)))
        self.assertTrue(la._unchanged(str(u), u))
        self.assertFalse(la._unchanged({"x": 1}, '{"x": 1}'))  # Codex: a real change
        self.assertFalse(la._unchanged(u, str(uuid.uuid4())))

    def test_failed_flush_in_savepoint_keeps_earlier_records(self) -> None:
        # Codex finding: the flush's own sub-transaction rollback dropped them all.
        with Session(self.engine) as s:
            s.add(Thing(id=1, name="a"))
            s.flush()
            sp = s.begin_nested()
            s.add(Thing(id=1, name="dup"))
            with self.assertRaises(IntegrityError):
                s.flush()
            sp.rollback()
            s.commit()
        self.assertEqual(self.names(), ["a"])
        self.assertEqual(self.printed(), ["a"])

    def test_close_then_reuse_prints_nothing_stale(self) -> None:
        # Codex finding: close() rolls back without after_soft_rollback.
        s = Session(self.engine)
        s.add(Thing(name="gone"))
        s.flush()
        s.close()
        s.add(Thing(name="kept"))
        s.commit()
        s.close()
        self.assertEqual(self.names(), ["kept"])
        self.assertEqual(self.printed(), ["kept"])

    def test_session_joined_to_outer_transaction_waits_for_it(self) -> None:
        # Codex finding: Session(bind=conn).commit() does not commit conn's transaction.
        with self.engine.connect() as conn:
            outer = conn.begin()
            with Session(bind=conn) as inner:
                inner.add(Thing(name="inner"))
                inner.commit()
            self.assertEqual(self.printed(), [])
            outer.rollback()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.printed(), [])

    def _with_joined_listener(self) -> None:
        def write_note(_mapper, connection, target):  # noqa: ANN001
            with Session(bind=connection) as inner:
                inner.add(Note(text=f"note for {target.name}"))
                inner.commit()

        event.listen(Thing, "after_insert", write_note)
        self.addCleanup(event.remove, Thing, "after_insert", write_note)

    def test_tag_style_listener_prints_after_outer_commit(self) -> None:
        self._with_joined_listener()
        with Session(self.engine) as s:
            s.add(Thing(name="t"))
            s.flush()
            self.assertEqual(self.printed(), [])
            s.commit()
        printed = sorted((l["target_type"], l["id"]) for l in LINES.lines)
        rows = sorted((r["target_type"], r["id"]) for r in self.rows())
        self.assertEqual(printed, rows)
        self.assertEqual([t for t, _ in rows], ["notes", "things"])

    def test_tag_style_listener_rolled_back_prints_nothing(self) -> None:
        self._with_joined_listener()
        with Session(self.engine) as s:
            s.add(Thing(name="t"))
            s.flush()
            s.rollback()
        self.assertEqual((self.names(), self.names("notes")), ([], []))
        self.assertEqual(self.printed(), [])

    def test_two_joined_sessions_after_external_rollback(self) -> None:
        # Codex finding: a joined session's commit after the owner rolled back printed.
        with self.engine.connect() as conn:
            outer = conn.begin()
            with Session(bind=conn) as a:
                a.add(Thing(name="a"))
                a.commit()
            b = Session(bind=conn)
            b.add(Thing(name="b"))
            b.flush()
            outer.rollback()
            b.commit()
            b.close()
        self.assertEqual((self.names(), self.rows(), self.printed()), ([], [], []))

    def test_joined_session_inside_connection_savepoints(self) -> None:
        # Codex finding: records released into an outer savepoint that is then rolled
        # back were printed later.
        with self.engine.connect() as conn:
            outer = conn.begin()
            sp1 = conn.begin_nested()
            conn.begin_nested()
            with Session(bind=conn) as joined:
                joined.add(Thing(name="sp"))
                joined.commit()  # releases the inner savepoint only
            self.assertEqual(self.printed(), [])
            sp1.rollback()
            with Session(bind=conn) as again:
                again.add(Thing(name="after"))
                again.commit()
            outer.commit()
        self.assertEqual(self.names(), ["after"])
        self.assertEqual(self.printed(), [])  # joined writes with no owning session: no line

    def test_rollback_after_invalidate_does_not_raise(self) -> None:
        # Codex finding: the rollback listener reconnected an invalidated connection.
        with self.engine.connect() as conn:
            tx = conn.begin()
            conn.invalidate()
            tx.rollback()

    def test_commit_failure_prints_nothing(self) -> None:
        # Stands in for a failover at COMMIT: the commit raises, nothing is printed.
        with Session(self.engine) as s:
            s.add(Thing(name="lost"))
            s.flush()
            conn = s.connection()
            with mock.patch.object(
                conn.dialect, "do_commit", side_effect=sa.exc.OperationalError("c", {}, Exception("server gone"))
            ):
                with self.assertRaises(sa.exc.OperationalError):
                    s.commit()
            s.rollback()
        self.assertEqual(self.printed(), [])
        self.assertEqual(self.names(), [])

    def test_audit_write_failure_fails_the_change(self) -> None:
        with self.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE {la.TABLE_NAME}")
        with Session(self.engine) as s:
            s.add(Thing(name="x"))
            with self.assertRaises(sa.exc.OperationalError):
                s.flush()
            s.rollback()
        self.assertEqual(self.names(), [])
        self.assertEqual(self.printed(), [])

    def test_table_is_append_only(self) -> None:
        with Session(self.engine) as s:
            s.add(Thing(name="a"))
            s.commit()
        for sql in (f"UPDATE {la.TABLE_NAME} SET action='x'", f"DELETE FROM {la.TABLE_NAME}"):
            with self.assertRaisesRegex(sa.exc.IntegrityError, "append-only"):
                with self.engine.begin() as conn:
                    conn.exec_driver_sql(sql)


@unittest.skipUnless(os.environ.get("AUDIT_TEST_PG_URL"), "set AUDIT_TEST_PG_URL")
class PostgresTest(unittest.TestCase):
    """The PostgreSQL-only cases, against a throwaway server, e.g.
    docker network create t; docker run -d --rm --name t-pg --network t \
      -e POSTGRES_PASSWORD=t pgvector/pgvector:pg18
    then the command above plus --network t
      -e AUDIT_TEST_PG_URL=postgresql+psycopg2://postgres:t@t-pg/postgres"""

    def setUp(self) -> None:
        self.engine = sa.create_engine(os.environ["AUDIT_TEST_PG_URL"])
        with self.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {la.TABLE_NAME}, things, notes")
        Base.metadata.create_all(self.engine)
        la.audit_table.create(self.engine)
        with self.engine.begin() as conn:
            la._pg_triggers(conn)
        patcher = mock.patch.object(la, "_is_metadata", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.engine.dispose)
        LINES.lines.clear()

    def count(self, table: str) -> int:
        with self.engine.connect() as conn:
            return conn.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()

    def test_commit_prints(self) -> None:
        with Session(self.engine) as s:
            s.add(Thing(name="a"))
            s.commit()
        self.assertEqual([l["target_name"] for l in LINES.lines], ["a"])
        self.assertEqual(self.count(la.TABLE_NAME), 1)

    def test_commit_of_aborted_transaction_prints_nothing(self) -> None:
        # PostgreSQL answers COMMIT in an aborted transaction with ROLLBACK, no error.
        with Session(self.engine) as s:
            s.add(Thing(name="lost"))
            s.flush()
            with self.assertRaises(sa.exc.DBAPIError):
                s.execute(sa.text("SELECT 1/0"))
            s.commit()
        self.assertEqual((self.count("things"), self.count(la.TABLE_NAME)), (0, 0))
        self.assertEqual(LINES.lines, [])

    def test_append_only(self) -> None:
        with Session(self.engine) as s:
            s.add(Thing(name="a"))
            s.commit()
        for sql in ("UPDATE", "DELETE FROM", "TRUNCATE"):
            stmt = {"UPDATE": f"UPDATE {la.TABLE_NAME} SET outcome='x'",
                    "DELETE FROM": f"DELETE FROM {la.TABLE_NAME}",
                    "TRUNCATE": f"TRUNCATE {la.TABLE_NAME}"}[sql]
            with self.assertRaisesRegex(sa.exc.DBAPIError, "append-only"):
                with self.engine.begin() as conn:
                    conn.exec_driver_sql(stmt)


class PoolReuseTest(unittest.TestCase):
    """A real pool: joined-session records never reach the next checkout."""

    def test_checkin_drops_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _sqlite_engine(os.path.join(tmp, "m.db"))
            engine = engine.execution_options()  # same pool
            engine.pool = sa.pool.QueuePool(engine.pool._creator, pool_size=1)
            Base.metadata.create_all(engine)
            la.audit_table.create(engine)
            LINES.lines.clear()
            with mock.patch.object(la, "_is_metadata", return_value=True):
                with self.assertLogs(la.logger, "WARNING") as logs:
                    with engine.connect() as conn:
                        outer = conn.begin()
                        with Session(bind=conn) as joined:
                            joined.add(Thing(name="raw"))
                            joined.commit()
                        outer.commit()  # a raw commit: no owning session saw it
                self.assertIn("without a stdout line", logs.output[0])
                with engine.connect() as conn:
                    self.assertNotIn(la._DEFERRED, conn.info)
                with Session(engine) as s:
                    s.add(Thing(name="next"))
                    s.commit()
            self.assertEqual([l["target_name"] for l in LINES.lines], ["next"])
            engine.dispose()


class RedactionTest(unittest.TestCase):
    def test_uri_password_and_query_credentials(self) -> None:
        got = la._value(
            "sqlalchemy_uri",
            None,
            "postgresql://u:pw1@h/db?password=pw2&sslmode=require&api_token=pw3",
        )
        for secret in ("pw1", "pw2", "pw3"):
            self.assertNotIn(secret, got)
        self.assertIn("sslmode=require", got)
        self.assertEqual(la._value("sqlalchemy_uri", None, "sqlite://"), "sqlite://")
        odbc = "mssql+pyodbc:///?odbc_connect=DRIVER%3D%7Bx%7D%3BUID%3Du%3BPwd%3Dpw4"
        self.assertNotIn("pw4", la._value("sqlalchemy_uri", None, odbc))

    def test_nested_credentials_in_json_text_and_values(self) -> None:
        extra = json.dumps(
            {"engine_params": {"connect_args": {"password": "pw", "sslmode": "verify-full"}}}
        )
        got = la._value("extra", None, extra)
        self.assertNotIn('"pw"', got)
        self.assertIn(la.REDACTED, got)
        self.assertIn("verify-full", got)
        got = la._value("params", None, {"a": [{"secret_key": "s1"}], "b": 1})
        self.assertNotIn("s1", got)
        self.assertEqual(la._value("name", None, "{not json"), "{not json")
        self.assertEqual(la._value("password_hash", None, "x"), la.REDACTED)


class NodeTest(unittest.TestCase):
    def test_order(self) -> None:
        env = {"LAURETTA_AUDIT_NODE": "n1", "DATABASE_APPLICATION_NAME": "superset-ha-b"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(la._node_name(), "n1")
        with mock.patch.dict(os.environ, {**env, "LAURETTA_AUDIT_NODE": " "}):
            self.assertEqual(la._node_name(), "superset-ha-b")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(la._node_name(), la.socket.gethostname())


if __name__ == "__main__":
    unittest.main()
