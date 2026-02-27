with customers as (

    select * from {{ ref('stg_customers') }}

),

orders as (

    select
        customer_id,
        count(*)      as number_of_orders,
        sum(amount)   as total_amount

    from {{ ref('stg_orders') }}

    group by 1

)

select
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    orders.number_of_orders,
    orders.total_amount

from customers
left join orders on customers.customer_id = orders.customer_id
