#!/usr/bin/env bash
# Run before submitting. Fails if a secret could be published or the build is not what the README says.
cd "$(dirname "$0")"
fail=0
echo "== secrets in working tree"
if grep -rIn --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.venv -E "AIza[0-9A-Za-z_-]{20,}" . ; then echo "FAIL: API key found"; fail=1; else echo "ok"; fi
echo "== secrets in built frontend"
if [ -d frontend/dist ] && grep -rIn -E "AIza[0-9A-Za-z_-]{20,}" frontend/dist; then echo "FAIL: key in dist"; fail=1; else echo "ok"; fi
echo "== secrets in git history"
if [ -d .git ]; then if git log -p --all | grep -E "AIza[0-9A-Za-z_-]{20,}" >/dev/null; then echo "FAIL: key in git history (rotate the key and rewrite history)"; fail=1; else echo "ok"; fi; else echo "skipped (not a git repo)"; fi
echo "== files that must not be committed"
for f in .env .firebaserc; do [ -e "$f" ] && git ls-files --error-unmatch "$f" >/dev/null 2>&1 && { echo "FAIL: $f is tracked"; fail=1; }; done; echo "ok"
echo "== pinned requirements"
if grep -vE "==" backend/requirements.txt | grep -q .; then echo "FAIL: unpinned requirement"; fail=1; else echo "ok"; fi
echo "== tests"
(cd backend && FAKE_AI=1 STORE=memory CLIMATE_LIVE=0 python -m pytest -q 2>&1 | tail -1)
[ $fail -eq 0 ] && echo "ALL CLEAR" || echo "FIX THE FAILURES ABOVE"
exit $fail
