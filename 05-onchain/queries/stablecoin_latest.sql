-- Последнее значение денежной массы стейблкоинов и точка отсчёта.
SELECT
    day,
    usd
FROM stablecoin_daily
ORDER BY ts DESC
LIMIT 1;
