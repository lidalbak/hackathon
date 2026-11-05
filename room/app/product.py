"""Demo product's event contract, indexed by the code agent.

A customer is new when customers.created_at is within the requested window.
Product usage means at least one usage_events row with event_name='product_used'.
Count distinct customer_id, not event rows. Exclude internal accounts.
"""
PRODUCT_USED = 'product_used'
DEFAULT_WINDOW_DAYS = 30
