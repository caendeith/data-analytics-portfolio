-- Топ-10 протоколов по TVL с недельной динамикой.
SELECT
    name,
    category,
    chain,
    tvl,
    ROUND(change_7d, 2) AS change_7d
FROM protocols
WHERE tvl > 0
ORDER BY tvl DESC
LIMIT 10;
