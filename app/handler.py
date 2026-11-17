import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr
from fastapi import FastAPI, Header, HTTPException
from mangum import Mangum
from pydantic import BaseModel, Field

TABLE_NAME = os.environ.get("LITERATURE_TABLE", "MaruLipiLiterature")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
app = FastAPI(title="Maru Lipi API", version="1.0.0")


class LiteratureInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    category: str = Field(pattern="^(poem|story|essay)$")
    language: str = Field(default="hi", pattern="^(hi|mwr)$")
    author: str = "Maru Lipi"
    published: bool = False


def require_admin(token: str | None):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin access is not configured")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def clean(value):
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


@app.get("/")
def health():
    return {"service": "Maru Lipi API", "status": "ok", "version": "1.0.0"}


@app.get("/literature")
def list_literature(category: str | None = None, language: str | None = None):
    filters = [Attr("published").eq(True)]
    if category:
        filters.append(Attr("category").eq(category))
    if language:
        filters.append(Attr("language").eq(language))
    filter_expr = filters[0]
    for item in filters[1:]:
        filter_expr = filter_expr & item
    response = table.scan(FilterExpression=filter_expr)
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression=filter_expr,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"items": clean(items)}


@app.get("/literature/{item_id}")
def get_literature(item_id: str):
    item = table.get_item(Key={"id": item_id}).get("Item")
    if not item or not item.get("published", False):
        raise HTTPException(status_code=404, detail="Literature not found")
    return clean(item)


@app.get("/admin/literature")
def admin_list_literature(x_admin_token: str | None = Header(default=None)):
    """Return all literature, including drafts, to an authenticated admin."""
    require_admin(x_admin_token)
    response = table.scan()
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return {"items": clean(items)}


@app.post("/admin/literature", status_code=201)
def create_literature(
    payload: LiteratureInput, x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)
    now = datetime.now(timezone.utc).isoformat()
    item = payload.model_dump()
    item.update({"id": str(uuid.uuid4()), "created_at": now, "updated_at": now})
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
    return clean(item)


@app.put("/admin/literature/{item_id}")
def update_literature(
    item_id: str,
    payload: LiteratureInput,
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    existing = table.get_item(Key={"id": item_id}).get("Item")
    if not existing:
        raise HTTPException(status_code=404, detail="Literature not found")
    updated = payload.model_dump()
    updated.update({
        "id": item_id,
        "created_at": existing.get("created_at"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    table.put_item(Item=updated)
    return clean(updated)


@app.delete("/admin/literature/{item_id}")
def delete_literature(
    item_id: str, x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)
    response = table.delete_item(Key={"id": item_id}, ReturnValues="ALL_OLD")
    if "Attributes" not in response:
        raise HTTPException(status_code=404, detail="Literature not found")
    return {"deleted": True, "id": item_id}


handler = Mangum(app)
