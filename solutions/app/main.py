from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import auth, posts, users, votes

# No Base.metadata.create_all() here: Alembic owns the schema now.
# create_all only creates missing tables; it never alters existing ones.
app = FastAPI(title="Posts API")

# Browsers block a page on one origin from calling an API on another
# unless the API opts in. List the frontends that are allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(posts.router)
app.include_router(users.router)
app.include_router(auth.router)
app.include_router(votes.router)


@app.get("/")
def root():
    return {"message": "Welcome to my API"}
