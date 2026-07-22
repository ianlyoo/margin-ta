"""Central tunables for multi-horizon analysis, consensus, and S/R tiering."""

# ── Horizons ────────────────────────────────────────────
HORIZONS = {
    "mid": {"resample_rule": "W-FRI", "min_bars": 60},   # 주봉, 최소 60주(≈14개월)
    "long": {"resample_rule": "ME", "min_bars": 36},     # 월봉, 최소 36개월
}
STANCE_BULL_MIN = 30    # 내부 점수(-100~+100) 기준 bullish 경계
STANCE_BEAR_MAX = -30

# ── Entry Score 카테고리 캡 (기존 주석의 ±N을 실제 적용) ──
SCORE_CATEGORY_CAPS = {
    "momentum": 24,
    "trend": 26,
    "volume": 20,
    "structure": 20,
    "candlestick": 10,
}

# 카테고리별 투표 가능 지표 id — 컨센서스의 neutral 카운트 분모
CATEGORY_INDICATORS = {
    "momentum": ["rsi", "stoch", "willr"],
    "trend": ["macd", "adx", "psar", "aroon", "vortex"],
    "volume": ["mfi", "cmf", "force_index", "volume_spike"],
    "structure": ["support_distance", "bollinger", "donchian",
                  "liquidity_confluence", "fib_confluence", "avwap", "volume_profile"],
    "candlestick": ["patterns", "doji_weak_rsi"],
}

# ── Consensus ───────────────────────────────────────────
CONSENSUS_MIN_DIRECTIONAL = 3        # 방향성 투표 3개 미만이면 agreement=None
AGREEMENT_CONFIDENCE_THRESHOLD = 40  # 미만이면 진입 플랜 confidence 1단계 강등

# ── S/R tiers ───────────────────────────────────────────
SR_TIERS = {
    "near":         {"cluster_pct": 0.010, "max_dist_pct": 0.30},
    "intermediate": {"cluster_pct": 0.015, "max_dist_pct": 0.30},
    "major":        {"cluster_pct": 0.025, "max_dist_pct": None},  # 거리 무제한
}
NEAR_BAND_WIDTH_PCT = 0.10    # 현재가 ±10% 밴드
NEAR_BAND_MAX_PER_SIDE = 6    # 밴드 내 near 티어 지지/저항 각 상한
CROSS_TIER_DEDUPE_PCT = 0.01  # 티어 간 같은 가격대 판정 폭 — 상위 티어만 노출
WEEKLY_PIVOT_WINDOW = 3

# ══ Risk layer (spec #2) ════════════════════════════════
# 전 지표 yfinance 티커 — 종합 스코어는 이것만으로 완전 동작
RISK_TICKERS = {
    "vix": "^VIX", "vxn": "^VXN", "vix9d": "^VIX9D", "vix3m": "^VIX3M",
    "vvix": "^VVIX", "wilshire5000": "^W5000", "sox": "^SOX",
    "hyg": "HYG", "lqd": "LQD", "tnx": "^TNX", "irx": "^IRX",
    "gld": "GLD", "spy": "SPY", "dxy": "DX-Y.NYB",
    "sp500": "^GSPC", "nasdaq": "^IXIC", "kospi": "^KS11",
}

# signal 임계 (warn, alert). 방향은 지표별 로직에서 해석.
RISK_SIGNAL_RULES = {
    "vix_level": {"warn": 20.0, "alert": 30.0},          # VIX 레벨 (표준 스트레스 밴드)
    "vxn_minus_vix": {"warn": 6.0, "alert": 10.0},       # 스프레드 절대값
    "vix_term_structure": {"warn": 0.95, "alert": 1.0},  # 단기/장기 비율(≥1=역전)
    "vvix": {"warn": 110.0, "alert": 130.0},
    "buffett": {"warn": 90.0, "alert": 97.0},            # percentile
    "hy_spread_change": {"warn": 0.03, "alert": 0.06},   # HYG/LQD 20일 변화율 (가격비율 기준)
    "index_cci_monthly": {"warn": 200.0, "alert": 200.0},# 월봉 CCI (+주봉 하향은 로직서)
    "ma200_gap": {"warn": 90.0, "alert": 97.0},          # 이격 percentile
    "monthly_rsi": {"warn": 80.0, "alert": 90.0},
    "drawdown": {"warn": -10.0, "alert": -20.0},         # ATH 대비 낙폭%
    "dxy_change": {"warn": 4.0, "alert": 8.0},           # 20일 변화%
    "breadth_divergence": {"warn": 50.0, "alert": 35.0},  # 지수 강세 + 참여 폭 협소
}

SECTOR_UNIVERSE = {
    "us": ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE"],
    "semi": ["SMH"],
    "kr": ["^KS11", "^KQ11", "091160.KS"],  # KOSPI/KOSDAQ/KODEX반도체 (pykrx 업종지수는 KRX 로그인 요구 → ETF 프록시)
}

RISK_GROUP_WEIGHTS = {
    "volatility": 0.35, "overheating": 0.25, "credit_rates": 0.20,
    "breadth": 0.10, "safe_haven": 0.10,
}
SECTOR_COMPONENT_WEIGHTS = {
    "overheating": 0.30, "momentum_rollover": 0.25, "drawdown_speed": 0.20,
    "volatility_rise": 0.15, "volume_anomaly": 0.10,
}
SIGNAL_SCORE_MAP = {"alert": 100, "warn": 60, "ok": 20}

FRED_SERIES = {"buffett_gdp": "GDP", "yield_10y2y": "T10Y2Y", "hy_oas": "BAMLH0A0HYM2"}
# FRED egress 차단 환경에서 버핏지표를 살리려면 최신 GDP(십억$)를 여기에 수동 입력
BUFFETT_GDP_OVERRIDE: float | None = None
