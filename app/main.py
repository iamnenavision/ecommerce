from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from passlib.context import CryptContext
from datetime import datetime, timedelta
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, Dict, List


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html", "r") as file:
        return file.read()

@app.get("/login", response_class=HTMLResponse)
async def read_login():
    with open("static/login.html", "r") as file:
        return file.read()
    
@app.get("/shop", response_class=HTMLResponse)
async def read_shop():
    with open("static/shop.html", "r") as file:
        return file.read()
    
@app.get("/recommended", response_class=HTMLResponse)
async def read_recommended():
    with open("static/recommended.html", "r") as file:
        return file.read()
    
@app.get("/checkout", response_class=HTMLResponse)
async def read_checkout():
    with open("static/checkout.html", "r") as file:
        return file.read()

@app.get("/thank-you", response_class=HTMLResponse)
async def read_thank_you():
    with open("static/thank-you.html", "r") as file:
        return file.read()

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@db/shop")
SECRET_KEY = os.getenv("SECRET_KEY", "secret_key")  
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserCreate(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    created_at: datetime

    class Config:
        from_attributes = True

class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None

class CategoryResponse(BaseModel):
    id: int
    name: str
    parent_id: Optional[int]

    class Config:
        from_attributes = True

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    stock: int
    category_id: Optional[int] = None
    attributes: Optional[Dict] = None

class ProductResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    stock: int
    category_id: Optional[int]
    attributes: Optional[Dict]
    created_at: datetime

    class Config:
        from_attributes = True

class CartItemCreate(BaseModel):
    product_id: int
    quantity: int

from pydantic import Field

class CartItemResponse(BaseModel):
    id: int
    cartId: int = Field(alias='cart_id')
    productId: int = Field(alias='product_id')
    quantity: int
    name: str
    price: float

    class Config:
        from_attributes = True

class OrderCreate(BaseModel):
    user_id: int
    total_amount: float
    status: str
    payment_id: Optional[str] = None

class OrderResponse(BaseModel):
    id: int
    user_id: int
    total_amount: float
    status: str
    payment_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class OrderItemResponse(BaseModel):
    id: int
    order_id: int
    product_id: int
    quantity: int
    price: float

    class Config:
        from_attributes = True


class PaymentInfo(BaseModel):
    pan: str  
    cvv: str  
    expiration_date: str  
    user_id: int  
    total_amount: float  


@app.post("/register", response_model=UserResponse)
def register_user(user: UserCreate, db: psycopg2.extensions.connection = Depends(get_db)):
    hashed_password = pwd_context.hash(user.password)
    email = user.email.lower()
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (name, email, password, role) VALUES (%s, %s, %s, 'покупатель') RETURNING id, name, email, role, created_at",
            (user.name, email, hashed_password),
        )
        new_user = cursor.fetchone()
        db.commit()
        return new_user


@app.post("/login")
def login_user(user: UserLogin, db: psycopg2.extensions.connection = Depends(get_db)):
    email = user.email.lower()
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        db_user = cursor.fetchone()
        if not db_user or not pwd_context.verify(user.password, db_user["password"]):
            raise HTTPException(status_code=400, detail="Incorrect email or password")
        
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": db_user["email"]}, expires_delta=access_token_expires
        )
        return {"access_token": access_token, "token_type": "bearer", "user_id": db_user["id"]}


@app.post("/purchase")
async def create_purchase(payment_info: PaymentInfo, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/purchase")
    try:
        expiration_date = payment_info.expiration_date
        if not expiration_date or len(expiration_date) != 5 or expiration_date[2] != '/':
            raise HTTPException(status_code=400, detail="Invalid expiration date format. Use MM/YY.")

        with db.cursor() as cursor:
            cursor.execute(
                "INSERT INTO orders (user_id, total_amount, status, payment_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (payment_info.user_id, payment_info.total_amount, 'в обработке', f"payment_{payment_info.pan[-4:]}", datetime.utcnow()),
            )
            order_id = cursor.fetchone()["id"]

            cursor.execute("""
                SELECT ci.product_id, ci.quantity, p.price
                FROM cart_items ci
                JOIN cart c ON ci.cart_id = c.id
                JOIN products p ON ci.product_id = p.id
                WHERE c.user_id = %s
            """, (payment_info.user_id,))
            cart_items = cursor.fetchall()

            for item in cart_items:
                cursor.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity, price) "
                    "VALUES (%s, %s, %s, %s)",
                    (order_id, item["product_id"], item["quantity"], item["price"]),
                )

            cursor.execute("DELETE FROM cart_items WHERE cart_id IN (SELECT id FROM cart WHERE user_id = %s)", (payment_info.user_id,))

            db.commit()

        return {"detail": "Purchase successful!", "order_id": order_id}
    except Exception as e:
        db.rollback()
        print(f"Error creating purchase: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while processing the purchase.")
    

async def get_current_user(token: str = Depends(oauth2_scheme), db: psycopg2.extensions.connection = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        if user is None:
            raise credentials_exception
        return user


@app.get("/users/me", response_model=UserResponse)
def get_current_user(current_user: dict = Depends(get_current_user)):
    return current_user

@app.get("/users/{user_id}", response_model=UserResponse)
def read_user(user_id: int, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/users")
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user


@app.post("/categories", response_model=CategoryResponse)
def create_category(category: CategoryCreate, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/categories create")
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO categories (name, parent_id) VALUES (%s, %s) RETURNING id, name, parent_id",
            (category.name, category.parent_id),
        )
        new_category = cursor.fetchone()
        db.commit()
        return new_category
    

@app.get("/categories", response_model=List[CategoryResponse])
def get_categories(db: psycopg2.extensions.connection = Depends(get_db)):
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM categories")
        categories = cursor.fetchall()
        return categories


@app.post("/products", response_model=ProductResponse)
def create_product(product: ProductCreate, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/products create")
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO products (name, description, price, stock, category_id, attributes, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, name, description, price, stock, category_id, attributes, created_at",
            (product.name, product.description, product.price, product.stock, product.category_id, product.attributes, datetime.utcnow()),
        )
        new_product = cursor.fetchone()
        db.commit()
        return new_product
    

@app.get("/products", response_model=List[ProductResponse])
def get_products(db: psycopg2.extensions.connection = Depends(get_db)):
    print("/products")
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM products")
        products = cursor.fetchall()
        return products


@app.get("/recommendations", response_model=List[ProductResponse])
async def get_recommendations(user_id: int, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/recommendations")
    with db.cursor() as cursor:
        cursor.execute("""
            SELECT p.*
            FROM recommendations r
            JOIN products p ON r.product_id = p.id
            WHERE r.user_id = %s
        """, (user_id,))
        recommended_products = cursor.fetchall()
        return recommended_products
    

@app.get("/cart", response_model=List[CartItemResponse])
def get_cart(user_id: int, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/cart")
    with db.cursor() as cursor:
        cursor.execute("""
            SELECT p.name, p.price, ci.id, ci.cart_id, ci.product_id, ci.quantity
            FROM cart_items ci
            JOIN cart c ON ci.cart_id = c.id
            JOIN products p ON ci.product_id = p.id
            WHERE c.user_id = %s
        """, (user_id,))
        cart_items = cursor.fetchall()
        cart_items = [
            {
                'name': item['name'],
                'price': float(item['price']),
                'id': item['id'],
                'cart_id': item['cart_id'],
                'product_id': item['product_id'],
                'quantity': item['quantity'],
            }
            for item in cart_items
        ]
        print(cart_items)
        return cart_items

@app.post("/cart/items", response_model=CartItemResponse)
def add_cart_item(item: CartItemCreate, user_id: int, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/cart/items")
    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM cart WHERE user_id = %s", (user_id,))
        cart = cursor.fetchone()
        if not cart:
            cursor.execute("INSERT INTO cart (user_id, created_at) VALUES (%s, NOW()) RETURNING id", (user_id,))
            cart = cursor.fetchone()
        
        cursor.execute("""
            INSERT INTO cart_items (cart_id, product_id, quantity)
            VALUES (%s, %s, %s)
            RETURNING id, cart_id, product_id, quantity
        """, (cart["id"], item.product_id, item.quantity))
        new_item = cursor.fetchone()
        
        cursor.execute("SELECT name, price FROM products WHERE id = %s", (new_item["product_id"],))
        product = cursor.fetchone()
        
        new_item_with_product = {
            **new_item,
            "name": product["name"],
            "price": float(product["price"])
        }
        
        db.commit()
        return new_item_with_product
    

@app.delete("/cart/items/{cart_item_id}")
def remove_cart_item(cart_item_id: int, user_id: int, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/cart/items/delete")
    with db.cursor() as cursor:
        cursor.execute("""
            SELECT ci.id
            FROM cart_items ci
            JOIN cart c ON ci.cart_id = c.id
            WHERE ci.id = %s AND c.user_id = %s
        """, (cart_item_id, user_id))
        item = cursor.fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Cart item not found")
        
        cursor.execute("DELETE FROM cart_items WHERE id = %s", (cart_item_id,))
        db.commit()
    return {"detail": "Cart item removed successfully"}


@app.post("/orders", response_model=OrderResponse)
def create_order(order: OrderCreate, db: psycopg2.extensions.connection = Depends(get_db)):
    print("/orders")
    with db.cursor() as cursor:
        cursor.execute(
            "INSERT INTO orders (user_id, total_amount, status, payment_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id, user_id, total_amount, status, payment_id, created_at",
            (order.user_id, order.total_amount, order.status, order.payment_id, datetime.utcnow()),
        )
        new_order = cursor.fetchone()
        db.commit()
        return new_order
    

def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


@app.on_event("startup")
def startup_event():
    db = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        # pass
        
        drop_all_tables(db)
        create_tables(db)
        insert_sample_data(db)
    finally:
        db.close()


def drop_all_tables(db: psycopg2.extensions.connection):
    try:
        with db.cursor() as cursor:
            cursor.execute(
            """
                DROP TABLE IF EXISTS users CASCADE;
                DROP TABLE IF EXISTS categories CASCADE;
                DROP TABLE IF EXISTS products CASCADE;
                DROP TABLE IF EXISTS cart CASCADE;
                DROP TABLE IF EXISTS cart_items CASCADE;
                DROP TABLE IF EXISTS orders CASCADE;
                DROP TABLE IF EXISTS order_items CASCADE;
                DROP TABLE IF EXISTS user_logs CASCADE;
                DROP TABLE IF EXISTS recommendations CASCADE;
            """)
    except Exception as e:
        db.rollback()
        print(f"Error dropping tables: {e}")


def create_tables(db: psycopg2.extensions.connection):
    try:
        with db.cursor() as cursor:

            
            cursor.execute("""
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL CHECK (role IN ('покупатель', 'администратор')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL UNIQUE,  -- Add UNIQUE constraint
                    parent_id INT REFERENCES categories(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL UNIQUE,  -- Add UNIQUE constraint
                    description TEXT,
                    price DECIMAL(10, 2) NOT NULL CHECK (price >= 0),
                    stock INT NOT NULL CHECK (stock >= 0),
                    category_id INT REFERENCES categories(id) ON DELETE SET NULL,
                    attributes JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS cart (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS cart_items (
                    id SERIAL PRIMARY KEY,
                    cart_id INT REFERENCES cart(id) ON DELETE CASCADE,
                    product_id INT REFERENCES products(id) ON DELETE CASCADE,
                    quantity INT NOT NULL CHECK (quantity > 0)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    total_amount DECIMAL(10, 2) NOT NULL CHECK (total_amount >= 0),
                    status VARCHAR(50) NOT NULL CHECK (status IN ('в обработке', 'отправлен', 'доставлен')),
                    payment_id VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id SERIAL PRIMARY KEY,
                    order_id INT REFERENCES orders(id) ON DELETE CASCADE,
                    product_id INT REFERENCES products(id) ON DELETE CASCADE,
                    quantity INT NOT NULL CHECK (quantity > 0),
                    price DECIMAL(10, 2) NOT NULL CHECK (price >= 0)
                );

                CREATE TABLE IF NOT EXISTS user_logs (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    action VARCHAR(255) NOT NULL,
                    product_id INT REFERENCES products(id) ON DELETE SET NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    id SERIAL PRIMARY KEY,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    product_id INT REFERENCES products(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            db.commit()
            print("Tables created successfully!")
    except Exception as e:
        db.rollback()
        print(f"Error creating tables: {e}")


def insert_sample_data(db: psycopg2.extensions.connection):
    print("Inserting sample data into categories and products...")
    cursor = db.cursor()
    try:
        cursor.execute("""
            -- Заполнение таблицы пользователей
            INSERT INTO users (name, email, password, role)
            VALUES
                ('Иван Иванов', 'ivanov@example.com', %s, 'покупатель'),
                ('Мария Петрова', 'petrova@example.com', %s, 'покупатель'),
                ('Алексей Сидоров', 'sidorov@example.com', %s, 'администратор'),
                ('Наталья Смирнова', 'smirnova@example.com', %s, 'покупатель'),
                ('Дмитрий Кузнецов', 'kuznetsov@example.com', %s, 'покупатель'),
                ('Ольга Васильева', 'vasilieva@example.com', %s, 'покупатель'),
                ('Екатерина Зайцева', 'zaytseva@example.com', %s, 'покупатель'),
                ('Игорь Беляев', 'belyaev@example.com', %s, 'покупатель'),
                ('Марина Орлова', 'orlova@example.com', %s, 'покупатель'),
                ('Юрий Михайлов', 'mikhaylov@example.com', %s, 'администратор'),
                ('Татьяна Федорова', 'fedorova@example.com', %s, 'покупатель'),
                ('Константин Соловьев', 'solovyov@example.com', %s, 'покупатель')
            ON CONFLICT (email) DO NOTHING;
        """, (pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123"),
               pwd_context.hash("password123")))

        
        cursor.execute("""
            INSERT INTO categories (name, parent_id)
            VALUES
                ('Электроника', NULL),
                ('Одежда', NULL),
                ('Обувь', NULL),
                ('Товары для дома', NULL),
                ('Товары для детей', NULL)
            ON CONFLICT (name) DO NOTHING;
        """)

        
        cursor.execute("""
            SELECT id, name FROM categories WHERE name IN (
                'Электроника', 'Одежда', 'Обувь', 'Товары для дома', 'Товары для детей'
            );
        """)
        parent_categories = {row['name']: row['id'] for row in cursor.fetchall()}

        
        cursor.execute("""
            INSERT INTO categories (name, parent_id)
            VALUES
                ('Компьютеры', %(electronics_id)s),
                ('Смартфоны', %(electronics_id)s),
                ('Телевизоры', %(electronics_id)s),
                ('Мужская одежда', %(clothing_id)s),
                ('Женская одежда', %(clothing_id)s),
                ('Спортивная обувь', %(shoes_id)s),
                ('Повседневная обувь', %(shoes_id)s),
                ('Мебель', %(home_goods_id)s),
                ('Декор', %(home_goods_id)s),
                ('Кухня', %(home_goods_id)s),
                ('Игрушки', %(kids_goods_id)s),
                ('Детская одежда', %(kids_goods_id)s)
            ON CONFLICT (name) DO NOTHING;
        """, {
            'electronics_id': parent_categories['Электроника'],
            'clothing_id': parent_categories['Одежда'],
            'shoes_id': parent_categories['Обувь'],
            'home_goods_id': parent_categories['Товары для дома'],
            'kids_goods_id': parent_categories['Товары для детей']
        })

        
        cursor.execute("SELECT id, name FROM categories;")
        categories = {row['name']: row['id'] for row in cursor.fetchall()}

        
        cursor.execute("""
            INSERT INTO products (name, description, price, stock, category_id, attributes)
            VALUES
                ('Ноутбук', 'Мощный ноутбук для работы и игр', 50000.00, 10, %(computers_id)s, '{"color": "черный", "processor": "Intel i7", "ram": "16GB"}'),
                ('Смартфон', 'Современный смартфон с отличной камерой', 25000.00, 15, %(smartphones_id)s, '{"color": "белый", "camera": "12MP", "battery": "4000mAh"}'),
                ('Телевизор', 'Ультра HD телевизор с поддержкой Smart TV', 35000.00, 8, %(tvs_id)s, '{"size": "55 inch", "type": "LED", "resolution": "4K"}'),
                ('Футболка', 'Стильная мужская футболка', 1500.00, 50, %(mens_clothing_id)s, '{"size": "M", "color": "синий"}'),
                ('Платье', 'Элегантное платье для особых случаев', 3000.00, 30, %(womens_clothing_id)s, '{"size": "S", "color": "красный"}'),
                ('Кроссовки', 'Удобные кроссовки для спорта', 4000.00, 25, %(sports_shoes_id)s, '{"size": "42", "color": "черный"}'),
                ('Сандалии', 'Летние сандалии для отдыха', 2000.00, 40, %(casual_shoes_id)s, '{"size": "38", "color": "бежевый"}'),
                ('Кресло', 'Удобное кресло для офиса', 8000.00, 15, %(furniture_id)s, '{"color": "черный", "material": "кожа"}'),
                ('Кровать', 'Комфортная двуспальная кровать с матрасом', 25000.00, 20, %(furniture_id)s, '{"material": "дерево", "size": "King"}'),
                ('Игрушечный робот', 'Интерактивный робот для детей', 1500.00, 50, %(toys_id)s, '{"battery": "AA", "color": "красный"}'),
                ('Детская футболка', 'Яркая футболка для детей', 800.00, 60, %(kids_clothing_id)s, '{"size": "L", "color": "голубой"}'),
                ('aaaaa', 'sadf', 800.00, 60, %(kids_clothing_id)s, '{"size": "L", "color": "blue"}')
            ON CONFLICT (name) DO NOTHING;
        """, {
            'computers_id': categories['Компьютеры'],
            'smartphones_id': categories['Смартфоны'],
            'tvs_id': categories['Телевизоры'],
            'mens_clothing_id': categories['Мужская одежда'],
            'womens_clothing_id': categories['Женская одежда'],
            'sports_shoes_id': categories['Спортивная обувь'],
            'casual_shoes_id': categories['Повседневная обувь'],
            'furniture_id': categories['Мебель'],
            'toys_id': categories['Игрушки'],
            'kids_clothing_id': categories['Детская одежда']
        })

        cursor.execute("""
                -- Заполнение таблицы корзин
                INSERT INTO cart (user_id)
                VALUES
                    (1), (2), (3), (4), (5),
                    (6), (7), (8), (9), (10);

                -- Заполнение таблицы товаров в корзине
                INSERT INTO cart_items (cart_id, product_id, quantity)
                VALUES
                    (1, 1, 1), (1, 2, 2), (2, 3, 1), (2, 4, 3), (3, 5, 1),
                    (3, 6, 1), (4, 7, 1), (4, 8, 2), (5, 9, 1), (6, 10, 1),
                    (7, 11, 2), (8, 12, 1);

                -- Заполнение таблицы заказов
                INSERT INTO orders (user_id, total_amount, status, payment_id)
                VALUES
                    (1, 55000.00, 'в обработке', 'payment_1'),
                    (2, 70000.00, 'отправлен', 'payment_2'),
                    (3, 30000.00, 'доставлен', 'payment_3'),
                    (4, 12000.00, 'в обработке', 'payment_4'),
                    (5, 10000.00, 'отправлен', 'payment_5'),
                    (6, 8000.00, 'доставлен', 'payment_6'),
                    (7, 25000.00, 'в обработке', 'payment_7'),
                    (8, 20000.00, 'отправлен', 'payment_8'),
                    (9, 15000.00, 'доставлен', 'payment_9'),
                    (10, 40000.00, 'в обработке', 'payment_10');

                -- Заполнение таблицы товаров в заказе
                INSERT INTO order_items (order_id, product_id, quantity, price)
                VALUES
                    (1, 1, 1, 50000.00), (1, 2, 2, 1500.00),
                    (2, 3, 1, 35000.00), (2, 4, 3, 1500.00),
                    (3, 5, 1, 3000.00), (3, 6, 1, 4000.00),
                    (4, 7, 1, 2000.00), (4, 8, 2, 1000.00),
                    (5, 9, 1, 8000.00), (6, 10, 1, 1500.00),
                    (7, 11, 2, 1500.00), (8, 12, 1, 12000.00);

                -- Заполнение таблицы логов пользователей
                INSERT INTO user_logs (user_id, action, product_id)
                VALUES
                    (1, 'Добавил товар в корзину', 1), (1, 'Перешел к оформлению заказа', NULL),
                    (2, 'Просмотрел товар', 3), (2, 'Добавил товар в корзину', 4),
                    (3, 'Удалил товар из корзины', 5), (4, 'Перешел к оформлению заказа', NULL),
                    (5, 'Добавил товар в корзину', 7), (6, 'Просмотрел товар', 9),
                    (7, 'Добавил товар в корзину', 11), (8, 'Удалил товар из корзины', 12);

                -- Заполнение таблицы рекомендаций
                INSERT INTO recommendations (user_id, product_id)
                VALUES
                    (1, 2), (1, 3), (2, 4), (3, 5),
                    (4, 6), (5, 7), (6, 8), (7, 9),
                    (8, 10), (9, 11), (10, 12);
""")

        db.commit()
        print("Sample data inserted successfully!")
    except Exception as e:
        print(f"Error inserting sample data: {e}")
    finally:
        cursor.close()