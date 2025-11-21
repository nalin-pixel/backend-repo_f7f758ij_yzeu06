"""
Database Schemas for Digital Library System

Each Pydantic model represents a MongoDB collection. The collection name is the
lowercase of the class name (e.g., Book -> "book").
"""
from pydantic import BaseModel, Field
from typing import Optional, List

class LibraryUser(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    password_hash: str = Field(..., description="Hashed password")
    role: str = Field("user", description="Role: user or admin")
    avatar_url: Optional[str] = Field(None, description="Optional avatar image URL")
    preferences: Optional[dict] = Field(default_factory=dict, description="Accessibility and reading preferences")
    is_active: bool = Field(True, description="Active account")

class Book(BaseModel):
    title: str
    author: str
    genre: str
    year: int
    isbn: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    file_url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    featured: bool = Field(False, description="Show on homepage as featured")

class Review(BaseModel):
    book_id: str
    user_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class Borrow(BaseModel):
    book_id: str
    user_id: str
    status: str = Field("borrowed", description="borrowed or returned")
    due_date: Optional[str] = Field(None, description="ISO date string for due date")

class Activity(BaseModel):
    user_id: Optional[str] = None
    type: str = Field(..., description="login, search, view, borrow, return, review, create_book, update_book, delete_book")
    meta: dict = Field(default_factory=dict)
