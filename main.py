import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timedelta, timezone
import hashlib

from database import db, create_document, get_documents
from schemas import LibraryUser, Book, Review, Borrow, Activity

app = FastAPI(title="Digital Library API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple token system (demo): token is user_id hashed with secret and expires in 24h
SECRET = os.getenv("SECRET_KEY", "dev-secret")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# Utility functions

def hash_password(pw: str) -> str:
    return hashlib.sha256((pw + SECRET).encode()).hexdigest()


def verify_password(pw: str, hashed: str) -> bool:
    return hash_password(pw) == hashed


def make_token(user_id: str) -> str:
    # naive token: user_id|expiry|signature
    expiry = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
    payload = f"{user_id}|{expiry}"
    signature = hashlib.sha256((payload + SECRET).encode()).hexdigest()
    return f"{payload}|{signature}"


def parse_token(token: str) -> Optional[str]:
    try:
        user_id, expiry, signature = token.split("|")
        payload = f"{user_id}|{expiry}"
        if hashlib.sha256((payload + SECRET).encode()).hexdigest() != signature:
            return None
        if int(expiry) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return user_id
    except Exception:
        return None


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    uid = parse_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db["libraryuser"].find_one({"_id": ObjectId(uid)})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# Health and schema
@app.get("/")
def root():
    return {"name": "Digital Library API", "status": "ok"}


@app.get("/test")
def test_database():
    response = {"backend": "✅ Running", "database": "❌ Not Available"}
    try:
        collections = db.list_collection_names()
        response["database"] = "✅ Connected"
        response["collections"] = collections
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# Auth
class RegisterPayload(BaseModel):
    name: str
    email: str
    password: str


@app.post("/auth/register", response_model=Token)
def register(payload: RegisterPayload):
    if db["libraryuser"].find_one({"email": payload.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = LibraryUser(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role="user",
        preferences={"theme": "light", "fontSize": 16, "highContrast": False},
    )
    uid = create_document("libraryuser", user)
    token = make_token(uid)
    create_document("activity", Activity(type="register", user_id=uid, meta={"email": payload.email}))
    return Token(access_token=token)


@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = db["libraryuser"].find_one({"email": form_data.username})
    if not user or not verify_password(form_data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = make_token(str(user["_id"]))
    create_document("activity", Activity(type="login", user_id=str(user["_id"])) )
    return Token(access_token=token)


@app.get("/me")
def me(current=Depends(get_current_user)):
    current["_id"] = str(current["_id"])  # make serializable
    current.pop("password_hash", None)
    return current


# Books CRUD (admin endpoints for create/update/delete)
@app.post("/books", response_model=dict)
def create_book(book: Book, current=Depends(get_current_user)):
    if current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    bid = create_document("book", book)
    create_document("activity", Activity(type="create_book", user_id=str(current["_id"]), meta={"book_id": bid}))
    return {"id": bid}


class BookQuery(BaseModel):
    q: Optional[str] = None
    genre: Optional[str] = None
    author: Optional[str] = None
    year: Optional[int] = None
    featured: Optional[bool] = None
    limit: int = 24


@app.post("/books/search")
def search_books(query: BookQuery):
    filt = {}
    if query.genre:
        filt["genre"] = query.genre
    if query.author:
        filt["author"] = query.author
    if query.year:
        filt["year"] = query.year
    if query.featured is not None:
        filt["featured"] = query.featured

    # text search over title, author, tags, description, isbn
    if query.q:
        regex = {"$regex": query.q, "$options": "i"}
        filt["$or"] = [
            {"title": regex},
            {"author": regex},
            {"tags": regex},
            {"description": regex},
            {"isbn": regex},
        ]

    books = db["book"].find(filt).limit(int(query.limit))
    results = []
    for b in books:
        b["_id"] = str(b["_id"])
        results.append(b)
    return {"items": results}


@app.get("/books/{book_id}")
def get_book(book_id: str):
    try:
        b = db["book"].find_one({"_id": ObjectId(book_id)})
        if not b:
            raise HTTPException(status_code=404, detail="Not found")
        b["_id"] = str(b["_id"])
        return b
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")


class UpdateBookPayload(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    isbn: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    file_url: Optional[str] = None
    tags: Optional[List[str]] = None
    featured: Optional[bool] = None


@app.put("/books/{book_id}")
def update_book(book_id: str, payload: UpdateBookPayload, current=Depends(get_current_user)):
    if current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    update = {k: v for k, v in payload.dict().items() if v is not None}
    res = db["book"].update_one({"_id": ObjectId(book_id)}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    create_document("activity", Activity(type="update_book", user_id=str(current["_id"]), meta={"book_id": book_id}))
    return {"updated": True}


@app.delete("/books/{book_id}")
def delete_book(book_id: str, current=Depends(get_current_user)):
    if current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    res = db["book"].delete_one({"_id": ObjectId(book_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    create_document("activity", Activity(type="delete_book", user_id=str(current["_id"]), meta={"book_id": book_id}))
    return {"deleted": True}


# Reviews & Ratings
@app.post("/books/{book_id}/reviews")
def add_review(book_id: str, review: Review, current=Depends(get_current_user)):
    if review.user_id != str(current["_id"]):
        raise HTTPException(status_code=403, detail="User mismatch")
    # ensure book exists
    if not db["book"].find_one({"_id": ObjectId(book_id)}):
        raise HTTPException(status_code=404, detail="Book not found")
    rid = create_document("review", review)
    create_document("activity", Activity(type="review", user_id=str(current["_id"]), meta={"book_id": book_id, "review_id": rid}))
    return {"id": rid}


@app.get("/books/{book_id}/reviews")
def list_reviews(book_id: str, limit: int = 20):
    items = list(db["review"].find({"book_id": book_id}).limit(limit))
    for r in items:
        r["_id"] = str(r["_id"])
    return {"items": items}


# Borrow / Download
@app.post("/borrow")
def borrow_book(borrow: Borrow, current=Depends(get_current_user)):
    if borrow.user_id != str(current["_id"]):
        raise HTTPException(status_code=403, detail="User mismatch")
    # limit: 3 active borrows
    active = db["borrow"].count_documents({"user_id": borrow.user_id, "status": "borrowed"})
    if active >= 3:
        raise HTTPException(status_code=400, detail="Borrow limit reached")
    borrow_id = create_document("borrow", borrow)
    create_document("activity", Activity(type="borrow", user_id=str(current["_id"]), meta={"book_id": borrow.book_id}))
    return {"id": borrow_id}


@app.post("/return/{borrow_id}")
def return_book(borrow_id: str, current=Depends(get_current_user)):
    b = db["borrow"].find_one({"_id": ObjectId(borrow_id)})
    if not b:
        raise HTTPException(status_code=404, detail="Not found")
    if b.get("user_id") != str(current["_id"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    db["borrow"].update_one({"_id": ObjectId(borrow_id)}, {"$set": {"status": "returned", "updated_at": datetime.now(timezone.utc)}})
    create_document("activity", Activity(type="return", user_id=str(current["_id"]), meta={"borrow_id": borrow_id}))
    return {"returned": True}


# Featured, latest, trending
@app.get("/home")
def home():
    featured = list(db["book"].find({"featured": True}).limit(10))
    latest = list(db["book"].find({}).sort("created_at", -1).limit(12))
    trending = list(db["book"].find({}).sort("updated_at", -1).limit(12))
    for col in (featured, latest, trending):
        for b in col:
            b["_id"] = str(b["_id"])
    announcements = [
        {"id": 1, "title": "Welcome to the Digital Library", "body": "Explore featured collections and the latest additions."},
    ]
    return {"featured": featured, "latest": latest, "trending": trending, "announcements": announcements}


# Simple activity feed for admin
@app.get("/admin/activity")
def admin_activity(current=Depends(get_current_user)):
    if current.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    items = list(db["activity"].find({}).sort("created_at", -1).limit(100))
    for a in items:
        a["_id"] = str(a["_id"])
    return {"items": items}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
