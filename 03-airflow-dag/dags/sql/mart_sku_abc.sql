-- mart.sku_abc — ABC-анализ ассортимента за 30 дней.
-- Классификация по накопленной доле выручки (правило Парето):
--   A — 80% выручки, B — следующие 15%, C — остальное.
-- Пересчёт заново на каждый запуск: партиция по calc_date удаляется целиком,
-- поэтому повторный запуск в тот же день не задваивает строки.

BEGIN;

DELETE FROM mart.sku_abc WHERE calc_date = CURRENT_DATE;

WITH sku_stats AS (
    SELECT
        sku,
        category,
        SUM(amount) AS revenue,
        SUM(units)  AS units
    FROM stg.orders_daily
    WHERE order_date >= CURRENT_DATE - INTERVAL '30 days'
      AND NOT is_canceled
      AND NOT is_returned
    GROUP BY sku, category
),
ranked AS (
    SELECT
        sku,
        category,
        revenue,
        units,
        ROW_NUMBER() OVER (ORDER BY revenue DESC, sku)                       AS rn,
        SUM(revenue) OVER (ORDER BY revenue DESC, sku
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)  AS cum_revenue,
        SUM(revenue) OVER ()                                                 AS total_revenue
    FROM sku_stats
)
INSERT INTO mart.sku_abc (
    calc_date, sku, category, revenue, units, rank, cum_share, abc_class
)
SELECT
    CURRENT_DATE,
    sku,
    category,
    revenue,
    units,
    rn,
    ROUND(cum_revenue / NULLIF(total_revenue, 0), 4) AS cum_share,
    CASE
        WHEN cum_revenue / NULLIF(total_revenue, 0) <= 0.80 THEN 'A'
        WHEN cum_revenue / NULLIF(total_revenue, 0) <= 0.95 THEN 'B'
        ELSE 'C'
    END AS abc_class
FROM ranked;

COMMIT;