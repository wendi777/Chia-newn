#!/bin/bash

set -vx

git mv tests/ chia/_tests/
find chia/_tests/ benchmarks/ tools/ -name '*.py' -exec sed -i -E 's/(from|import) tests/\1 chia._tests/' {} \;
python tools/manage_clvm.py build
