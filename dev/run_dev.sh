#!/usr/bin/env bash
# ============================================================
#  ASVA DEV runner - full app on THIS laptop, no Tally, isolated local DB.
#
#    bash dev/run_dev.sh up      # start local Supabase (if down) + backend (reload)
#    bash dev/run_dev.sh seed    # load fake-Tally sample data (backend must be up)
#    bash dev/run_dev.sh serve   # just the backend against the dev DB
#    bash dev/run_dev.sh db-up   # start the local Supabase stack
#    bash dev/run_dev.sh db-down # stop the local Supabase stack (data is kept)
#    bash dev/run_dev.sh reset   # wipe + re-apply all migrations to the dev DB
#
#  Uses dev/.env.dev on TOP of the real .env (env vars win), so it can never
#  touch the 4 live shops. Prod .env is left untouched.
# ============================================================
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"
PY=".venv/Scripts/python"; [ -f "$PY" ] || PY=".venv/bin/python"; [ -f "$PY" ] || PY="python"
DBC="supabase_db_dev"

load_env() { set -a; # shellcheck disable=SC1091
  source dev/.env.dev; set +a; }

db_up()  { ( cd dev && npx --yes supabase start ); }
db_down(){ ( cd dev && npx --yes supabase stop ); }

apply_migrations() {
  echo "==> applying migrations to $DBC ..."
  for f in $(ls migrations/*.sql | sort); do
    docker exec -i "$DBC" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q < "$f" \
      || echo "   (warn) $(basename "$f") had an issue"
  done
  # Supabase cloud auto-grants service_role; local does not after the RLS
  # lockdown, so re-grant here or the backend gets "permission denied".
  docker exec -i "$DBC" psql -U postgres -d postgres -q < dev/_dev_grants.sql 2>/dev/null || true
  echo "==> migrations + dev grants done."
}

serve() {
  load_env
  echo "==> DEV backend on http://localhost:8000  (DB: $SUPABASE_URL)"
  echo "    Owner view : http://localhost:8000/admin?token=$TALLY_AGENT_TOKEN"
  echo "    Command Ctr: http://localhost:8000/ops?key=$ADMIN_API_KEY"
  echo "    Studio (DB): http://127.0.0.1:54323"
  "$PY" -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
}

case "${1:-up}" in
  db-up)   db_up ;;
  db-down) db_down ;;
  reset)   ( cd dev && npx --yes supabase db reset --no-seed 2>/dev/null ) ; apply_migrations ;;
  seed)    load_env; "$PY" dev/seed.py ;;
  serve)   serve ;;
  up)      docker exec "$DBC" true 2>/dev/null || db_up; serve ;;
  *) echo "usage: bash dev/run_dev.sh [up|seed|serve|db-up|db-down|reset]"; exit 1 ;;
esac
