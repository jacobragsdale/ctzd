SELECT CAST(trade_id AS VARCHAR(40)) AS trade_id, trade_date, isin, side, notional, desk
FROM dbo.trades
ORDER BY trade_date DESC, trade_id
