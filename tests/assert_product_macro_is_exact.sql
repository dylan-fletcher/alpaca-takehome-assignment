-- The `product` macro reaches a product through exp/sum/ln, which is the one
-- piece of arithmetic here that cannot be checked by reading it. Pin it to a
-- product anyone can verify in their head: 2 * 3 * 4 = 24.
--
-- The tolerance covers exp/ln round-tripping, not a real margin of error --
-- Postgres computes both in `numeric`, so the result lands far inside it.

with known_values as (

    select 2.0 as n
    union all
    select 3.0
    union all
    select 4.0

),

computed as (

    select {{ product('n') }} as result
    from known_values

)

select
    result,
    abs(result - 24.0) as error
from computed
where abs(result - 24.0) > 0.000000001
