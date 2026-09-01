#!/bin/bash
# Local helper. Usage:  ./run.sh <command>
set -e
cd "$(dirname "$0")"
[ -f keys.sh ] && source keys.sh
[ -d venv ] && source venv/bin/activate

case "$1" in
  sync)    python visions_sync.py sync --attribute-name "sizes" --skip-sold-out ;;
  test)    python visions_sync.py test --skip-sold-out ;;
  check)   python visions_sync.py sync --attribute-name "sizes" --skip-sold-out --dry-run ;;
  review)  rm -rf review; python brand_clean.py review; open review/index.html 2>/dev/null || true ;;
  clean)   shift; python brand_clean.py apply "$@" ;;
  all)     python visions_sync.py sync --attribute-name "sizes" --skip-sold-out
           rm -rf review; python brand_clean.py review
           open review/index.html 2>/dev/null || true
           echo; echo "Review the page, then:  ./run.sh clean" ;;
  *)
    echo "Usage: ./run.sh <command>"
    echo
    echo "  test     check both ends, change nothing"
    echo "  check    show what sync would change"
    echo "  sync     push price and stock changes"
    echo "  review   find branding, build the review page"
    echo "  clean    upload the cleaned images  (./run.sh clean --skip 4,7)"
    echo "  all      sync, then build the review page"
    exit 1 ;;
esac
