# -*- coding: utf-8 -*-
"""手动触发的最小真实数据冒烟（P2-35）。

用法：python scripts/smoke_real.py --code 110022 --out output/results/smoke
"""
from market_research.ops.smoke import main

if __name__ == "__main__":
    raise SystemExit(main())
