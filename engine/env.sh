# Sourced at the top of every tad-clip skill snippet (the Bash tool forgets variables between calls):
#   K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"
# Sets KIT (kit root, also the working directory afterwards), TC_HOME, PY (kit venv python), FF (ffmpeg with libass), NODE.
# Works in macOS bash/zsh and in Git Bash on Windows. Because of the cd, pass user files as absolute paths.
# Git Bash: CLAUDE_PLUGIN_ROOT / TAD_CLIP_HOME may be C:\… paths and python prints CRLF lines with backslash paths,
# so on Windows (OSTYPE msys/cygwin) backslashes become slashes and every \r is dropped before the values are used.
# PYTHONUTF8=1 (this one python call only) makes python print tool paths under a non-ASCII Windows home as UTF-8, not ???.
_tc_src="${BASH_SOURCE[0]:-$0}"
TC_HOME="${TAD_CLIP_HOME:-$HOME/.tad-clip}"
case "$OSTYPE" in msys* | cygwin* | win32*) _tc_src="${_tc_src//\\//}"; TC_HOME="${TC_HOME//\\//}" ;; esac
KIT="$(cd "$(dirname "$_tc_src")/.." && pwd)"
unset _tc_src
PY="$TC_HOME/venv/bin/python"
[ -x "$PY" ] || PY="$TC_HOME/venv/Scripts/python.exe"
cd "$KIT" || return 1
if [ -x "$PY" ]; then
  _tc_tools="$(PYTHONUTF8=1 "$PY" -c "from engine import kit
for n in ('ffmpeg', 'node'):
    try: print(kit.tool(n))
    except kit.KitError: print('')" 2>/dev/null | tr -d '\r')"
  case "$OSTYPE" in msys* | cygwin* | win32*) _tc_tools="${_tc_tools//\\//}" ;; esac
  FF="$(printf '%s\n' "$_tc_tools" | sed -n 1p)"
  NODE="$(printf '%s\n' "$_tc_tools" | sed -n 2p)"
  unset _tc_tools
else
  echo "tad-clip ยังไม่ได้ติดตั้ง — ใช้ skill tad-clip-setup ก่อน" >&2
fi
: "${FF:=ffmpeg}" "${NODE:=node}"
