-- mart.daily_revenue — выручка по дням.
-- Пересобираем скользящее окно 14 дней, а не только вчерашний день:
-- заказы, отмены и возвраты приходят задним числом, и вчерашняя цифра
-- через три дня уже другая. Окно + ON CONFLICT делают запуск идемпотентным.

BEGIN;

DELETE FROM mart.daily_revenue
 WHERE order_date >= CURRENT_DATE - INTERVAL '14 days';

INSERT INTO mart.daily_revenue (
    order_date, orders, revenue, avg_order_value, units, canceled, returned
)
SELECT
    order_date,
    COUNT(*)  FILTER (WHERE NOT is_canceled)                                  AS orders,
    COALESCE(SUM(amount) FILTER (WHERE NOT is_canceled), 0)                   AS revenue,
    ROUND(
        COALESCE(SUM(amount) FILTER (WHERE NOT is_canceled), 0)
        / NULLIF(COUNT(*) FILTER (WHERE NOT is_canceled), 0), 2
    )                                                                         AS avg_order_value,
    COALESCE(SUM(units) FILTER (WHERE NOT is_canceled), 0)                    AS units,
    COUNT(*)  FILTER (WHERE is_canceled)                                      AS canceled,
    COUNT(*)  FILTER (WHERE is_returned)                                      AS returned
FROM stg.orders_daily
WHERE order_date >= CURRENT_DATE - INTERVAL '14 days'
GROUP BY order_date
ON CONFLICT (order_date) DO UPDATE SET
    orders          = EXCLUDED.orders,
    revenue         = EXCLUDED.revenue,
    avg_order_value = EXCLUDED.avg_order_value,
    units           = EXCLUDED.units,
    canceled        = EXCLUDED.canceled,
    returned        = EXCLUDED.returned,
    updated_at      = NOW();

COMMIT;