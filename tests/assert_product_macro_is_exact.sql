-- The `product` macros reach a product through exp/sum/ln, which is the one
-- piece of arithmetic here that cannot be checked by reading it. Pin both to a
-- product anyone can verify in their head: 2 * 3 * 4 = 24, reached one step at
-- a time as 2, 6, 24.
--
-- The tolerance covers exp/ln round-tripping, not a real margin of error --
-- Postgres computes both in `numeric`, so the result lands far inside it.

with known_values as (

    select
        grp,
        seq,
        n
    from (
        values
        (1, 1, 2.0),
        (1, 2, 3.0),
        (1, 3, 4.0)
    ) as v (grp, seq, n)

),

expected_running as (

    -- 2, then 2 * 3, then 2 * 3 * 4.
    select
        seq,
        expected
    from (
        values
        (1, 2.0),
        (2, 6.0),
        (3, 24.0)
    ) as v (seq, expected)

),

aggregate_form as (

    select {{ product('n') }} as result
    from known_values

),

running_form as (

    select
        seq,
        {{ running_product('n', 'grp', 'seq') }} as result
    from known_values

),

checked as (

    -- `product` collapses the group to one number; `running_product` leaves
    -- one number per row. Both are held to the same worked example.
    select
        'product'             as macro,
        'total'               as step,
        aggregate_form.result as actual,
        24.0                  as expected
    from aggregate_form

    union all

    select
        'running_product'           as macro,
        'step ' || running_form.seq as step,
        running_form.result         as actual,
        expected_running.expected
    from running_form
    inner join expected_running
        on running_form.seq = expected_running.seq

)

select
    macro,
    step,
    actual,
    expected,
    abs(actual - expected) as error
from checked
where abs(actual - expected) > 0.000000001
