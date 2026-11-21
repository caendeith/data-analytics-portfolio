-- Категории протоколов: сколько проектов и сколько денег в каждом сегменте.
SELECT
    category,
    COUNT(*)        AS protocols,
    SUM(tvl)        AS tvl,
    ROUND(AVG(tvl), 0) AS avg_tvl
FROM protocols
WHERE tvl > 0 AND category IS NOT NULL AND category <> ''
GROUP BY category
ORDER BY tvl DESC
LIMIT 12;
