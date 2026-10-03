#!/usr/bin/env bash
# Run the salient hook with the first working Python 3 on PATH.
# The probe matters on Windows, where `python3` can be a Store stub that exits silently.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for py in python3 python "py -3"; do
  # shellcheck disable=SC2086
  if [ "$($py -c 'import sys; print(sys.version_info[0])' 2>/dev/null)" = "3" ]; then
    # shellcheck disable=SC2086
    exec $py "$here/salient_hook.py"
  fi
done
echo "salient: no Python 3 interpreter found (tried python3, python, py -3)" >&2
exit 0
