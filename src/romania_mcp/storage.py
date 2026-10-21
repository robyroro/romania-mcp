"""Local reports, watchlists and resumable supplier checks, separate from the HTTP cache."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .evidence import now


class Store:
    def __init__(self, path=None):
        self.path = str(path or os.environ.get("ROMANIA_MCP_DATA_PATH") or
                        Path.home() / ".local" / "share" / "romania-mcp" / "data.sqlite3")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                create table if not exists snapshots (
                    id integer primary key, cui integer not null, saved_at text not null, payload text not null);
                create index if not exists snapshots_cui on snapshots(cui, id desc);
                create table if not exists watches (
                    cui integer primary key, interval_hours integer not null, enabled integer not null,
                    next_due text not null, last_snapshot integer);
                create table if not exists jobs (
                    id text primary key, created_at text not null, years integer not null);
                create table if not exists job_items (
                    job_id text not null, position integer not null, cui integer not null,
                    state text not null default 'pending', payload text, primary key(job_id, cui));
                create table if not exists datasets (
                    id text primary key, kind text not null, source_url text not null, published_on text not null,
                    period_start text, period_end text, imported_at text not null, row_count integer not null,
                    skipped integer not null, sha256 text not null, license text);
                create table if not exists companies (
                    dataset_id text not null, cui integer not null, name text not null, name_key text not null,
                    county text, county_key text, caen text, registration_number text, address text,
                    primary key(dataset_id, cui));
                create index if not exists company_name on companies(name_key);
                create index if not exists company_registration on companies(registration_number);
                create table if not exists company_caen (
                    dataset_id text not null, registration_number text not null, caen text not null,
                    version text, primary key(dataset_id, registration_number, caen, version));
                create index if not exists caen_lookup on company_caen(registration_number, caen);
                create table if not exists contracts (
                    dataset_id text not null, record_id text not null, supplier_cui integer not null,
                    supplier text, buyer text, title text, signed_on text, value text, currency text,
                    primary key(dataset_id, record_id, supplier_cui));
                create index if not exists contract_supplier on contracts(supplier_cui, signed_on);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys=on")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, report):
        with self.connect() as db:
            cursor = db.execute("insert into snapshots(cui,saved_at,payload) values (?,?,?)",
                                (report["cui"], now(), json.dumps(report, ensure_ascii=False)))
            return cursor.lastrowid

    def history(self, cui, limit=20):
        with self.connect() as db:
            rows = db.execute("select id,saved_at,payload from snapshots where cui=? order by id desc limit ?",
                              (cui, limit)).fetchall()
        return [{"id": r["id"], "saved_at": r["saved_at"], "report": json.loads(r["payload"])} for r in rows]

    def watch(self, cui, interval_hours=24, enabled=True):
        if not 1 <= interval_hours <= 8760:
            raise ValueError("interval_hours must be between 1 and 8760")
        with self.connect() as db:
            db.execute("""insert into watches values (?,?,?,?,NULL)
                on conflict(cui) do update set interval_hours=excluded.interval_hours,
                enabled=excluded.enabled,next_due=excluded.next_due""", (cui, interval_hours, int(enabled), now()))
        return {"cui": cui, "interval_hours": interval_hours, "enabled": enabled}

    def watches(self, due=False, limit=100):
        with self.connect() as db:
            query = "select * from watches"
            args = []
            if due:
                query += " where enabled=1 and next_due<=?"
                args.append(now())
            return [dict(r) for r in db.execute(query + " order by next_due,cui limit ?", (*args, limit))]

    def create_job(self, cuis, years):
        job_id = uuid4().hex
        with self.connect() as db:
            db.execute("insert into jobs values (?,?,?)", (job_id, now(), years))
            db.executemany("insert into job_items(job_id,position,cui) values (?,?,?)",
                           [(job_id, i, cui) for i, cui in enumerate(dict.fromkeys(cuis))])
        return job_id

    def job(self, job_id):
        with self.connect() as db:
            job = db.execute("select * from jobs where id=?", (job_id,)).fetchone()
            if job is None:
                raise ValueError("Unknown supplier check")
            items = [dict(r) for r in db.execute(
                "select position,cui,state,payload from job_items where job_id=? order by position", (job_id,))]
        return {**dict(job), "items": items, "total": len(items),
                "completed": sum(r["state"] == "done" for r in items),
                "pending": sum(r["state"] == "pending" for r in items)}

    def finish_item(self, job_id, cui, report):
        with self.connect() as db:
            db.execute("update job_items set state='done',payload=? where job_id=? and cui=?",
                       (json.dumps(report, ensure_ascii=False), job_id, cui))
