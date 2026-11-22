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
