from fastapi import FastAPI, Response, status, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from . import models
from .database import engine, get_db

models.Base.metadata.create_all(bind = engine)

app =  FastAPI()

class Post(BaseModel): # Pydantic model / schema that defines what the request body should look like and validates it
    title: str
    content: str
    published: bool = True # Defaults to True if the client doesn't send a value
    rating: Optional[int] = None # Optional field, not stored in the database, just here to demo Optional types

# The following code block is also called path operation also referred to as route
# Route (Path Operation) = Decorator + Function (For FastAPI)
# Route = HTTP Method + URL Path + Handler (Universally)

# Decorator tells the framework where to listen (the URL path) and how to listen (HTTP method, in this case GET)
@app.get("/")
def read_root():
    return {"message": "Welcome to my API"}


@app.get("/posts")
def get_posts(db: Session = Depends(get_db)):
    posts = db.query(models.Post).all() # SELECT * FROM posts, but through the ORM
    return {"data": posts}

@app.post("/posts", status_code = status.HTTP_201_CREATED)
def create_posts(post: Post, db: Session = Depends(get_db)): # Taking the body of the post, storing it in a dictionary and assigning it to the variable 'payload'
    new_post = models.Post(**post.model_dump(exclude={"rating"})) # Created a new post
    db.add(new_post) # Added the post to the database
    db.commit() # Committed to the database
    db.refresh(new_post) # Retrive the newly created post and store it in the variable, 'new_post'

    return {"data": new_post}

@app.get("/posts/{id}") # Everytime we have a path parameter like '{id}', it will always return a string
def get_post(id: int, db: Session = Depends(get_db)):
    post = db.query(models.Post).filter(models.Post.id == id).first() # 'first()' grabs the single matching row, or None if there isn't one
    if not post:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND,
                            detail = f"Post with id: {id} was not found")
    return {"post detail": post}

@app.delete("/posts/{id}", status_code = status.HTTP_204_NO_CONTENT)
def delete_post(id: int, db: Session = Depends(get_db)):
    post_query = db.query(models.Post).filter(models.Post.id == id) # Keeping this as a query (not calling '.first()') so we can call '.delete()' on it directly

    if post_query.first() == None: # Check if a post with this id actually exists before trying to delete it
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail = f"Post with id: {id} does not exist")

    post_query.delete(synchronize_session = False) # Deletes the row(s) matching the query
    db.commit() # Commits the delete to the database

    # FastAPI enforces this rule that if we are deleting something we should not be sending any data back 
    # So we use 'Response(status_code=status.HTTP_204_NO_CONTENT' to send back some information 
    return Response(status_code=status.HTTP_204_NO_CONTENT)  

@app.put("/posts/{id}")
def update_post(id: int, post:Post, db: Session = Depends(get_db)):
    post_query = db.query(models.Post).filter(models.Post.id == id) # Keeping this as a query so we can call '.update()' on it directly
    existing_post = post_query.first() # Grabbing the actual row separately, just to check whether it exists

    if existing_post == None:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail = f"Post with id: {id} does not exist")

    post_query.update(post.model_dump(exclude={"rating"}), synchronize_session= False) # 'rating' isn't a column on the model, so we leave it out
    db.commit()
    return {"data": post_query.first()} # Re-fetch so we return the row with its updated values