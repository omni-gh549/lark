"""A fake Notion API for tests. Mounted into mock_upstream under /notion/v1."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/notion/v1")
SEEN: list = []  # (kind, id, json body) of every call that sends something, for assertions

DB = "11111111-1111-1111-1111-111111111111"
PG = "22222222-2222-2222-2222-222222222222"


def rt(s):
    return [{"type": "text", "plain_text": s, "text": {"content": s}, "annotations": {}, "href": None}]


DB_OBJ = {"object": "database", "id": DB, "url": "https://www.notion.so/tasks", "title": rt("Tasks"), "description": [],
          "properties": {"Name": {"type": "title", "title": {}},
                         "Status": {"type": "status", "status": {"options": [{"name": "Todo"}, {"name": "Done"}]}},
                         "Due": {"type": "date", "date": {}},
                         "Tags": {"type": "multi_select", "multi_select": {"options": [{"name": "a"}]}},
                         "Points": {"type": "number", "number": {}},
                         "Made": {"type": "created_time", "created_time": {}}}}
PG_OBJ = {"object": "page", "id": PG, "url": "https://www.notion.so/Buy-milk", "last_edited_time": "2026-10-09T10:00:00Z",
          "archived": False, "parent": {"type": "database_id", "database_id": DB},
          "properties": {"Name": {"type": "title", "title": rt("Buy milk")},
                         "Status": {"type": "status", "status": {"name": "Todo"}},
                         "Due": {"type": "date", "date": {"start": "2026-10-12", "end": None}},
                         "Points": {"type": "number", "number": 3}}}


def _bad(request: Request):
    if request.headers.get("authorization") != "Bearer ntn-good-token":
        return JSONResponse({"object": "error", "message": "API token is invalid."}, status_code=401)
    assert request.headers.get("notion-version")
    return None


def _missing():
    return JSONResponse({"object": "error", "message": "not found"}, status_code=404)


@router.get("/users/me")
async def me(request: Request):
    return _bad(request) or {"object": "user", "name": "Lark", "bot": {"workspace_name": "Oscar HQ"}}


@router.post("/search")
async def search(request: Request):
    if (bad := _bad(request)):
        return bad
    SEEN.append(("SEARCH", "", await request.json()))
    return {"results": [PG_OBJ], "has_more": False}


@router.get("/pages/{pid}")
async def page(pid: str, request: Request):
    return _bad(request) or (PG_OBJ if pid == PG else _missing())


@router.patch("/pages/{pid}")
async def patch_page(pid: str, request: Request):
    if (bad := _bad(request)):
        return bad
    SEEN.append(("PATCH", pid, await request.json()))
    return PG_OBJ


@router.post("/pages")
async def new_page(request: Request):
    if (bad := _bad(request)):
        return bad
    SEEN.append(("POST", "", await request.json()))
    return {**PG_OBJ, "id": "33333333-3333-3333-3333-333333333333"}


@router.get("/databases/{did}")
async def database(did: str, request: Request):
    return _bad(request) or (DB_OBJ if did == DB else _missing())


@router.post("/databases/{did}/query")
async def query(did: str, request: Request):
    if (bad := _bad(request)):
        return bad
    SEEN.append(("QUERY", did, await request.json()))
    return {"results": [PG_OBJ], "has_more": True, "next_cursor": "cur-2"}


@router.get("/blocks/{bid}/children")
async def children(bid: str, request: Request):
    if (bad := _bad(request)):
        return bad
    if bid == PG:
        return {"results": [
            {"id": "b1", "type": "heading_1", "has_children": False, "heading_1": {"rich_text": rt("Shopping")}},
            {"id": "b2", "type": "to_do", "has_children": True, "to_do": {"rich_text": rt("Milk"), "checked": False}},
            {"id": "b3", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": [
                {"type": "text", "plain_text": "see site", "annotations": {}, "href": "https://example.com"}]}}], "has_more": False}
    return {"results": [{"id": "b4", "type": "bulleted_list_item", "has_children": False,
                         "bulleted_list_item": {"rich_text": rt("oat")}}], "has_more": False}


@router.patch("/blocks/{bid}/children")
async def append(bid: str, request: Request):
    if (bad := _bad(request)):
        return bad
    SEEN.append(("APPEND", bid, await request.json()))
    return {"results": []}
