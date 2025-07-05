from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timedelta
import httpx
import json

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Models
class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    name: str
    picture: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Product(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    price: float
    images: List[str] = []  # Base64 encoded images
    category: str
    inventory: int = 0
    type: str  # physical, digital, service
    featured: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ProductCreate(BaseModel):
    name: str
    description: str
    price: float
    images: List[str] = []
    category: str
    inventory: int = 0
    type: str
    featured: bool = False

class Category(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CategoryCreate(BaseModel):
    name: str
    description: str

class CartItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    product_id: str
    quantity: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CartItemCreate(BaseModel):
    product_id: str
    quantity: int

class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    items: List[Dict[str, Any]]
    total: float
    status: str = "pending"  # pending, processing, shipped, delivered, cancelled
    payment_method: str
    payment_status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class OrderCreate(BaseModel):
    items: List[Dict[str, Any]]
    total: float
    payment_method: str

class Session(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    session_token: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PromoCode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    code: str
    discount_percentage: float
    discount_amount: Optional[float] = None
    min_order_amount: Optional[float] = None
    active: bool = True
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PromoCodeCreate(BaseModel):
    code: str
    discount_percentage: float
    discount_amount: Optional[float] = None
    min_order_amount: Optional[float] = None
    expires_at: Optional[datetime] = None

# Authentication helper
async def get_current_user(x_session_id: str = Header(None)):
    if not x_session_id:
        raise HTTPException(status_code=401, detail="Session ID required")
    
    session = await db.sessions.find_one({"session_token": x_session_id})
    if not session or session["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    user = await db.users.find_one({"id": session["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return User(**user)

# Authentication routes
@api_router.post("/auth/session")
async def create_session(session_id: str):
    """Create session from Emergent Auth"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": session_id}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid session")
        
        user_data = response.json()
        
        # Save or update user
        existing_user = await db.users.find_one({"email": user_data["email"]})
        if not existing_user:
            user = User(
                email=user_data["email"],
                name=user_data["name"],
                picture=user_data.get("picture")
            )
            await db.users.insert_one(user.dict())
        else:
            user = User(**existing_user)
        
        # Create session
        session_token = str(uuid.uuid4())
        session = Session(
            user_id=user.id,
            session_token=session_token,
            expires_at=datetime.utcnow() + timedelta(days=7)
        )
        await db.sessions.insert_one(session.dict())
        
        return {"session_token": session_token, "user": user}

@api_router.get("/auth/profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    return current_user

# Product routes
@api_router.get("/products", response_model=List[Product])
async def get_products(
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    featured: Optional[bool] = None
):
    query = {}
    
    if category:
        query["category"] = category
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}}
        ]
    if min_price is not None or max_price is not None:
        price_query = {}
        if min_price is not None:
            price_query["$gte"] = min_price
        if max_price is not None:
            price_query["$lte"] = max_price
        query["price"] = price_query
    if featured is not None:
        query["featured"] = featured
    
    products = await db.products.find(query).to_list(1000)
    return [Product(**product) for product in products]

@api_router.get("/products/{product_id}", response_model=Product)
async def get_product(product_id: str):
    product = await db.products.find_one({"id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(**product)

@api_router.post("/products", response_model=Product)
async def create_product(product: ProductCreate):
    product_obj = Product(**product.dict())
    await db.products.insert_one(product_obj.dict())
    return product_obj

@api_router.put("/products/{product_id}", response_model=Product)
async def update_product(product_id: str, product: ProductCreate):
    existing_product = await db.products.find_one({"id": product_id})
    if not existing_product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    updated_product = Product(**product.dict(), id=product_id, created_at=existing_product["created_at"])
    await db.products.replace_one({"id": product_id}, updated_product.dict())
    return updated_product

@api_router.delete("/products/{product_id}")
async def delete_product(product_id: str):
    result = await db.products.delete_one({"id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"message": "Product deleted successfully"}

# Category routes
@api_router.get("/categories", response_model=List[Category])
async def get_categories():
    categories = await db.categories.find().to_list(1000)
    return [Category(**category) for category in categories]

@api_router.post("/categories", response_model=Category)
async def create_category(category: CategoryCreate):
    category_obj = Category(**category.dict())
    await db.categories.insert_one(category_obj.dict())
    return category_obj

# Cart routes
@api_router.get("/cart")
async def get_cart(current_user: User = Depends(get_current_user)):
    cart_items = await db.cart_items.find({"user_id": current_user.id}).to_list(1000)
    
    # Get product details for each cart item
    cart_with_products = []
    for item in cart_items:
        product = await db.products.find_one({"id": item["product_id"]})
        if product:
            cart_with_products.append({
                "id": item["id"],
                "quantity": item["quantity"],
                "product": Product(**product)
            })
    
    return cart_with_products

@api_router.post("/cart")
async def add_to_cart(item: CartItemCreate, current_user: User = Depends(get_current_user)):
    # Check if item already exists in cart
    existing_item = await db.cart_items.find_one({
        "user_id": current_user.id,
        "product_id": item.product_id
    })
    
    if existing_item:
        # Update quantity
        new_quantity = existing_item["quantity"] + item.quantity
        await db.cart_items.update_one(
            {"id": existing_item["id"]},
            {"$set": {"quantity": new_quantity}}
        )
        return {"message": "Cart updated successfully"}
    else:
        # Add new item
        cart_item = CartItem(
            user_id=current_user.id,
            product_id=item.product_id,
            quantity=item.quantity
        )
        await db.cart_items.insert_one(cart_item.dict())
        return {"message": "Item added to cart successfully"}

@api_router.put("/cart/{item_id}")
async def update_cart_item(item_id: str, quantity: int, current_user: User = Depends(get_current_user)):
    result = await db.cart_items.update_one(
        {"id": item_id, "user_id": current_user.id},
        {"$set": {"quantity": quantity}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return {"message": "Cart item updated successfully"}

@api_router.delete("/cart/{item_id}")
async def remove_from_cart(item_id: str, current_user: User = Depends(get_current_user)):
    result = await db.cart_items.delete_one({"id": item_id, "user_id": current_user.id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return {"message": "Item removed from cart successfully"}

# Order routes
@api_router.get("/orders", response_model=List[Order])
async def get_orders(current_user: User = Depends(get_current_user)):
    orders = await db.orders.find({"user_id": current_user.id}).to_list(1000)
    return [Order(**order) for order in orders]

@api_router.post("/orders", response_model=Order)
async def create_order(order: OrderCreate, current_user: User = Depends(get_current_user)):
    order_obj = Order(**order.dict(), user_id=current_user.id)
    await db.orders.insert_one(order_obj.dict())
    
    # Clear cart after order
    await db.cart_items.delete_many({"user_id": current_user.id})
    
    return order_obj

@api_router.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str, current_user: User = Depends(get_current_user)):
    order = await db.orders.find_one({"id": order_id, "user_id": current_user.id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return Order(**order)

# Promo code routes
@api_router.get("/promo-codes/{code}")
async def validate_promo_code(code: str):
    promo = await db.promo_codes.find_one({
        "code": code,
        "active": True,
        "$or": [
            {"expires_at": {"$gte": datetime.utcnow()}},
            {"expires_at": None}
        ]
    })
    if not promo:
        raise HTTPException(status_code=404, detail="Invalid or expired promo code")
    return PromoCode(**promo)

@api_router.post("/promo-codes", response_model=PromoCode)
async def create_promo_code(promo: PromoCodeCreate):
    promo_obj = PromoCode(**promo.dict())
    await db.promo_codes.insert_one(promo_obj.dict())
    return promo_obj

# Admin routes (simplified - no auth for MVP)
@api_router.get("/admin/dashboard")
async def admin_dashboard():
    total_products = await db.products.count_documents({})
    total_orders = await db.orders.count_documents({})
    total_users = await db.users.count_documents({})
    
    return {
        "total_products": total_products,
        "total_orders": total_orders,
        "total_users": total_users
    }

# Sample data initialization
@api_router.post("/admin/init-sample-data")
async def init_sample_data():
    """Initialize sample data for testing"""
    
    # Sample categories
    categories = [
        {"name": "Electronics", "description": "Latest gadgets and tech accessories"},
        {"name": "Fashion", "description": "Trendy clothing and accessories"},
        {"name": "Digital Products", "description": "Software, courses, and digital services"},
        {"name": "Services", "description": "Professional services and consultations"},
        {"name": "Home & Garden", "description": "Home improvement and garden supplies"}
    ]
    
    # Check if categories already exist
    existing_categories = await db.categories.count_documents({})
    if existing_categories == 0:
        category_objects = [Category(**cat) for cat in categories]
        await db.categories.insert_many([cat.dict() for cat in category_objects])
    
    # Sample products with base64 placeholder images
    sample_products = [
        {
            "name": "Wireless Bluetooth Headphones",
            "description": "High-quality wireless headphones with noise cancellation and 30-hour battery life.",
            "price": 99.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxwYXRoIGQ9Ik0xMDAgNTBDMTI3LjYxNCA1MCAxNTAgNzIuMzg1OCAxNTAgMTAwQzE1MCAxMjcuNjE0IDEyNy42MTQgMTUwIDEwMCAxNTBDNzIuMzg1OCAxNTAgNTAgMTI3LjYxNCA1MCAxMDBDNTAgNzIuMzg1OCA3Mi4zODU4IDUwIDEwMCA1MFoiIGZpbGw9IiMzQjgyRjYiLz4KPHN2ZyB3aWR0aD0iMjAiIGhlaWdodD0iMjAiIHZpZXdCb3g9IjAgMCAyMCAyMCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEwIDFMMTMuMDkgNi4yNkwxOSA3TDE0LjUgMTEuMjRMMTYgMTlMMTAgMTUuMjdMNCA5TDEwIDEzLjI3TDEzLjA5IDYuMjZMMTAgMVoiIGZpbGw9IndoaXRlIi8+Cjwvc3ZnPgo8L3N2Zz4K"],
            "category": "Electronics",
            "inventory": 50,
            "type": "physical",
            "featured": True
        },
        {
            "name": "Smart Fitness Watch",
            "description": "Track your health and fitness with this advanced smartwatch featuring heart rate monitoring, GPS, and 7-day battery life.",
            "price": 249.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxyZWN0IHg9IjUwIiB5PSI3MCIgd2lkdGg9IjEwMCIgaGVpZ2h0PSI2MCIgcng9IjEwIiBmaWxsPSIjMTBCOTgxIi8+CjxyZWN0IHg9IjcwIiB5PSI5MCIgd2lkdGg9IjYwIiBoZWlnaHQ9IjIwIiBmaWxsPSJ3aGl0ZSIvPgo8L3N2Zz4K"],
            "category": "Electronics",
            "inventory": 30,
            "type": "physical",
            "featured": True
        },
        {
            "name": "Premium Cotton T-Shirt",
            "description": "Comfortable, breathable cotton t-shirt available in multiple colors and sizes.",
            "price": 29.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxwYXRoIGQ9Ik02MCA2MEM2MCA0NC4zNzUgNzIuMzc1IDMyIDg4IDMySDExMkMxMjcuNjI1IDMyIDE0MCA0NC4zNzUgMTQwIDYwVjE2MEMxNDAgMTYwIDEwMCAxNjAgMTAwIDE2MEMxMDAgMTYwIDYwIDE2MCA2MCAxNjBWNjBaIiBmaWxsPSIjRUY0NDQ0Ii8+Cjwvc3ZnPgo="],
            "category": "Fashion",
            "inventory": 100,
            "type": "physical",
            "featured": False
        },
        {
            "name": "Web Development Course",
            "description": "Complete full-stack web development course with React, Node.js, and MongoDB. Includes lifetime access and certificate.",
            "price": 79.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxyZWN0IHg9IjMwIiB5PSI0MCIgd2lkdGg9IjE0MCIgaGVpZ2h0PSIxMjAiIHJ4PSI4IiBmaWxsPSIjODA3OUY3Ii8+CjxyZWN0IHg9IjUwIiB5PSI2MCIgd2lkdGg9IjEwMCIgaGVpZ2h0PSI4IiBmaWxsPSJ3aGl0ZSIvPgo8cmVjdCB4PSI1MCIgeT0iODAiIHdpZHRoPSI4MCIgaGVpZ2h0PSI4IiBmaWxsPSJ3aGl0ZSIvPgo8cmVjdCB4PSI1MCIgeT0iMTAwIiB3aWR0aD0iMTIwIiBoZWlnaHQ9IjgiIGZpbGw9IndoaXRlIi8+Cjwvc3ZnPgo="],
            "category": "Digital Products",
            "inventory": 9999,
            "type": "digital",
            "featured": True
        },
        {
            "name": "Business Consultation",
            "description": "1-hour business strategy consultation with experienced consultant. Includes follow-up notes and action plan.",
            "price": 150.00,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxjaXJjbGUgY3g9IjEwMCIgY3k9IjgwIiByPSIyMCIgZmlsbD0iIzNCODJGNiIvPgo8cmVjdCB4PSI3MCIgeT0iMTEwIiB3aWR0aD0iNjAiIGhlaWdodD0iNDAiIGZpbGw9IiMzQjgyRjYiLz4KPHN2ZyB3aWR0aD0iMjAiIGhlaWdodD0iMjAiIHZpZXdCb3g9IjAgMCAyMCAyMCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEwIDFMMTMuMDkgNi4yNkwxOSA3TDE0LjUgMTEuMjRMMTYgMTlMMTAgMTUuMjdMNCA5TDEwIDEzLjI3TDEzLjA5IDYuMjZMMTAgMVoiIGZpbGw9IndoaXRlIi8+Cjwvc3ZnPgo8L3N2Zz4K"],
            "category": "Services",
            "inventory": 10,
            "type": "service",
            "featured": False
        },
        {
            "name": "Smartphone Case",
            "description": "Protective case for smartphones with shock absorption and wireless charging compatibility.",
            "price": 24.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxyZWN0IHg9IjcwIiB5PSI0MCIgd2lkdGg9IjYwIiBoZWlnaHQ9IjEyMCIgcng9IjEwIiBmaWxsPSIjMTExODI3Ii8+CjxyZWN0IHg9IjgwIiB5PSI2MCIgd2lkdGg9IjQwIiBoZWlnaHQ9IjQwIiBmaWxsPSIjMzc0MTUxIi8+Cjwvc3ZnPgo="],
            "category": "Electronics",
            "inventory": 75,
            "type": "physical",
            "featured": False
        },
        {
            "name": "Leather Wallet",
            "description": "Handcrafted genuine leather wallet with RFID protection and multiple card slots.",
            "price": 45.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxyZWN0IHg9IjUwIiB5PSI4MCIgd2lkdGg9IjEwMCIgaGVpZ2h0PSI0MCIgcng9IjQiIGZpbGw9IiM5MjQwMDAiLz4KPHJlY3QgeD0iNjAiIHk9IjkwIiB3aWR0aD0iODAiIGhlaWdodD0iNCIgZmlsbD0iIzc5MjcwNCIvPgo8cmVjdCB4PSI2MCIgeT0iMTAwIiB3aWR0aD0iODAiIGhlaWdodD0iNCIgZmlsbD0iIzc5MjcwNCIvPgo8L3N2Zz4K"],
            "category": "Fashion",
            "inventory": 25,
            "type": "physical",
            "featured": False
        },
        {
            "name": "Digital Marketing eBook",
            "description": "Comprehensive guide to digital marketing strategies, SEO, and social media marketing. PDF format.",
            "price": 19.99,
            "images": ["data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgdmlld0JveD0iMCAwIDIwMCAyMDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxyZWN0IHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIiBmaWxsPSIjRjNGNEY2Ii8+CjxyZWN0IHg9IjYwIiB5PSI0MCIgd2lkdGg9IjgwIiBoZWlnaHQ9IjEyMCIgZmlsbD0iIzEwOTkzRSIvPgo8cmVjdCB4PSI3MCIgeT0iNjAiIHdpZHRoPSI2MCIgaGVpZ2h0PSI0IiBmaWxsPSJ3aGl0ZSIvPgo8cmVjdCB4PSI3MCIgeT0iNzAiIHdpZHRoPSI0MCIgaGVpZ2h0PSI0IiBmaWxsPSJ3aGl0ZSIvPgo8cmVjdCB4PSI3MCIgeT0iODAiIHdpZHRoPSI2MCIgaGVpZ2h0PSI0IiBmaWxsPSJ3aGl0ZSIvPgo8cmVjdCB4PSI3MCIgeT0iOTAiIHdpZHRoPSI1MCIgaGVpZ2h0PSI0IiBmaWxsPSJ3aGl0ZSIvPgo8L3N2Zz4K"],
            "category": "Digital Products",
            "inventory": 9999,
            "type": "digital",
            "featured": False
        }
    ]
    
    # Check if products already exist
    existing_products = await db.products.count_documents({})
    if existing_products == 0:
        product_objects = [Product(**prod) for prod in sample_products]
        await db.products.insert_many([prod.dict() for prod in product_objects])
    
    # Sample promo codes
    promo_codes = [
        {
            "code": "WELCOME10",
            "discount_percentage": 10.0,
            "min_order_amount": 50.0,
            "active": True
        },
        {
            "code": "SAVE20",
            "discount_percentage": 20.0,
            "min_order_amount": 100.0,
            "active": True
        },
        {
            "code": "NEWUSER",
            "discount_percentage": 15.0,
            "min_order_amount": 30.0,
            "active": True
        }
    ]
    
    # Check if promo codes already exist
    existing_promos = await db.promo_codes.count_documents({})
    if existing_promos == 0:
        promo_objects = [PromoCode(**promo) for promo in promo_codes]
        await db.promo_codes.insert_many([promo.dict() for promo in promo_objects])
    
    return {
        "message": "Sample data initialized successfully",
        "categories": len(categories),
        "products": len(sample_products),
        "promo_codes": len(promo_codes)
    }

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()