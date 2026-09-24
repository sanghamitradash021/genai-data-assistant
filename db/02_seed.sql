-- Deterministic sample data; dates are relative to today so "last month" queries work.
SELECT setseed(0.42);

INSERT INTO customers (name, email, country, segment, created_at) VALUES
 ('Alice Johnson',    'alice@example.com',   'USA',       'enterprise',     current_date - 400),
 ('Bob Smith',        'bob@example.com',     'USA',       'consumer',       current_date - 380),
 ('Carla Gomez',      'carla@example.com',   'Spain',     'small_business', current_date - 350),
 ('Dmitri Ivanov',    'dmitri@example.com',  'Germany',   'enterprise',     current_date - 340),
 ('Emma Wilson',      'emma@example.com',    'UK',        'consumer',       current_date - 330),
 ('Farid Khan',       'farid@example.com',   'India',     'small_business', current_date - 320),
 ('Grace Lee',        'grace@example.com',   'Canada',    'enterprise',     current_date - 300),
 ('Hiro Tanaka',      'hiro@example.com',    'Japan',     'consumer',       current_date - 290),
 ('Isabel Costa',     'isabel@example.com',  'Brazil',    'small_business', current_date - 280),
 ('Jack Brown',       'jack@example.com',    'Australia', 'consumer',       current_date - 270),
 ('Kavya Rao',        'kavya@example.com',   'India',     'enterprise',     current_date - 260),
 ('Liam O''Connor',   'liam@example.com',    'Ireland',   'consumer',       current_date - 250),
 ('Mia Schmidt',      'mia@example.com',     'Germany',   'small_business', current_date - 240),
 ('Noah Martin',      'noah@example.com',    'France',    'consumer',       current_date - 230),
 ('Olivia Chen',      'olivia@example.com',  'Singapore', 'enterprise',     current_date - 220),
 ('Pedro Alvarez',    'pedro@example.com',   'Mexico',    'small_business', current_date - 210),
 ('Quinn Taylor',     'quinn@example.com',   'USA',       'consumer',       current_date - 200),
 ('Rosa Mendes',      'rosa@example.com',    'Portugal',  'consumer',       current_date - 190),
 ('Sven Larsson',     'sven@example.com',    'Sweden',    'small_business', current_date - 180),
 ('Tara Patel',       'tara@example.com',    'UK',        'enterprise',     current_date - 170);

INSERT INTO products (name, category, price, stock) VALUES
 ('Laptop Pro 14',        'Computers',   1499.00, 40),
 ('Laptop Air 13',        'Computers',    999.00, 65),
 ('Desktop Tower X',      'Computers',   1299.00, 25),
 ('Wireless Mouse',       'Accessories',   29.99, 500),
 ('Mechanical Keyboard',  'Accessories',   89.50, 300),
 ('USB-C Hub',            'Accessories',   49.00, 420),
 ('27" Monitor',          'Displays',     329.00, 120),
 ('34" Ultrawide Monitor','Displays',     599.00, 60),
 ('Noise-Cancel Headset', 'Audio',        199.00, 150),
 ('Bluetooth Speaker',    'Audio',         79.00, 220),
 ('Webcam HD',            'Accessories',   59.00, 260),
 ('Docking Station',      'Accessories',  189.00, 90),
 ('Tablet 10"',           'Tablets',      449.00, 110),
 ('Smartphone S',         'Phones',       799.00, 140),
 ('Smartwatch Fit',       'Wearables',    249.00, 130);

INSERT INTO orders (customer_id, order_date, status)
SELECT 1 + floor(random() * 20)::int,
       current_date - (1 + floor(random() * 179))::int,
       CASE WHEN r < 0.80 THEN 'completed'
            WHEN r < 0.88 THEN 'pending'
            WHEN r < 0.94 THEN 'cancelled'
            ELSE 'refunded' END
FROM (SELECT random() AS r FROM generate_series(1, 300)) s;

-- Guarantee some refunds in the previous calendar month.
UPDATE orders SET status = 'refunded'
WHERE id IN (
    SELECT id FROM orders
    WHERE order_date >= date_trunc('month', current_date) - interval '1 month'
      AND order_date <  date_trunc('month', current_date) - interval '6 days'
      AND status = 'completed'
    ORDER BY id LIMIT 4);

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT s.order_id, p.id, s.qty, p.price
FROM (SELECT o.id AS order_id,
             1 + floor(random() * 15)::int AS pid,
             1 + floor(random() * 3)::int  AS qty
      FROM orders o
      CROSS JOIN generate_series(1, 1 + (o.id % 3)) k) s
JOIN products p ON p.id = s.pid;

UPDATE orders o SET total_amount = t.total
FROM (SELECT order_id, SUM(quantity * unit_price) AS total FROM order_items GROUP BY order_id) t
WHERE t.order_id = o.id;

INSERT INTO refunds (order_id, amount, reason, refunded_at)
SELECT o.id,
       CASE WHEN o.id % 3 = 0 THEN round(o.total_amount * 0.5, 2) ELSE o.total_amount END,
       (ARRAY['defective product', 'wrong item shipped', 'changed mind', 'late delivery'])[1 + o.id % 4],
       LEAST(o.order_date + (1 + o.id % 5), current_date)
FROM orders o
WHERE o.status = 'refunded';
