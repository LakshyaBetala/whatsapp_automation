#!/usr/bin/env bash
# Background bring-up of an isolated LOCAL Supabase dev stack (Docker).
set -uo pipefail
cd "$(dirname "$0")"
echo "[bringup] docker daemon check..."
if ! docker ps >/dev/null 2>&1; then
  echo "[bringup] ERROR: Docker daemon not running. Start Docker Desktop, then re-run: bash dev/_bringup.sh" ; exit 2
fi
echo "[bringup] docker OK. init supabase (first run downloads CLI ~30-60s)..."
if [ ! -f supabase/config.toml ]; then
  printf 'n\n' | npx --yes supabase init >/dev/null 2>&1 || { echo "[bringup] supabase init failed"; exit 3; }
fi
echo "[bringup] supabase start (first run pulls ~8 docker images, can take several min)..."
npx --yes supabase start
echo "[bringup] DONE. Status:"
npx --yes supabase status
