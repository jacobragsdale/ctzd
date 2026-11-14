# ctzd-fi

Fixed Income's kit: how its apps read the department's data. One folder per source in `src/ctzd_fi/sources/`, pure
functions in `src/ctzd_fi/helpers/`, and the department's conventions in `src/ctzd_fi/NOTES.md`, which every app's
`sbu-fi` skill carries. Add a source with `ctzd kit add source NAME --kind KIND`, and prove the kit with
`ctzd kit check kits/fi`.
