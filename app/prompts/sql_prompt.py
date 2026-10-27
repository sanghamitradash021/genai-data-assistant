"""Prompt building blocks for the text-to-SQL agent (app/agents/sql_agent.py): the business-rules
glossary appended after the live schema, and the few-shot SQL examples."""

BUSINESS_RULES = """\
Business rules:
- Revenue = SUM(orders.total_amount) over orders with status = 'completed' (ALWAYS add that filter).
- orders.total_amount is already the whole-order total: NEVER join order_items when summing it (double counting).
  Only join order_items/products for product-level questions, using SUM(quantity * unit_price).
- Money refunded = SUM(refunds.amount); the date of a refund is refunds.refunded_at (NOT orders.order_date).
- "Last month" = the previous calendar month: refunded_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
  AND refunded_at < date_trunc('month', CURRENT_DATE).
- order_items.unit_price * order_items.quantity = line revenue."""

SQL_EXAMPLES = """\
Q: Which are the top 5 customers by revenue?
SQL: SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.status = 'completed' GROUP BY c.id, c.name ORDER BY revenue DESC LIMIT 5

Q: How much was refunded last month?
SQL: SELECT COALESCE(SUM(amount), 0) AS total_refunded FROM refunds WHERE refunded_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' AND refunded_at < date_trunc('month', CURRENT_DATE)

Q: Show monthly revenue for the last 3 months
SQL: SELECT TO_CHAR(date_trunc('month', order_date), 'FMMonth YYYY') AS month, SUM(total_amount) AS revenue FROM orders WHERE status = 'completed' AND order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '3 month' GROUP BY date_trunc('month', order_date) ORDER BY date_trunc('month', order_date)"""
