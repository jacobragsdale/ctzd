-- The rows example() returns. Bind by name, as %(as_of)s. Select ids as text and every number as it is: apps round
-- nothing.
SELECT item_id, as_of, name, value
FROM dbo.example_items
WHERE as_of = %(as_of)s
