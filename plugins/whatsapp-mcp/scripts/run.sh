#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
  echo 'whatsapp-mcp suporta apenas macOS.' >&2
  exit 1
fi

plugin_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
uv_bin=$(command -v uv 2>/dev/null || true)
case "$uv_bin" in /*) ;; *) uv_bin= ;; esac
if [ -n "$uv_bin" ] && [ -f "$uv_bin" ] && [ -x "$uv_bin" ]; then
  :
elif [ -f /opt/homebrew/bin/uv ] && [ -x /opt/homebrew/bin/uv ]; then
  uv_bin=/opt/homebrew/bin/uv
elif [ -f /usr/local/bin/uv ] && [ -x /usr/local/bin/uv ]; then
  uv_bin=/usr/local/bin/uv
else
  uv_bin=$(/bin/sh "$plugin_dir/scripts/bootstrap-uv.sh")
fi

# Keep runtime dependencies outside the distributable plugin and WhatsApp data.
lock_hash=$(/usr/bin/shasum -a 256 "$plugin_dir/uv.lock" | /usr/bin/cut -c 1-16)
export UV_PROJECT_ENVIRONMENT="${WHATSAPP_MCP_VENV:-${XDG_CACHE_HOME:-$HOME/.cache}/whatsapp-mcp/venvs/$lock_hash}"
export PYTHONDONTWRITEBYTECODE=1
exec "$uv_bin" run --quiet --locked --no-dev --no-env-file --directory "$plugin_dir" python -B scripts/server.py "$@"
