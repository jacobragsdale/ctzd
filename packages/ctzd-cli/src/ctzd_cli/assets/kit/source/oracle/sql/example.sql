-- The rows example() returns. Bind by name, as :as_of. Select ids as text and every number as it is: apps round
-- nothing.
select item_id, as_of, name, value
from example_items
where as_of = :as_of
