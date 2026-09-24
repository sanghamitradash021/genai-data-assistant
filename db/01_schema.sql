CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    country     TEXT NOT NULL,
    segment     TEXT NOT NULL CHECK (segment IN ('consumer', 'small_business', 'enterprise')),
    created_at  DATE NOT NULL
);

CREATE TABLE products (
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL,
    category  TEXT NOT NULL,
    price     NUMERIC(10,2) NOT NULL CHECK (price >= 0),
    stock     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id            SERIAL PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers(id),
    order_date    DATE NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('completed', 'pending', 'cancelled', 'refunded')),
    total_amount  NUMERIC(12,2) NOT NULL DEFAULT 0
);

CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL
);

CREATE TABLE refunds (
    id           SERIAL PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    amount       NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    reason       TEXT NOT NULL,
    refunded_at  DATE NOT NULL
);

-- Audit log of every NL->SQL attempt. Written by the API's admin role only;
-- the read-only role used to execute generated SQL cannot even see it.
CREATE TABLE query_log (
    id             BIGSERIAL PRIMARY KEY,
    asked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    question       TEXT NOT NULL,
    generated_sql  TEXT,
    status         TEXT NOT NULL,          -- ok | rejected | error
    error          TEXT,
    row_count      INTEGER,
    duration_ms    INTEGER
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_refunds_date ON refunds(refunded_at);
