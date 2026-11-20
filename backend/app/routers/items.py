from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import connect

router = APIRouter(prefix="/items", tags=["items"])


class ItemCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class Item(ItemCreate):
    id: int


@router.get("", response_model=list[Item])
def list_items() -> list[Item]:
    with connect() as db:
        rows = db.execute("SELECT id, name, description FROM items ORDER BY id").fetchall()
        return [Item(**dict(row)) for row in rows]


@router.post("", response_model=Item, status_code=201)
def create_item(payload: ItemCreate) -> Item:
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO items(name, description) VALUES (?, ?)",
            (payload.name, payload.description),
        )
        row = db.execute("SELECT id, name, description FROM items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return Item(**dict(row))


@router.get("/{item_id}", response_model=Item)
def get_item(item_id: int) -> Item:
    with connect() as db:
        row = db.execute("SELECT id, name, description FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Item not found")
        return Item(**dict(row))


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int) -> None:
    with connect() as db:
        cursor = db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Item not found")
