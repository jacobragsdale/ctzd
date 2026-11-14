-- The Treasury curve at a close. On the sample source this is answered by fixtures/sample/curve.csv.
SELECT tenor, years, ytw
FROM curve_points
WHERE as_of = %(as_of)s
ORDER BY years
