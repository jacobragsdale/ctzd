# Commit $SPEC (the airflow-spec.json `ctzd ci render` wrote) to the ctzd-dags repo as specs/$SBU/$APP.json,
# retrying if another build pushed first. Proposal for the infrastructure owner.
# Uses $SPEC, $DAGS_REPO, $SBU, $APP, $CTZD_GITHUB_TOKEN.
host=$(printf '%s' "$DAGS_REPO" | sed -E 's#https://([^/]+)/.*#\1#')
auth="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$CTZD_GITHUB_TOKEN" | base64 -w0)"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
dags="$work/dags"
target="specs/$SBU/$APP.json"
git -c "http.https://$host/.extraheader=$auth" clone --quiet --depth 1 "$DAGS_REPO" "$dags"
mkdir -p "$dags/specs/$SBU"
for attempt in 1 2 3 4 5; do
    cp "$SPEC" "$dags/$target"
    git -C "$dags" add "$target"
    if git -C "$dags" diff --cached --quiet; then exit 0; fi
    git -C "$dags" -c user.name=ctzd -c user.email=ctzd@localhost commit --quiet -m "$SBU/$APP: $(jq -r '.image' "$SPEC")"
    if git -C "$dags" -c "http.https://$host/.extraheader=$auth" push --quiet origin HEAD:main; then exit 0; fi
    git -C "$dags" -c "http.https://$host/.extraheader=$auth" pull --quiet --rebase origin main
    sleep "$attempt"
done
echo "Could not publish the job spec after 5 attempts" >&2
exit 1
