# {{kit_package}}

{{title}}'s kit: how its apps read the department's data. One folder per source in `src/{{kit}}/sources/`, pure
functions in `src/{{kit}}/helpers/`, and the department's conventions in `src/{{kit}}/NOTES.md`, which every app's
`sbu-{{sbu}}` skill carries. Add a source with `ctzd kit add source NAME --kind KIND`, and prove the kit with
`ctzd kit check kits/{{sbu}}`.
