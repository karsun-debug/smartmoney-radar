#!/usr/bin/env bash
# Keep the SQLite database on an orphan "data" branch (one commit, force-pushed),
# so history survives even if the Actions cache is evicted.
#   scripts/data_branch.sh restore   # data branch -> data/spiderhead.db (no-op if missing)
#   scripts/data_branch.sh save      # data/spiderhead.db -> data branch
# Needs GITHUB_TOKEN with contents:write and GITHUB_REPOSITORY (both set in Actions).
set -euo pipefail
DB="${SPIDERHEAD_DB:-data/spiderhead.db}"
BRANCH="${DATA_BRANCH:-data}"
REMOTE="${DATA_REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"

case "${1:-}" in
  restore)
    mkdir -p "$(dirname "$DB")"
    if git fetch --depth=1 "$REMOTE" "$BRANCH" 2>/dev/null; then
      git show FETCH_HEAD:spiderhead.db.gz | gunzip > "$DB.tmp" && mv "$DB.tmp" "$DB"
      echo "restored $DB from branch $BRANCH ($(du -h "$DB" | cut -f1))"
    else
      echo "no $BRANCH branch yet; starting from cache or an empty database"
    fi
    ;;
  save)
    [ -f "$DB" ] || { echo "no database at $DB"; exit 0; }
    python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('VACUUM'); c.close()" "$DB"
    tmp="$(mktemp -d)"
    gzip -9 -c "$DB" > "$tmp/spiderhead.db.gz"
    size=$(stat -c %s "$tmp/spiderhead.db.gz")
    if [ "$size" -gt 90000000 ]; then
      echo "::warning::database is $size bytes gzipped; GitHub rejects files over 100MB"
    fi
    cat > "$tmp/README.md" <<'MD'
Automated data snapshot for Smartmoney Radar (public posts, filings and prices).
Restore with `scripts/data_branch.sh restore`. Rewritten on every run.
MD
    (
      cd "$tmp"
      git init -q
      git checkout -q -b "$BRANCH"
      git add spiderhead.db.gz README.md
      git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
        commit -q -m "data snapshot $(date -u +%Y-%m-%dT%H:%MZ)"
      git push -q -f "$REMOTE" "$BRANCH"
    )
    echo "saved $DB to branch $BRANCH ($size bytes gzipped)"
    rm -rf "$tmp"
    ;;
  *)
    echo "usage: $0 restore|save" >&2
    exit 2
    ;;
esac
