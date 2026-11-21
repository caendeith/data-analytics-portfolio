-- Топ сетей по заблокированной стоимости (TVL), с долей от всего DeFi TVL.
-- Параметр :total подставляет build.py; в Dune-версии считается подзапросом.
SELECT
    name,
    tvl,
    ROUND(tvl * 100.0 / :total, 2) AS share_pct
FROM chains
WHERE tvl > 0
ORDER BY tvl DESC
LIMIT 12;
