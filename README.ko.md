# margin-ta

Read-only technical-analysis pipeline — generate multi-horizon stances with indicator consensus, tiered S/R and market/sector risk dashboard.

[English](README.md)

[![CI](https://github.com/ianlyoo/margin-ta/actions/workflows/ci.yml/badge.svg)](https://github.com/ianlyoo/margin-ta/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Release: v3.0.0](https://img.shields.io/github/v/release/ianlyoo/margin-ta?label=v3.0.0)](https://github.com/ianlyoo/margin-ta/releases/tag/v3.0.0)
[![Pages](https://img.shields.io/badge/Pages-GitHub_Pages-2ea44f)](https://ianlyoo.github.io/margin-ta/)

한국과 미국 주식 및 암호화폐 페어를 위한 읽기 전용 기술적 분석 파이프라인. 30개 이상의 벡터화 지표, 일/주/월 다중 호라이즌 스탠스, indicator-consensus 계층, tiered support-resistance 탐지, market-dashboard와 섹터 리스크를 단일 CLI로 제공 — MCP 서버의 분석 엔진으로도 동작합니다. 주문을 실행하지 않으며 모든 출력은 의사결정 지원 데이터입니다.

- Python 3.10+, yfinance + pykrx 공개 데이터 기본
- 30개 이상 지표 (SMA/EMA, MACD, RSI, Stochastic, ADX, Bollinger, ATR, Ichimoku 등) + TA-Lib 패턴
- MCP 서버 지원, JSON + 차트 PNG + TradingView 딥링크

## 빠른 시작 — Python과 technical-analysis로 multi-horizon 분석

Python 파이프라인으로 multi-horizon 스탠스와 technical-analysis 지표를 실행합니다. stock-analysis 신호와 quant 점수를 포함합니다.

### GitHub Release tarball로 설치 (PyPI 레지스트리 사용 안 함)

```bash
gh release download v3.0.0 --repo ianlyoo/margin-ta --pattern "margin-ta-*.tar.gz"
pip install ./margin-ta-3.0.0.tar.gz
```

`gh`가 없을 때 — 로컬에서 tarball 빌드:

```bash
python -m build --sdist
pip install ./dist/margin-ta-3.0.0.tar.gz
```

### 클론, 설치, 실행

```bash
git clone https://github.com/ianlyoo/margin-ta.git
cd margin-ta
pip install -r requirements.txt
python scripts/margin_ta.py AAPL --json --quiet --no-tv --no-market
python scripts/market_risk.py --sectors
```

## 사용 사례 — market-dashboard와 trading, risk-management

indicator consensus와 tiered S/R이 포지션 계획을 돕고, market-dashboard와 risk-management 컨텍스트가 노출을 조절하는 트레이딩 리서치용입니다.

## 아키텍처: indicator-consensus와 support-resistance 파이프라인

indicator-consensus, support-resistance, multi-horizon, quant, trading, developer-tools 흐름은 영문 README와 동일합니다. 자세한 내용은 영문 문서를 참조하세요.

## 벤치마크: 측정된 실행에서의 Entry Score와 indicator-consensus

> 검증된 증거만 제시합니다. 수익이나 체결 주장이 아닙니다.

**설정 (인접한 제한사항):** 30개 종목 합성 워치리스트, 점수 스냅샷당 1회 실행, 데이터 기준 2026-08-25, 오프라인 결정성을 위한 `--no-tv`/`--no-market`, 로컬 캐시만 사용, 실제 브로커리지 없음, 미래 수익률 검증 없음. 점수는 휴리스틱이며 임계값은 튜닝에 따라 변경될 수 있습니다.

Limitations restated: 오프라인 스냅샷, 1회 실행, 브로커리지 체결 없음, 수익 예측 없음, 공개 데이터 지연 가능, 휴리스틱 임계값, 투자 조언 아님.

## 프로젝트 링크

- Repository: https://github.com/ianlyoo/margin-ta
- Issues: https://github.com/ianlyoo/margin-ta/issues
- Pages: https://ianlyoo.github.io/margin-ta/

## 라이선스

MIT — [LICENSE](LICENSE)를 참조하세요.
