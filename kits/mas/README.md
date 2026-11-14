# ctzd-mas

Multi-Asset Solutions's kit: how its apps read the department's data. One folder per source in `src/ctzd_mas/sources/`, pure
functions in `src/ctzd_mas/helpers/`, and the department's conventions in `src/ctzd_mas/NOTES.md`, which every app's
`sbu-mas` skill carries. Add a source with `ctzd kit add source NAME --kind KIND`, and prove the kit with
`ctzd kit check kits/mas`.
