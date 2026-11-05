-- Схема и наполнение для локального стенда.
-- Выполняется автоматически при первом старте контейнера postgres
-- (монтируется в /docker-entrypoint-initdb.d/).
-- Здесь же лежит DDL всех слоёв — чтобы можно было поднять стек
-- одной командой и увидеть, как DAG реально отрабатывает.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS bi;

-- ---------------------------------------------------------------- raw
CREATE TABLE IF NOT EXISTS raw.orders (
    order_id     bigint PRIMARY KEY,
    order_date   date        NOT NULL,
    sku          text        NOT NULL,
    category     text        NOT NULL,
    region       text        NOT NULL,
    amount       numeric(12, 2) NOT NULL,
    units        integer     NOT NULL DEFAULT 1,
    is_canceled  boolean     NOT NULL DEFAULT false,
    is_returned  boolean     NOT NULL DEFAULT false,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_orders_date_idx ON raw.orders (order_date);
CREATE INDEX IF NOT EXISTS raw_orders_updated_idx ON raw.orders (updated_at);

-- ---------------------------------------------------------------- stg
CREATE TABLE IF NOT EXISTS stg.orders_daily (
    order_id     bigint PRIMARY KEY,
    order_date   date        NOT NULL,
    sku          text        NOT NULL,
    category     text        NOT NULL,
    region       text        NOT NULL,
    amount       numeric(12, 2) NOT NULL,
    units        integer     NOT NULL DEFAULT 1,
    is_canceled  boolean     NOT NULL DEFAULT false,
    is_returned  boolean     NOT NULL DEFAULT false,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stg_orders_daily_date_idx ON stg.orders_daily (order_date);

-- ---------------------------------------------------------------- mart
CREATE TABLE IF NOT EXISTS mart.daily_revenue (
    order_date      date PRIMARY KEY,
    orders          bigint        NOT NULL,
    revenue         numeric(14, 2) NOT NULL,
    avg_order_value numeric(12, 2) NOT NULL,
    units           bigint        NOT NULL,
    canceled        bigint        NOT NULL,
    returned        bigint        NOT NULL,
    updated_at      timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mart.sku_abc (
    calc_date date    NOT NULL,
    sku       text    NOT NULL,
    category  text    NOT NULL,
    revenue   numeric(14, 2) NOT NULL,
    units     bigint  NOT NULL,
    rank      bigint  NOT NULL,
    cum_share numeric(6, 4)  NOT NULL,
    abc_class char(1) NOT NULL CHECK (abc_class IN ('A', 'B', 'C')),
    PRIMARY KEY (calc_date, sku)
);

-- ---------------------------------------------------------------- bi
DROP MATERIALIZED VIEW IF EXISTS bi.revenue_daily;
CREATE MATERIALIZED VIEW bi.revenue_daily AS
SELECT order_date, orders, revenue, avg_order_value, units, canceled, returned
FROM mart.daily_revenue;

-- REFRESH ... CONCURRENTLY требует уникального индекса — без него DAG упадёт
CREATE UNIQUE INDEX IF NOT EXISTS bi_revenue_daily_pk ON bi.revenue_daily (order_date);

-- ---------------------------------------------------------------- демоданные
-- 60 000 заказов за последние 30 дней. Номера SKU и категории подобраны так,
-- чтобы распределение было парето-подобным (мало SKU дают много выручки).
INSERT INTO raw.orders (
    order_id, order_date, sku, category, region,
    amount, units, is_canceled, is_returned, updated_at
)
SELECT
    g                                                                  AS order_id,
    CURRENT_DATE - ((g % 30))::int                                     AS order_date,
    'SKU-' || lpad((((g * 7) % 120) + 1)::text, 3, '0')                AS sku,
    (ARRAY['Электроника', 'Одежда', 'Дом и сад', 'Красота', 'Спорт'])[(g % 5) + 1] AS category,
    (ARRAY['Москва', 'Санкт-Петербург', 'Регионы'])[(g % 3) + 1]       AS region,
    round((500 + ((g * 137) % 12000))::numeric * (1 + (g % 7) * 0.15), 2) AS amount,
    1 + (g % 3)                                                        AS units,
    (g % 41 = 0)                                                       AS is_canceled,
    (g % 27 = 0)                                                       AS is_returned,
    now() - ((g % 30) || ' days')::interval                            AS updated_at
FROM generate_series(1, 60000) AS g
ON CONFLICT (order_id) DO NOTHING;

ANALYZE raw.orders;