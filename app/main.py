from fastapi import FastAPI, Response, status, HTTPException
from fastapi.params import Body
from pydantic import BaseModel
from typing import Optional
from random import randrange
import time
import psycopg2
from  psycopg2.extras import RealDictCursor
from .config import settings

app =  FastAPI()

class Post(BaseModel):
    title: str
    content: str
    published: bool = True
    rating: Optional[int] = None

while True:
    try:
        conn = psycopg2.connect(
            host=settings.database_hostname,
            database=settings.database_name,
            user=settings.database_username,
            password=settings.database_password,
            cursor_factory=RealDictCursor,
        )
        cursor = conn.cursor()
        print("Database connection was successful.")
        break
    except Exception as error:
        print("Connecting to database failed !")
        print(f"Error: {error}")
        time.sleep(2)


my_posts = [{"title": "title of post 1", "content": "content of post 1", "id": 1},
            {"title": "Favorite Food", "content": "Momo, Coffee", "id": 2}]

def find_post(id):
    for p in my_posts:
        if p["id"] == id:
            return p

def find_index_post(id):
    for i,p in enumerate(my_posts):
        if p["id"] == id:
            return i
        

# The following code block is also called path operation also referred to as route
# Route (Path Operation) = Decorator + Function (For FastAPI)
# Route = HTTP Method + URL Path + Handler (Universally)

# Decorator tells the framework where to listen (the URL path) and how to listen (HTTP method, in this case GET)
@app.get("/")
def read_root():
    return {"message": "Welcome to my API"}

@app.get("/posts")
def get_posts():
    return {"data": my_posts}

@app.post("/posts", status_code = status.HTTP_201_CREATED)
def create_posts(post: Post): # Taking the body of the post, storing it in a dictionary and assigning it to the variable 'payload'
    post_dict = post.model_dump()  # .model_dump() converts Pydantic model instance into a Python dictionary
    post_dict['id'] = randrange(0, 1000000)
    my_posts.append(post_dict)
    return {"data": post_dict}

@app.get("/posts/{id}") # Everytime we have a path parameter like '{id}', it will always return a string 
def get_post(id: int, response: Response):
    print(type(id))
    post = find_post(int(id))
    if not post:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, 
                            detail = f"Post with id: {id} was not found")
        # response.status_code = status.HTTP_404_NOT_FOUND
        # return {'message': f"Post with id: {id} was not found"}
    return {"post detail": post}

@app.delete("/posts/{id}", status_code = status.HTTP_204_NO_CONTENT)
def delete_post(id: int):
    # Find the index of the required post in the array
    index = find_index_post(id)

    if index == None:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail = f"Post with id: {id} does not exist")
    my_posts.pop(index)

    # FastAPI enforces this rule that if we are deleting something we should not be sending any data back 
    # So we use 'Response(status_code=status.HTTP_204_NO_CONTENT' to send back some information 
    return Response(status_code=status.HTTP_204_NO_CONTENT)  

@app.put("/posts/{id}")
def update_post(id: int, post:Post):
    index = find_index_post(id)

    if index == None:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail = f"Post with id: {id} does not exist")
    my_posts.pop(index)

    post_dict = post.model_dump() # .model_dump() converts Pydantic model instance into a Python dictionary
    post_dict["id"] = id
    my_posts[index] = post_dict
    return {"data": post_dict}