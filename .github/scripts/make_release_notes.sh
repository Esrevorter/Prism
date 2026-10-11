#!/usr/bin/env bash
# Build a detailed RELEASE_NOTES.md for the GitHub Release.
#
# Replaces the auto-generated "What's Changed" blurb (which only shows opaque
# PR titles like "Update from task <uuid>") with real commit subjects grouped
# by conventional-commit type, plus each merged PR title/number/body excerpt.
#
# Inputs (env):
#   TAG  - the tag being released (e.g. v0.1.3). Defaults to newest tag at HEAD.
# Requires: git, curl + python3 (for PR metadata; skipped gracefully if GH_TOKEN absent).

set -uo pipefail

TAG="${TAG:-$(git describe --tags --abbrev=0 2>/dev/null || true)}"

# Previous tag = most recent tag before this one (by creation date).
PREV="$(git for-each-ref --sort=creatordate --format '%(refname:short)' refs/tags \
        | grep -v "^${TAG}$" | tail -n1 || true)"

if [ -n "$PREV" ] && git rev-parse -q --verify "${PREV}^{commit}" >/dev/null; then
  RANGE="${PREV}..HEAD"
else
  RANGE="HEAD"   # first release: include all history
fi

# --- classify commits -------------------------------------------------------
feat=(); fix=(); perf=(); refactor=(); docs=(); test=(); ci=(); build=(); other=()
declare -A PR_OF_COMMIT

while I=$'	' read -r sha subject; do
  [ -z "${sha:-}" ] && continue
  entry="- ${subject} (${sha})"
  pr_num=""
  if [[ "$subject" =~ Merge\ pull\ request\ \#([0-9]+) ]]; then
    pr_num="${BASH_REMATCH[1]}"
  elif [[ "$subject" == *"(#"*")" ]]; then
    pr_num="$(echo "$subject" | grep -oE '#[0-9]+' | tail -n1 | tr -d '#')"
  fi
  [ -n "$pr_num" ] && PR_OF_COMMIT["$pr_num"]="$sha"

  case "$subject" in
    feat*)       feat+=("$entry") ;;
    fix*)        fix+=("$entry") ;;
    perf*)       perf+=("$entry") ;;
    refactor*)   refactor+=("$entry") ;;
    docs*)       docs+=("$entry") ;;
    test*)       test+=("$entry") ;;
    'ci:'*|'chore(ci)'*) ci+=("$entry") ;;
    build*)      build+=("$entry") ;;
    *)           other+=("$entry") ;;
  esac
done < <(git log --no-merges --reverse --format='%h%x09%s' "$RANGE" 2>/dev/null || true)

# Merge commits carry PR numbers too — collect them for the PR-details section.
while I=$'	' read -r sha subject; do
  [ -z "${sha:-}" ] && continue
  if [[ "$subject" =~ Merge\ pull\ request\ \#([0-9]+) ]]; then
    PR_OF_COMMIT["${BASH_REMATCH[1]}"]="$sha"
  fi
done < <(git log --merges --reverse --format='%h%x09%s' "$RANGE" 2>/dev/null || true)

# --- write notes -------------------------------------------------------------
{
  echo "# Prism ${TAG:-unreleased}"
  echo
  echo "_$(git show -s --format=%cI HEAD)_ — pre-release binaries for Windows / macOS / Linux."
  echo

  emit_section() {
    local title="$1"; shift
    [ "$#" -gt 0 ] && [ -n "${1:-}" ] || return 0
    echo "## $title"
    printf '%s\n' "$@"
    echo
  }
  emit_section "🚀 Features"          "${feat[@]:-}"
  emit_section "🐛 Bug Fixes"         "${fix[@]:-}"
  emit_section "⚡ Performance"        "${perf[@]:-}"
  emit_section "♻️ Refactoring"        "${refactor[@]:-}"
  emit_section "📦 Build & Packaging" "${build[@]:-}"
  emit_section "👷 CI"                "${ci[@]:-}"
  emit_section "📝 Documentation"     "${docs[@]:-}"
  emit_section "✅ Tests"             "${test[@]:-}"
  emit_section "🔧 Other changes"     "${other[@]:-}"

  # Pull-request details (title + body excerpt) when API access is available.
  if [ "${#PR_OF_COMMIT[@]}" -gt 0 ] && [ -n "${GH_TOKEN:-}" ]; then
    echo "## Pull requests"
    echo
    for pr in $(printf '%s\n' "${!PR_OF_COMMIT[@]}" | sort -n); do
      meta="$(curl -sf -H "Authorization: Bearer $GH_TOKEN" \
              "https://api.github.com/repos/${GITHUB_REPOSITORY}/pulls/${pr}" 2>/dev/null || true)"
      [ -z "$meta" ] && continue
      ptitle="$(echo "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("title",""))' 2>/dev/null || true)"
      pbody="$(echo "$meta" | python3 -c 'import json,sys; b=(json.load(sys.stdin).get("body") or "").strip(); print(b[:500])' 2>/dev/null || true)"
      echo "- [#${pr}](https://github.com/${GITHUB_REPOSITORY}/pull/${pr}) **${ptitle}**"
      if [ -n "$pbody" ]; then
        echo "$pbody" | sed 's/^/> /'
      fi
      echo
    done
  fi

  total=$(( ${#feat[@]} + ${#fix[@]} + ${#perf[@]} + ${#refactor[@]} + ${#docs[@]} + ${#test[@]} + ${#ci[@]} + ${#build[@]} + ${#other[@]} ))
  if [ "$total" -eq 0 ]; then
    echo "_No classified changes since $( [ -n "$PREV" ] && echo "$PREV" || echo "initial history")._"
    echo
  fi

  echo "## Assets"
  echo
  echo "| File | Contents |"
  echo "|------|----------|"
  echo "| \`prism-windows-*.zip\` | \`prism.exe\` CLI node + \`prism-gui.exe\` desktop wallet (portable, no install) |"
  echo "| \`prism-macos-*.zip\` | macOS CLI + GUI binaries (Apple Silicon and Intel) |"
  echo "| \`prism-linux-*.zip\` | Linux CLI + GUI binaries |"
  echo "| \`.whl\` / \`.tar.gz\` | pip-installable package (\`pip install prism-*.whl\`) |"
  echo
  echo "Desktop installers (\`.msi\`, \`.dmg\`, \`.AppImage\`, \`.deb\`) are attached by the Desktop workflow for the same tag."
} > RELEASE_NOTES.md

cat RELEASE_NOTES.md
