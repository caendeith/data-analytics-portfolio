-- Сильнейшие движения недели среди крупных протоколов (TVL > 50 млн).
-- Мелкие протоколы отсекаем: у них проценты скачут от одного транзакционного дня.
SELECT
    name,
    category,
    tvl,
    ROUND(change_7d, 2) AS change_7d
FROM protocols
WHERE tvl > 50000000 AND change_7d IS NOT NULL
ORDER BY change_7d DESC;
