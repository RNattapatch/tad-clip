# Sourced at the top of every tad-clip skill snippet (the Bash tool forgets variables between calls):
#   K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
# Sets KIT (kit root, also the working directory afterwards), TC_HOME, PY (kit venv python), FF (ffmpeg with libass), NODE.
# Works in macOS bash/zsh and in Git Bash on Windows. Because of the cd, pass user files as absolute paths.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
TC_HOME="${TAD_CLIP_HOME:-$HOME/.tad-clip}"
PY="$TC_HOME/venv/bin/python"
[ -x "$PY" ] || PY="$TC_HOME/venv/Scripts/python.exe"
cd "$KIT" || return 1
if [ -x "$PY" ]; then
  _tc_tools="$("$PY" -c "from engine import kit
for n in ('ffmpeg', 'node'):
    try: print(kit.tool(n))
    except kit.KitError: print('')" 2>/dev/null)"
  FF="$(printf '%s\n' "$_tc_tools" | sed -n 1p)"
  NODE="$(printf '%s\n' "$_tc_tools" | sed -n 2p)"
  unset _tc_tools
else
  echo "tad-clip ยังไม่ได้ติดตั้ง — ใช้ skill tad-clip-setup ก่อน" >&2
fi
: "${FF:=ffmpeg}" "${NODE:=node}"
