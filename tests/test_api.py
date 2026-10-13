import json
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.resolvers import execute
from app.seed import demo_database
from app.server import make_handler

ANA, BEN, MIA = 1, 2, 3  # two agents and a manager; 30 open leads, 10 of Ana's, 5 of Ben's

LEADS = """query($status: LeadStatus, $first: Int, $after: String) {
  leads(status: $status, first: $first, after: $after) {
    totalCount pageInfo { endCursor hasNextPage }
    edges { cursor node { id fullName status agent { id name } } }
  }
}"""
CLAIM = "mutation($id: ID!) { claimLead(id: $id) { id agent { name } } }"
MOVE = "mutation($id: ID!, $s: LeadStatus!) { updateLeadStatus(id: $id, status: $s) { id status } }"


@pytest.fixture
def db():
    return demo_database()


def code(errors):
    return errors[0]["extensions"]["code"]


def test_agent_sees_open_pool_and_own_leads_only(db):
    data, errors = execute(db, ANA, LEADS, {"first": 100})
    assert not errors and data["leads"]["totalCount"] == 40
    owners = {e["node"]["agent"]["name"] if e["node"]["agent"] else None for e in data["leads"]["edges"]}
    assert owners == {None, "Ana Agent"}


def test_manager_sees_everything(db):
    data, _ = execute(db, MIA, LEADS, {"first": 100})
    assert data["leads"]["totalCount"] == 45


def test_another_agents_lead_looks_missing(db):
    bens = 41  # first of Ben's leads
    data, errors = execute(db, ANA, "query($id: ID!) { lead(id: $id) { id } }", {"id": bens})
    assert not errors and data["lead"] is None
    assert execute(db, BEN, "query($id: ID!) { lead(id: $id) { id } }", {"id": bens})[0]["lead"]["id"] == "41"


def test_cursor_pagination_walks_every_lead_once(db):
    seen, after = [], None
    while True:
        data, errors = execute(db, ANA, LEADS, {"first": 7, "after": after})
        assert not errors
        seen += [e["node"]["id"] for e in data["leads"]["edges"]]
        if not data["leads"]["pageInfo"]["hasNextPage"]:
            break
        after = data["leads"]["pageInfo"]["endCursor"]
    assert len(seen) == 40 and len(set(seen)) == 40


def test_status_filter_and_pipeline_counts_agree(db):
    data, _ = execute(db, ANA, LEADS, {"status": "QUOTED"})
    pipeline, _ = execute(db, ANA, "{ pipeline { status count } }")
    counts = {p["status"]: p["count"] for p in pipeline["pipeline"]}
    assert data["leads"]["totalCount"] == counts["QUOTED"] == 5
    assert counts == {"NEW": 30, "CONTACTED": 5, "QUOTED": 5, "SOLD": 0, "LOST": 0}


def test_a_page_of_leads_with_owners_costs_two_queries_not_one_per_row(db):
    execute(db, MIA, "{ me { id } }")
    before = db.query_count
    execute(db, MIA, LEADS, {"first": 45})
    assert db.query_count - before == 3  # caller lookup, count, page (owners joined in)


def test_bad_page_size_and_bad_cursor_are_rejected(db):
    assert code(execute(db, ANA, LEADS, {"first": 0})[1]) == "BAD_INPUT"
    assert code(execute(db, ANA, LEADS, {"first": 101})[1]) == "BAD_INPUT"
    assert code(execute(db, ANA, LEADS, {"after": "not-a-cursor"})[1]) == "BAD_INPUT"


def test_claim_assigns_the_lead(db):
    data, errors = execute(db, ANA, CLAIM, {"id": 1})
    assert not errors and data["claimLead"]["agent"]["name"] == "Ana Agent"
    assert code(execute(db, BEN, CLAIM, {"id": 1})[1]) == "CONFLICT"
    assert code(execute(db, BEN, CLAIM, {"id": 9999})[1]) == "NOT_FOUND"
    assert code(execute(db, MIA, CLAIM, {"id": 2})[1]) == "FORBIDDEN"


def test_two_agents_racing_for_the_same_leads_never_both_win(db):
    def claim(args):
        agent, lead = args
        return lead, agent, not execute(db, agent, CLAIM, {"id": lead})[1]

    attempts = [(agent, lead) for lead in range(1, 31) for agent in (ANA, BEN)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, attempts))
    winners = {}
    for lead, agent, ok in results:
        if ok:
            assert lead not in winners, f"lead {lead} was claimed twice"
            winners[lead] = agent
    assert len(winners) == 30


def test_status_moves_forward_only_and_only_for_the_owner(db):
    execute(db, ANA, CLAIM, {"id": 1})
    assert execute(db, ANA, MOVE, {"id": 1, "s": "CONTACTED"})[0]["updateLeadStatus"]["status"] == "CONTACTED"
    assert code(execute(db, ANA, MOVE, {"id": 1, "s": "SOLD"})[1]) == "INVALID_TRANSITION"
    assert code(execute(db, ANA, MOVE, {"id": 1, "s": "NEW"})[1]) == "INVALID_TRANSITION"
    assert code(execute(db, BEN, MOVE, {"id": 1, "s": "QUOTED"})[1]) == "NOT_FOUND"   # Ana's lead is invisible to Ben
    assert code(execute(db, BEN, MOVE, {"id": 2, "s": "CONTACTED"})[1]) == "FORBIDDEN"  # open lead, not claimed
    execute(db, ANA, MOVE, {"id": 1, "s": "QUOTED"})
    execute(db, ANA, MOVE, {"id": 1, "s": "SOLD"})
    assert code(execute(db, ANA, MOVE, {"id": 1, "s": "LOST"})[1]) == "INVALID_TRANSITION"  # SOLD is final


def test_unknown_caller_is_rejected(db):
    data, errors = execute(db, 99, "{ me { id } }")
    assert data is None and code(errors) == "UNAUTHENTICATED"


def test_invalid_enum_is_rejected_by_the_schema(db):
    _, errors = execute(db, ANA, LEADS, {"status": "MAYBE"})
    assert errors and "LeadStatus" in errors[0]["message"]


def test_http_endpoint(db):
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/graphql", method="POST",
            data=json.dumps({"query": "{ me { name role } }"}).encode(), headers={"X-Agent-Id": "3"})
        body = json.loads(urllib.request.urlopen(req).read())
        assert body == {"data": {"me": {"name": "Mia Manager", "role": "MANAGER"}}}
    finally:
        server.shutdown()
        server.server_close()
