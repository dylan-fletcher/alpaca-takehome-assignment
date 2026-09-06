{#-
    Multiply a column across a group -- the aggregate Postgres does not have.

    Reinvesting makes a run of trades compound: each trade scales the stake by
    `exit / entry`, so the return over a span is every multiplier multiplied
    together, not added. SQL aggregates sums but has no `product()`, so this
    uses the schoolbook log identity

        a * b * c = exp(ln(a) + ln(b) + ln(c))

    to reach a product through the `sum` that does exist. Nothing statistical
    is happening here; it is a workaround for a missing aggregate function.

    `ln` is undefined at or below zero. That is guaranteed upstream by the
    `int_btcusdt_hourly_prices_positive` test, and asserted again on
    `gross_return` in `_facts.yml` -- a zero price would otherwise surface as
    a null total return rather than a failure.

    Verified against a known product in `tests/assert_product_macro_is_exact`.
-#}

{% macro product(column) -%}
    exp(sum(ln({{ column }})))
{%- endmacro %}


{#-
    The same identity as a window, giving the running product up to each row.

    `product()` answers "what did this hour return overall"; this answers "what
    was the stake worth after each trade along the way". That curve is what a
    drawdown is measured against, and unlike every other number in the summary
    it depends on the order the trades happened in.

    Verified alongside `product` in `tests/assert_product_macro_is_exact`.
-#}

{% macro running_product(column, partition_by, order_by) -%}
    exp(sum(ln({{ column }})) over (
        partition by {{ partition_by }} order by {{ order_by }}
    ))
{%- endmacro %}
