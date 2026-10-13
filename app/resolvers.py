"""Resolvers. Authorization lives in one place (`_scope`) so no query can forget it."""
import base64
from pathlib import Path

from graphql import GraphQLError, build_schema, graphql_sync

SCHEMA = build_schema((Path(__file__).parent / "schema.graphql").read_text())
MAX_PAGE = 100

# A lead only moves forward through the pipeline, or to LOST. SOLD and LOST are final.
TRANSITIONS = {
    "NEW": {"CONTACTED", "LOST"},
    "CONTACTED": {"QUOTED", "LOST"},
    "QUOTED": {"SOLD", "LOST"},
    "SOLD": set(),
    "LOST": set(),
}

# One query returns the lead and its owner, so a page of leads never runs a query per row.
LEAD_SELECT = """
SELECT l.id, l.full_name, l.state, l.product, l.status, l.created_at,
       a.id AS a_id, a.name AS a_name, a.role AS a_role
FROM lead l LEFT JOIN agent a ON a.id = l.agent_id
"""


def _error(message, code):
    return GraphQLError(message, extensions={"code": code})


def _lead(row):
    agent = {"id": row["a_id"], "name": row["a_name"], "role": row["a_role"]} if row["a_id"] else None
    return {"id": row["id"], "fullName": row["full_name"], "state": row["state"], "product": row["product"],
            "status": row["status"], "createdAt": row["created_at"], "agent": agent}


def _scope(me):
    """SQL filter for the leads this caller may see."""
    if me["role"] == "MANAGER":
        return "1=1", []
    return "(l.agent_id IS NULL OR l.agent_id = ?)", [me["id"]]


def _cursor(lead_id):
    return base64.urlsafe_b64encode(f"lead:{lead_id}".encode()).decode()


def _from_cursor(cursor):
    try:
        kind, value = base64.urlsafe_b64decode(cursor.encode()).decode().split(":")
        assert kind == "lead"
        return int(value)
    except Exception:
        raise _error("invalid cursor", "BAD_INPUT") from None


class Root:
    """Query and Mutation fields. graphql-core passes arguments as keyword arguments."""

    def me(self, info):
        return info.context["me"]

    def leads(self, info, status=None, first=20, after=None):
        if not 1 <= first <= MAX_PAGE:
            raise _error(f"first must be between 1 and {MAX_PAGE}", "BAD_INPUT")
        db, me = info.context["db"], info.context["me"]
        where, args = _scope(me)
        if status:
            where += " AND l.status = ?"
            args.append(status)
        total = db.execute(f"SELECT COUNT(*) FROM lead l WHERE {where}", args).fetchone()[0]
        page_where, page_args = where, list(args)
        if after:
            page_where += " AND l.id > ?"
            page_args.append(_from_cursor(after))
        # Fetch one extra row to learn whether another page exists without a second query.
        rows = db.execute(f"{LEAD_SELECT} WHERE {page_where} ORDER BY l.id LIMIT ?", page_args + [first + 1]).fetchall()
        page = rows[:first]
        return {
            "edges": [{"cursor": _cursor(r["id"]), "node": _lead(r)} for r in page],
            "pageInfo": {"endCursor": _cursor(page[-1]["id"]) if page else None, "hasNextPage": len(rows) > first},
            "totalCount": total,
        }

    def lead(self, info, id):
        db, me = info.context["db"], info.context["me"]
        where, args = _scope(me)
        # A lead owned by someone else looks exactly like a missing one, so ids cannot be probed.
        row = db.execute(f"{LEAD_SELECT} WHERE l.id = ? AND {where}", [id] + args).fetchone()
        return _lead(row) if row else None

    def pipeline(self, info):
        db, me = info.context["db"], info.context["me"]
        where, args = _scope(me)
        rows = db.execute(f"SELECT l.status, COUNT(*) n FROM lead l WHERE {where} GROUP BY l.status", args).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        return [{"status": s, "count": counts.get(s, 0)} for s in TRANSITIONS]

    def claimLead(self, info, id):
        db, me = info.context["db"], info.context["me"]
        if me["role"] != "AGENT":
            raise _error("only agents can claim leads", "FORBIDDEN")
        # The WHERE clause is the lock: only one UPDATE can find the row still unowned.
        changed = db.execute("UPDATE lead SET agent_id = ? WHERE id = ? AND agent_id IS NULL", (me["id"], id)).rowcount
        if not changed:
            exists = db.execute("SELECT 1 FROM lead WHERE id = ?", (id,)).fetchone()
            raise _error("lead already claimed" if exists else "lead not found", "CONFLICT" if exists else "NOT_FOUND")
        return _lead(db.execute(f"{LEAD_SELECT} WHERE l.id = ?", (id,)).fetchone())

    def updateLeadStatus(self, info, id, status):
        db, me = info.context["db"], info.context["me"]
        row = db.execute("SELECT status, agent_id FROM lead WHERE id = ?", (id,)).fetchone()
        if not row or (me["role"] == "AGENT" and row["agent_id"] not in (None, me["id"])):
            raise _error("lead not found", "NOT_FOUND")
        if row["agent_id"] != me["id"]:
            raise _error("claim the lead before changing its status", "FORBIDDEN")
        if status not in TRANSITIONS[row["status"]]:
            raise _error(f"cannot move a lead from {row['status']} to {status}", "INVALID_TRANSITION")
        # Guard on the old status so two updates racing from the same state cannot both apply.
        changed = db.execute("UPDATE lead SET status = ? WHERE id = ? AND status = ?", (status, id, row["status"])).rowcount
        if not changed:
            raise _error("lead changed while you were editing it", "CONFLICT")
        return _lead(db.execute(f"{LEAD_SELECT} WHERE l.id = ?", (id,)).fetchone())


def execute(db, agent_id, query, variables=None):
    """Run one GraphQL operation as the given agent. Returns (data, errors)."""
    row = db.execute("SELECT id, name, role FROM agent WHERE id = ?", (agent_id,)).fetchone()
    if not row:
        return None, [{"message": "not signed in", "extensions": {"code": "UNAUTHENTICATED"}}]
    result = graphql_sync(SCHEMA, query, root_value=Root(), context_value={"db": db, "me": dict(row)},
                          variable_values=variables)
    return result.data, [e.formatted for e in result.errors or []]
