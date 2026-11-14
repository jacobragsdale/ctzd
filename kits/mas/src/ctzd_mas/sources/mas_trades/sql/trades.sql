-- The trades booked from start up to but not including end. Ids are text.
SELECT trade_id, portfolio_id, instrument_id, side, quantity, price, booked_at
FROM dbo.trades
WHERE booked_at >= %(start)s AND booked_at < %(end)s
