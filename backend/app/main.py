from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import items, portals

app = FastAPI(title="Jimix API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(items.router, prefix="/api")
app.include_router(portals.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
