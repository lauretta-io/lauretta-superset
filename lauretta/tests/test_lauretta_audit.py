# mypy: ignore-errors
# unittest, not pytest: these run inside the Superset image, which has no pytest
# (PT009/PT027). S608: f-string SQL here only names this file's own tables.
# ruff: noqa: PT009, PT027, S608
"""Tests of the audit trail's transaction rules
(docker/pythonpath_dev/lauretta_audit.py), plus the gap fixes (GapsTest).

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

import lauretta_audit as la
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, Session

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

    def test_outermost_savepoint_release_still_prints(self) -> None:
        # Codex r5: a check on sqlite3's in_transaction dropped these lines.
        with Session(self.engine) as s:
            with s.begin_nested():
                s.add(Thing(name="sp"))
            s.commit()
        self.assertEqual(self.names(), ["sp"])
        self.assertEqual(self.printed(), ["sp"])

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
        printed = sorted((line["target_type"], line["id"]) for line in LINES.lines)
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
        self.assertEqual(
            self.printed(), []
        )  # joined writes with no owning session: no line

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
                conn.dialect,
                "do_commit",
                side_effect=sa.exc.OperationalError("c", {}, Exception("server gone")),
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
        for sql in (
            f"UPDATE {la.TABLE_NAME} SET action='x'",
            f"DELETE FROM {la.TABLE_NAME}",
        ):
            with self.assertRaisesRegex(sa.exc.IntegrityError, "append-only"):
                with self.engine.begin() as conn:
                    conn.exec_driver_sql(sql)


class GapsTest(unittest.TestCase):
    """Bulk statements, raw-SQL side effects in a flush, idle-session sweep."""

    # The same fixture, without inheriting (and re-running) the tests above.
    setUp = AuditTransactionTest.setUp
    tearDown = AuditTransactionTest.tearDown
    rows = AuditTransactionTest.rows
    names = AuditTransactionTest.names

    def _seed(self, *names: str) -> None:
        with Session(self.engine) as s:
            s.add_all([Thing(name=n) for n in names])
            s.commit()
        LINES.lines.clear()

    def test_bulk_delete_is_recorded_with_its_rows(self) -> None:
        self._seed("a", "b", "c")
        with Session(self.engine) as s:
            n = (
                s.query(Thing)
                .filter(Thing.name.in_(["a", "b"]))
                .delete(synchronize_session=False)
            )
            self.assertEqual(n, 2)
            self.assertEqual(LINES.lines, [])  # not committed yet
            s.commit()
        row = self.rows()[-1]
        self.assertEqual(row["action"], "data.bulk_delete")
        self.assertEqual(row["target_type"], "things")
        details = json.loads(row["details"])
        self.assertEqual(details["rows"], 2)
        self.assertEqual(sorted(details["affected"]), [1, 2])
        self.assertIn("name IN", details["where"])
        self.assertEqual([line["action"] for line in LINES.lines], ["data.bulk_delete"])
        self.assertEqual(la._sign(row, la._state.key), row["row_hmac"])

    def test_bulk_update_records_values_and_rollback_prints_nothing(self) -> None:
        self._seed("a", "b")
        with Session(self.engine) as s:
            s.execute(sa.update(Thing).where(Thing.name == "a").values(name="z"))
            s.rollback()
        self.assertEqual(LINES.lines, [])
        self.assertEqual(self.names(), ["a", "b"])
        with Session(self.engine) as s:
            s.query(Thing).filter(Thing.name == "a").update(
                {Thing.name: "z"}, synchronize_session=False
            )
            s.commit()
        details = json.loads(self.rows()[-1]["details"])
        self.assertEqual(details["values"], {"name": "z"})
        self.assertEqual(details["affected"], [1])
        self.assertEqual([line["action"] for line in LINES.lines], ["data.bulk_update"])

    def test_bulk_statement_matching_nothing_is_not_recorded(self) -> None:
        self._seed("a")
        before = len(self.rows())
        with Session(self.engine) as s:
            s.query(Thing).filter(Thing.name == "nope").delete(
                synchronize_session=False
            )
            s.commit()
        self.assertEqual(len(self.rows()), before)

    def test_side_records_join_the_flush_that_made_them(self) -> None:
        # Stands in for Superset's raw-SQL permission removal inside a delete flush.
        def side(mapper, connection, target):  # noqa: ANN001
            la._stash_side(
                connection,
                [la._record("role", "role.permission_remove", target_name="Gamma")],
            )

        event.listen(Thing, "after_delete", side)
        self.addCleanup(event.remove, Thing, "after_delete", side)
        self._seed("a")
        with Session(self.engine) as s:
            s.delete(s.query(Thing).one())
            s.flush()
            s.rollback()
        self.assertEqual(LINES.lines, [])
        with Session(self.engine) as s:
            s.delete(s.query(Thing).one())
            s.commit()
        self.assertEqual(
            [r["action"] for r in self.rows()[-2:]],
            ["role.permission_remove", "data.delete"],
        )
        self.assertEqual(
            [line["action"] for line in LINES.lines],
            ["role.permission_remove", "data.delete"],
        )

    def test_idle_sweep_records_one_logout_per_session(self) -> None:
        from datetime import datetime, timedelta, timezone

        la.sessions_table.create(self.engine)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        life = timedelta(minutes=30)
        rows = [
            ("old", now - timedelta(hours=2), now - timedelta(minutes=40)),
            ("fresh", now - timedelta(hours=1), now - timedelta(minutes=5)),
            ("edge", now - timedelta(hours=1), now - timedelta(minutes=31)),  # in grace
        ]
        with self.engine.begin() as conn:
            for sid, started, seen in rows:
                conn.execute(
                    la.sessions_table.insert().values(
                        sid=sid,
                        user_id=7,
                        username="ann",
                        ip_address="198.51.100.4",
                        node="n1",
                        started_at=started,
                        last_seen=seen,
                    )
                )
        self.assertEqual(la.sweep_sessions(self.engine, life, now), 1)
        self.assertEqual(la.sweep_sessions(self.engine, life, now), 0)  # claimed once
        row = self.rows()[-1]
        self.assertEqual(row["action"], "auth.logout")
        self.assertEqual(row["actor_username"], "ann")
        self.assertEqual(row["ip_address"], "198.51.100.4")
        self.assertEqual(row["request"], "session idle timeout")
        self.assertEqual(json.loads(row["details"])["reason"], "idle_timeout")
        self.assertEqual(
            la._aware(row["event_time"]), now - timedelta(minutes=40) + life
        )
        self.assertEqual(la._sign(row, la._state.key), row["row_hmac"])
        with self.engine.connect() as conn:
            ended = dict(
                conn.execute(
                    sa.select(la.sessions_table.c.sid, la.sessions_table.c.end_reason)
                ).fetchall()
            )
        self.assertEqual(ended, {"old": "idle_timeout", "fresh": None, "edge": None})
        self.assertEqual([line["action"] for line in LINES.lines], ["auth.logout"])


class BulkWritesTest(unittest.TestCase):
    """Writes that skip after_flush: bulk mappings (dataset columns and metrics) and
    ORM INSERT statements (asset import links)."""

    setUp = AuditTransactionTest.setUp
    tearDown = AuditTransactionTest.tearDown
    rows = AuditTransactionTest.rows
    names = AuditTransactionTest.names
    _seed = GapsTest._seed

    def test_bulk_mappings_record_each_row(self) -> None:
        self._seed("a", "b")
        before = len(self.rows())
        with Session(self.engine) as s:
            s.bulk_insert_mappings(
                Thing, [{"name": "c", "extra": '{"password": "hunter2"}'}]
            )
            a = s.get(Thing, 1)
            # DatasetDAO passes a whole instance __dict__ plus the new values.
            s.bulk_update_mappings(
                Thing, [{**a.__dict__, "name": "a2"}, {"id": 2, "name": "b"}]
            )
            self.assertEqual(LINES.lines, [])  # not committed yet
            s.commit()
        rows = self.rows()[before:]
        self.assertEqual([r["action"] for r in rows], ["data.create", "data.update"])
        created, updated = (json.loads(r["details"]) for r in rows)
        self.assertEqual(
            created["values"], {"name": "c", "extra": '{"password": "[REDACTED]"}'}
        )
        self.assertEqual(rows[0]["target_name"], "c")
        # Only the row that changed, and only its changed column; b was unchanged.
        self.assertEqual(updated, {"changes": {"name": {"old": "a", "new": "a2"}}})
        self.assertEqual(rows[1]["target_id"], "1")
        self.assertEqual(self.names(), ["a2", "b", "c"])
        self.assertEqual(
            [line["action"] for line in LINES.lines], ["data.create", "data.update"]
        )
        for row in rows:
            self.assertEqual(la._sign(row, la._state.key), row["row_hmac"])

    def test_bulk_mappings_rolled_back_print_nothing(self) -> None:
        self._seed("a")
        before = len(self.rows())
        with Session(self.engine) as s:
            s.bulk_insert_mappings(Thing, [{"name": "c"}])
            s.bulk_update_mappings(Thing, [{"id": 1, "name": "z"}])
            s.rollback()
        self.assertEqual(LINES.lines, [])
        self.assertEqual(len(self.rows()), before)
        self.assertEqual(self.names(), ["a"])

    def test_orm_insert_statements_are_recorded(self) -> None:
        with Session(self.engine) as s:
            s.execute(sa.insert(Thing.__table__).values([{"name": "x"}, {"name": "y"}]))
            s.execute(sa.insert(Thing.__table__), [{"name": "p"}, {"name": "q"}])
            s.commit()
        rows = self.rows()
        self.assertEqual([r["action"] for r in rows], ["data.bulk_create"] * 2)
        self.assertEqual(
            [json.loads(r["details"]) for r in rows],
            [
                {"rows": 2, "values": [{"name": "x"}, {"name": "y"}]},
                {"rows": 2, "values": [{"name": "p"}, {"name": "q"}]},
            ],
        )
        self.assertEqual(len(LINES.lines), 2)

    def test_sweep_skips_a_session_touched_since_its_select(self) -> None:
        from datetime import datetime, timedelta, timezone

        la.sessions_table.create(self.engine)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(
                la.sessions_table.insert().values(
                    sid="old",
                    user_id=7,
                    username="ann",
                    started_at=now - timedelta(hours=2),
                    last_seen=now - timedelta(minutes=40),
                )
            )

        # Another worker's touch lands between the sweep's SELECT and its claim.
        touched: list[bool] = []

        def touch(conn, cursor, statement, *_):  # noqa: ANN001
            if statement.startswith("UPDATE lauretta_audit_sessions") and not touched:
                touched.append(True)
                seen = (now - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S.%f")
                cursor.execute(
                    "UPDATE lauretta_audit_sessions SET last_seen = ? "
                    "WHERE sid = 'old'",
                    (seen,),
                )

        event.listen(self.engine, "before_cursor_execute", touch)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", touch)
        self.assertEqual(la.sweep_sessions(self.engine, timedelta(minutes=30), now), 0)
        with self.engine.connect() as conn:
            ended = conn.execute(sa.select(la.sessions_table.c.ended_at)).scalar()
        self.assertIsNone(ended)
        self.assertEqual(LINES.lines, [])


class DmlVerdictTest(unittest.TestCase):
    """Which finished SQL Lab queries become data.sql_dml."""

    TEMPLATE = "{% set t = 'items' %}DELETE FROM {{ t }}; SELECT 1"

    def verdict(self, sql, executed, allow_dml=False, status="success"):  # noqa: ANN001
        return la._dml_verdict(sql, executed, "postgresql", allow_dml, status)

    def test_plain_write_and_read(self) -> None:
        self.assertEqual(
            self.verdict("DELETE FROM t", None, status="failed"), "mutation"
        )
        self.assertIsNone(self.verdict("SELECT 1", "SELECT 1", allow_dml=True))

    def test_template_that_may_have_written(self) -> None:
        # Only the final SELECT is in executed_sql; the template does not parse.
        self.assertEqual(
            self.verdict(self.TEMPLATE, "SELECT 1", allow_dml=True), "template"
        )
        # Refused by Superset (no DML on this database): the attempt is recorded.
        self.assertEqual(self.verdict(self.TEMPLATE, None, status="failed"), "template")

    def test_template_that_cannot_have_written(self) -> None:
        # Superset checks the rendered script: success without DML wrote nothing.
        self.assertIsNone(self.verdict(self.TEMPLATE, "SELECT 1"))


class MachineLoginTest(unittest.TestCase):
    """login_user() without an audited auth method: Superset's machine auth."""

    def setUp(self) -> None:
        import flask

        self.flask = flask
        self.app = flask.Flask(__name__)
        self.user = mock.Mock(is_authenticated=True, id=5, username="reporter")

    def test_recorded_as_a_machine_login_and_tracked(self) -> None:
        with (
            self.app.test_request_context("/login"),
            mock.patch.object(la, "record_event") as record,
            mock.patch.object(la, "_start_tracking") as track,
            mock.patch.object(la._state, "tracking", True),
        ):
            la._on_login(None, user=self.user)
        details = record.call_args.kwargs["details"]
        self.assertEqual(record.call_args.args, ("auth", "auth.login"))
        self.assertEqual(details["method"], "machine")
        # The tracked session is the one the login names: the logout will match.
        track.assert_called_once_with(self.user, details["session"])

    def test_audited_login_is_not_recorded_twice(self) -> None:
        with (
            self.app.test_request_context("/login"),
            mock.patch.object(la, "record_event") as record,
            mock.patch.object(la, "_start_tracking") as track,
            mock.patch.object(la._state, "tracking", True),
        ):
            self.flask.g.lauretta_audit_sid = "abc"
            la._on_login(None, user=self.user)
            self.assertNotIn("lauretta_audit_sid", self.flask.g)
        record.assert_not_called()
        track.assert_called_once_with(self.user, "abc")

    def test_anonymous_login_is_ignored(self) -> None:
        from flask_login import AnonymousUserMixin

        with (
            self.app.test_request_context("/embedded/x"),
            mock.patch.object(la, "record_event") as record,
            mock.patch.object(la, "_start_tracking") as track,
            mock.patch.object(la._state, "tracking", True),
        ):
            la._on_login(None, user=AnonymousUserMixin())
        record.assert_not_called()
        track.assert_not_called()


class FileSinkTest(unittest.TestCase):
    """The SIEM file: one JSON line per committed event, appended."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(setattr, la._state, "file_path", None)
        self.addCleanup(setattr, la._state, "file_warned", False)

    def test_lines_are_appended(self) -> None:
        path = os.path.join(self.tmp.name, "audit", "audit.jsonl")
        la._state.file_path = path
        la._emit_stdout([la._record("auth", "auth.login", target_name="a")])
        la._emit_stdout([la._record("auth", "auth.logout", target_name="a")])
        with open(path) as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([x["action"] for x in lines], ["auth.login", "auth.logout"])
        self.assertTrue(all(x["lauretta_audit"] == 1 for x in lines))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)

    def test_rotation_keeps_the_configured_backups(self) -> None:
        path = os.path.join(self.tmp.name, "audit.jsonl")
        la._state.file_path = path
        self.addCleanup(setattr, la._state, "file_max_bytes", la._state.file_max_bytes)
        self.addCleanup(setattr, la._state, "file_backups", la._state.file_backups)
        la._state.file_max_bytes, la._state.file_backups = 1500, 3
        for i in range(40):
            la._emit_stdout([la._record("auth", "auth.login", target_name=f"u{i}")])
        names = sorted(os.listdir(self.tmp.name))
        self.assertEqual(
            names, ["audit.jsonl", "audit.jsonl.1", "audit.jsonl.2", "audit.jsonl.3"]
        )
        seen = []
        for name in reversed(names[1:]):  # oldest first
            with open(os.path.join(self.tmp.name, name)) as f:
                seen += [json.loads(line)["target_name"] for line in f]
        with open(path) as f:
            seen += [json.loads(line)["target_name"] for line in f]
        self.assertEqual(seen[-1], "u39")
        # in order, with no gaps among what is kept (the oldest file was dropped)
        first = int(seen[0][1:])
        self.assertEqual(seen, [f"u{i}" for i in range(first, 40)])
        for name in names:
            self.assertLess(
                os.path.getsize(os.path.join(self.tmp.name, name)), 1500 + 600
            )

    def test_concurrent_writers_with_rotation_lose_nothing(self) -> None:
        import multiprocessing

        path = os.path.join(self.tmp.name, "audit.jsonl")

        def writer(n: int) -> None:
            la._state.file_path = path
            la._state.file_max_bytes, la._state.file_backups = 20000, 1000
            for i in range(150):
                la._emit_stdout(
                    [la._record("auth", "auth.login", target_name=f"{n}-{i}")]
                )

        ctx = multiprocessing.get_context("fork")
        procs = [ctx.Process(target=writer, args=(n,)) for n in range(4)]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(60)
            self.assertEqual(proc.exitcode, 0)
        names = os.listdir(self.tmp.name)
        self.assertGreater(len(names), 3)  # it did rotate
        seen = []
        for name in names:
            with open(os.path.join(self.tmp.name, name)) as f:
                seen += [json.loads(line)["target_name"] for line in f]  # no torn lines
        self.assertEqual(
            sorted(seen), sorted(f"{n}-{i}" for n in range(4) for i in range(150))
        )

    def test_unwritable_file_never_raises(self) -> None:
        la._state.file_path = "/proc/no/such/dir/audit.jsonl"
        la._emit_stdout([la._record("auth", "auth.login")])
        self.assertTrue(la._state.file_warned)


class RepeatedFailureTest(unittest.TestCase):
    setUp = AuditTransactionTest.setUp
    tearDown = AuditTransactionTest.tearDown
    rows = AuditTransactionTest.rows

    def _fail(self, when, username="bob", ip="198.51.100.9") -> None:  # noqa: ANN001
        rec = la._record(
            "auth",
            "auth.login",
            outcome="failure",
            target_name=username,
            overrides={"event_time": when},
            context={
                "actor_id": None,
                "actor_username": username,
                "ip_address": ip,
                "user_agent": None,
                "request": "POST /login/",
            },
        )
        with self.engine.begin() as conn:
            la._insert(conn, [rec])

    def test_threshold_window_and_once_per_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        self._fail(now - timedelta(minutes=30))  # outside the 10-minute window
        for i in range(4):
            self._fail(now - timedelta(minutes=5 - i))
        self.assertEqual(
            la.check_repeated_failures("bob", "198.51.100.9", self.engine, now), 0
        )
        self._fail(now - timedelta(seconds=10), username="carol")  # same address
        got = la.check_repeated_failures("carol", "198.51.100.9", self.engine, now)
        self.assertEqual(got, 1)  # the address: 5 failures; carol has only 1
        self._fail(now - timedelta(seconds=5))
        self.assertEqual(
            la.check_repeated_failures("bob", "198.51.100.9", self.engine, now), 1
        )
        self.assertEqual(
            la.check_repeated_failures("bob", "198.51.100.9", self.engine, now), 0
        )
        alerts = [r for r in self.rows() if r["action"] == "auth.repeated_failure"]
        self.assertEqual(
            sorted((r["target_type"], r["target_name"]) for r in alerts),
            [("ip_address", "198.51.100.9"), ("username", "bob")],
        )
        by_ip = next(r for r in alerts if r["target_type"] == "ip_address")
        self.assertEqual(json.loads(by_ip["details"])["usernames"], ["bob", "carol"])
        self.assertEqual(la._sign(by_ip, la._state.key), by_ip["row_hmac"])


class ReportTest(unittest.TestCase):
    setUp = AuditTransactionTest.setUp
    tearDown = AuditTransactionTest.tearDown

    def test_csv_filters_and_seal_check(self) -> None:
        import csv
        import io
        from datetime import datetime, timedelta, timezone

        day = datetime(2026, 3, 1, tzinfo=timezone.utc)
        recs = [
            la._record(
                "auth",
                "auth.login",
                target_name="a",
                overrides={"event_time": day + timedelta(hours=h), "actor_username": u},
            )
            for h, u in ((1, "ann"), (2, "bob"), (30, "ann"))
        ]
        with self.engine.begin() as conn:
            la._insert(conn, recs)
            conn.exec_driver_sql(f"DROP TRIGGER {la.TABLE_NAME}_no_update")
            conn.exec_driver_sql(
                f"UPDATE {la.TABLE_NAME} SET target_name='x' WHERE id={recs[1]['id']}"
            )
        out = io.StringIO()
        keys = {la._state.key_id: la._state.key}
        with mock.patch.object(la, "record_event") as recorded:
            n = la.report(
                out,
                since=day,
                until=day + timedelta(days=1),
                engine=self.engine,
                keys=keys,
            )
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(n, 2)
        self.assertEqual([r["actor_username"] for r in rows], ["ann", "bob"])
        self.assertEqual([r["seal_ok"] for r in rows], ["yes", "NO"])
        self.assertEqual(recorded.call_args.args[:2], ("audit", "audit.report"))
        self.assertEqual(recorded.call_args.kwargs["details"]["rows"], 2)
        with mock.patch.object(la, "record_event"):
            n = la.report(io.StringIO(), user="ann", engine=self.engine, keys=keys)
        self.assertEqual(n, 2)


class PrivilegedRequestTest(unittest.TestCase):
    """access.request for every request by a privileged account."""

    def setUp(self) -> None:
        import flask

        self.app = flask.Flask("t")
        self.app.config["LAURETTA_AUDIT_PRIVILEGED_ROLES"] = ["Admin"]

        @self.app.route("/<path:p>", methods=["GET", "POST"])
        def anything(p):  # noqa: ANN001, ANN202
            return "ok"

        self.app.after_request(la._audit_privileged)
        self.client = self.app.test_client()
        patcher = mock.patch.object(la, "record_event")
        self.recorded = patcher.start()
        self.addCleanup(patcher.stop)

    def _as(self, *roles: str):  # noqa: ANN202
        user = mock.Mock(is_authenticated=True, roles=[mock.Mock() for _ in roles])
        for role, name in zip(user.roles, roles, strict=True):
            role.name = name
        return mock.patch("flask_login.current_user", user)

    def details(self) -> dict:
        return self.recorded.call_args.kwargs["details"]

    def test_admin_requests_are_recorded(self) -> None:
        with self._as("Admin", "Gamma"):
            self.client.get("/api/v1/dashboard/?q=(page:0)&access_token=s3cret")
            d = self.details()
            self.assertEqual(d["path"], "/api/v1/dashboard/")
            self.assertEqual(d["args"], {"q": "(page:0)", "access_token": la.REDACTED})
            self.assertEqual(d["roles"], ["Admin"])
            self.client.post(
                "/api/v1/sqllab/execute/", json={"sql": "SELECT 1", "database_id": 2}
            )
            self.assertEqual(self.details()["sql"], "SELECT 1")
            self.client.post(
                "/api/v1/chart/data",
                data={
                    "form_data": json.dumps(
                        {"result_format": "csv", "result_type": "full"}
                    )
                },
            )
            self.assertEqual(self.details()["result_format"], "csv")
            calls = self.recorded.call_count
            self.client.get("/static/assets/x.js")
            self.client.post("/api/v1/log/")
            self.assertEqual(self.recorded.call_count, calls)  # quiet paths

    def test_other_users_are_not(self) -> None:
        with self._as("Gamma"):
            self.client.get("/api/v1/dashboard/")
        anon = mock.Mock(is_authenticated=False)
        with mock.patch("flask_login.current_user", anon):
            self.client.get("/api/v1/dashboard/")
        self.recorded.assert_not_called()


class NoReturningTest(unittest.TestCase):
    def test_audit_table_never_uses_returning(self) -> None:
        """raib-ha D104 (see PostgresTest.test_audit_insert_without_returning)."""
        self.assertFalse(la.audit_table.implicit_returning)


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
        self.assertEqual([line["target_name"] for line in LINES.lines], ["a"])
        self.assertEqual(self.count(la.TABLE_NAME), 1)

    def test_audit_insert_without_returning(self) -> None:
        """raib-ha D104: pgAudit logs reads of the audit table; INSERT ... RETURNING needs
        SELECT, so audit rows are inserted with the id from nextval() first, no RETURNING."""
        seen: list[str] = []

        def _sql(conn, cursor, statement, *args) -> None:  # noqa: ANN001
            seen.append(statement)

        event.listen(self.engine, "before_cursor_execute", _sql)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", _sql)
        record = {
            "event_time": la.datetime.now(la.timezone.utc), "category": "auth",
            "action": "auth.login", "outcome": "failure", "key_id": "k", "row_hmac": "h",
        }
        with self.engine.begin() as conn:
            la._insert(conn, [dict(record), dict(record)])
        inserts = [x for x in seen if x.lstrip().upper().startswith(f"INSERT INTO {la.TABLE_NAME.upper()}")]
        self.assertEqual(len(inserts), 2, seen)
        self.assertFalse([x for x in inserts if "RETURNING" in x.upper()], inserts)
        self.assertEqual(self.count(la.TABLE_NAME), 2)

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
            stmt = {
                "UPDATE": f"UPDATE {la.TABLE_NAME} SET outcome='x'",
                "DELETE FROM": f"DELETE FROM {la.TABLE_NAME}",
                "TRUNCATE": f"TRUNCATE {la.TABLE_NAME}",
            }[sql]
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
            self.assertEqual([line["target_name"] for line in LINES.lines], ["next"])
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
            {
                "engine_params": {
                    "connect_args": {"password": "pw", "sslmode": "verify-full"}
                }
            }
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
        env = {
            "LAURETTA_AUDIT_NODE": "n1",
            "DATABASE_APPLICATION_NAME": "superset-ha-b",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(la._node_name(), "n1")
        with mock.patch.dict(os.environ, {**env, "LAURETTA_AUDIT_NODE": " "}):
            self.assertEqual(la._node_name(), "superset-ha-b")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(la._node_name(), la.socket.gethostname())


if __name__ == "__main__":
    unittest.main()
