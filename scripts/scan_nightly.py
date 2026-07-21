#!/usr/bin/env python3
"""
Nightly TA Scanner — NASDAQ 100 + S&P 500 technical screening.
Scans all tickers with margin-ta, ranks by Entry Score, outputs top picks.

v3.0 — OHLCV 캐싱 + 전체 결과 저장 + Google Drive 업로드

Usage:
    # 기본 (기존과 동일)
    python3 scan_nightly.py --markdown --top 5

    # OHLCV 캐싱 + 결과 저장
    python3 scan_nightly.py --cache-ohlcv --save-results data/nightly_results/ --markdown --top 5

    # Google Drive 업로드 포함
    python3 scan_nightly.py --cache-ohlcv --save-results data/nightly_results/ --gdrive-upload --markdown --top 5
"""

import subprocess, json, sys, os, time, argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
TICKERS_FILE = os.path.join(SKILL_DIR, "data", "nightly_tickers.json")
MARGIN_TA = os.path.join(SCRIPT_DIR, "margin_ta.py")
DOWNLOAD_OHLCV = os.path.join(SCRIPT_DIR, "download_ohlcv_batch.py")
VENV_PYTHON = os.path.join(SKILL_DIR, ".venv", "bin", "python3")
CACHE_BASE = os.path.join(SKILL_DIR, "data", "ohlcv_cache")


def load_tickers():
    """Load combined S&P 500 + NASDAQ 100 tickers from cached JSON."""
    if not os.path.exists(TICKERS_FILE):
        print(f"ERROR: Tickers file not found: {TICKERS_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(TICKERS_FILE) as f:
        data = json.load(f)
    tickers = data.get("combined", [])
    if not tickers:
        print("ERROR: No tickers in file", file=sys.stderr)
        sys.exit(1)
    return tickers


def pre_download_ohlcv(cache_date, delay=0.3):
    """Run download_ohlcv_batch.py to pre-fetch all OHLCV data."""
    print(f"📥 OHLCV 일괄 다운로드 시작... (date={cache_date})", file=sys.stderr)
    result = subprocess.run(
        [VENV_PYTHON, DOWNLOAD_OHLCV, "--date", cache_date, "--delay", str(delay)],
        capture_output=False,  # let stderr flow through for progress
        timeout=3600,  # 1 hour max
    )
    if result.returncode != 0:
        print(f"⚠️  OHLCV 다운로드 일부 실패 (exit={result.returncode}). 계속 진행...", file=sys.stderr)
    else:
        print("✅ OHLCV 다운로드 완료", file=sys.stderr)


def scan_ticker(symbol, ohlcv_cache_dir=None, timeout=15):
    """Run margin_ta.py on a single ticker, return parsed JSON or None."""
    cmd = [
        VENV_PYTHON,
        MARGIN_TA,
        symbol,
        "--quiet",
        "--json",
        "--no-tv",
        "--no-market",
        "--no-session-quote",
        "--no-options",
    ]
    if ohlcv_cache_dir:
        cmd.extend(["--ohlcv-cache-dir", ohlcv_cache_dir])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return None
    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def google_token_path() -> str:
    """Google OAuth token JSON path: env MARGIN_TA_GOOGLE_TOKEN or generic default."""
    return os.environ.get("MARGIN_TA_GOOGLE_TOKEN") or os.path.expanduser(
        "~/.config/margin-ta/google_token.json"
    )


def upload_to_gdrive(local_path, folder_name="margin-ta-scans"):
    """Upload a file to Google Drive. Returns webViewLink, or None when skipped.

    Optional feature: missing token file or missing Google API libraries
    print a warning and skip — the scan/analysis itself is never affected.
    """
    token_path = google_token_path()
    if not os.path.exists(token_path):
        print("⚠️  Google OAuth 미설정 — Drive 업로드 건너뜀", file=sys.stderr)
        print(
            f"   설정: {token_path} 경로에 OAuth 토큰 JSON을 배치하거나 "
            "MARGIN_TA_GOOGLE_TOKEN env를 설정하세요",
            file=sys.stderr,
        )
        return None

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        with open(token_path) as f:
            token_data = json.load(f)

        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", ["https://www.googleapis.com/auth/drive.file"]),
        )

        service = build("drive", "v3", credentials=creds)

        # 폴더 찾기 또는 생성
        folder_id = None
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = service.files().list(q=query, spaces="drive", fields="files(id,name)").execute()
        folders = response.get("files", [])
        if folders:
            folder_id = folders[0]["id"]
            print(f"  📁 Drive 폴더 발견: {folder_name} ({folder_id})", file=sys.stderr)
        else:
            folder_meta = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            folder = service.files().create(body=folder_meta, fields="id").execute()
            folder_id = folder["id"]
            print(f"  📁 Drive 폴더 생성: {folder_name} ({folder_id})", file=sys.stderr)

        # 파일 업로드
        file_name = os.path.basename(local_path)
        file_meta = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(local_path, mimetype="application/json", resumable=True)

        uploaded = service.files().create(body=file_meta, media_body=media, fields="id,name,webViewLink").execute()
        link = uploaded.get("webViewLink")
        print(f"  ☁️  Drive 업로드 완료: {link}", file=sys.stderr)
        return link

    except ImportError as e:
        print(f"⚠️  Google API 라이브러리 없음 — Drive 업로드 건너뜀 ({e})", file=sys.stderr)
        return None
    except Exception as e:
        print(f"⚠️  Drive 업로드 실패: {e}", file=sys.stderr)
        return None


def format_markdown(top_results, total_scanned, total_valid, gdrive_link=None):
    """Generate Discord-friendly markdown report in Korean."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "# 🌙 야간 기술적 분석 스캔",
        f"📅 {now}  |  스캔: {total_scanned}개  |  유효: {total_valid}개",
        "",
        "> S&P 500 + NASDAQ 100 종목 중 Entry Score 상위 종목",
        "",
    ]

    for i, r in enumerate(top_results, 1):
        sym = r["symbol"]
        score = r["score"]
        verdict = r["verdict"]
        price = r["price"]
        name = r.get("name", sym)

        if score >= 70:
            emoji, tier = "🔥", "STRONG"
        elif score >= 50:
            emoji, tier = "🟢", "MODERATE"
        elif score >= 30:
            emoji, tier = "🟡", "WEAK"
        else:
            emoji, tier = "🔴", "AVOID"

        lines.append(f"## {i}. {emoji} **{sym}** — {score}/100 ({tier})")
        lines.append(f"🏷 {name}  |  💰 ${price:.2f}")
        lines.append("")

        strategies = r.get("strategies", [])
        for s in strategies:
            if s.get("entry"):
                lines.append(
                    f"- **{s['type']}**: 진입 ${s['entry']:.2f} | "
                    f"손절 ${s['stop']:.2f} | 리스크 {s['risk_pct']}% | "
                    f"비중 {s.get('size_pct', 'N/A')}%"
                )
        if not any(s.get("entry") for s in strategies):
            lines.append("- ⏸ 진입 전략 없음 — 관망 권장")
        lines.append("")

    lines.append("---")
    if gdrive_link:
        lines.append(f"📊 [전체 결과 다운로드 (Google Drive)]({gdrive_link})")
        lines.append("")
    lines.append("⚙️ *margin-ta nightly scan · 매일 미국장 마감 후 자동 생성*")
    lines.append("⚠️ *투자 권유가 아닌 기술적 분석 보조 자료입니다*")

    return "\n".join(lines)


def format_json(top_results, total_scanned, total_valid):
    """Output JSON payload for programmatic consumption."""
    return json.dumps({
        "scanned": total_scanned,
        "valid": total_valid,
        "generated_at": datetime.now().isoformat(),
        "top": [
            {
                "rank": i + 1,
                "symbol": r["symbol"],
                "name": r.get("name", r["symbol"]),
                "score": r["score"],
                "verdict": r["verdict"],
                "price": r["price"],
                "strategies": r.get("strategies", []),
            }
            for i, r in enumerate(top_results)
        ],
    }, indent=2, ensure_ascii=False)


def save_full_results(all_results, path, total_scanned, total_valid, gdrive_link=None):
    """Save complete scan results to a JSON file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "scanned": total_scanned,
        "valid": total_valid,
        "generated_at": datetime.now().isoformat(),
        "gdrive_link": gdrive_link,
        "results": sorted(all_results, key=lambda x: x["score"], reverse=True),
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"💾 전체 결과 저장: {path} ({len(all_results)}종목)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Nightly TA Scanner v3.0")
    parser.add_argument("--top", type=int, default=5, help="Number of top picks")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum entry score")
    parser.add_argument("--markdown", action="store_true", help="Output markdown report instead of JSON")
    parser.add_argument("--json", action="store_true", help="Output JSON report (default, kept for MCP compatibility)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Delay between scans (0=no delay when using OHLCV cache)")

    # New v3.0 flags
    parser.add_argument("--cache-ohlcv", action="store_true",
                        help="Pre-download all OHLCV data before scanning")
    parser.add_argument("--save-results", type=str, default=None, metavar="PATH",
                        help="Save full scan results JSON to this file (supports strftime: %%Y%%m%%d)")
    parser.add_argument("--gdrive-upload", action="store_true",
                        help="Upload saved results to Google Drive (requires --save-results)")
    parser.add_argument("--gdrive-folder", type=str, default="margin-ta-scans",
                        help="Google Drive folder name (default: margin-ta-scans)")

    args = parser.parse_args()

    # ── Phase 0: OHLCV Pre-download ──
    cache_date = datetime.now().strftime("%Y-%m-%d")
    ohlcv_cache_dir = None

    if args.cache_ohlcv:
        pre_download_ohlcv(cache_date, delay=0.3)
        ohlcv_cache_dir = os.path.join(CACHE_BASE, cache_date)
        # 캐시 디렉토리 존재 확인
        if not os.path.isdir(ohlcv_cache_dir):
            print(f"⚠️  OHLCV 캐시 디렉토리 없음: {ohlcv_cache_dir}. fallback: yfinance 직접 호출",
                  file=sys.stderr)
            ohlcv_cache_dir = None
        else:
            print(f"📂 OHLCV 캐시 사용: {ohlcv_cache_dir}", file=sys.stderr)

    # ── Phase 1: Scan ──
    tickers = load_tickers()
    print(f"🔍 Scanning {len(tickers)} tickers..." + 
          (" (OHLCV 캐시)" if ohlcv_cache_dir else " (yfinance 직접)"),
          file=sys.stderr)

    all_results = []
    errors = 0
    t_start = time.time()

    for i, sym in enumerate(tickers):
        if i > 0 and i % 100 == 0:
            elapsed = time.time() - t_start
            print(f"  [{i}/{len(tickers)}] {len(all_results)} valid, {errors} errors, {elapsed:.0f}s elapsed",
                  file=sys.stderr)

        data = scan_ticker(sym, ohlcv_cache_dir=ohlcv_cache_dir, timeout=15)
        if data and "signals" in data:
            score = data["signals"]["entry_score"]["score"]
            verdict = data["signals"]["entry_score"]["verdict"]
            all_results.append({
                "symbol": sym,
                "name": data["info"].get("name", sym),
                "score": score,
                "verdict": verdict,
                "price": data["current_price"],
                "strategies": [
                    s for s in data["pricing"]["strategies"]
                    if s.get("entry")
                ],
            })
        else:
            errors += 1

        # 캐시 모드일 땐 delay 불필요 (yfinance 호출 없음)
        if not ohlcv_cache_dir and args.delay > 0:
            time.sleep(args.delay)

    elapsed = time.time() - t_start
    print(f"✅ Done: {len(all_results)} valid, {errors} errors, {elapsed:.0f}s",
          file=sys.stderr)

    # Filter by min score
    valid_results = [r for r in all_results if r["score"] >= args.min_score]

    # Sort by score descending
    valid_results.sort(key=lambda x: x["score"], reverse=True)
    top = valid_results[:args.top]

    # ── Phase 2: Save Results ──
    gdrive_link = None
    results_path = None

    if args.save_results:
        # strftime 지원
        results_path = datetime.now().strftime(args.save_results)
        save_full_results(all_results, results_path, len(tickers), len(valid_results))

        if args.gdrive_upload:
            gdrive_link = upload_to_gdrive(results_path, folder_name=args.gdrive_folder)
            if gdrive_link and args.markdown:
                # 메타데이터만 로컬에 기록
                pass

    # ── Phase 3: Output ──
    if args.markdown:
        print(format_markdown(top, len(tickers), len(valid_results), gdrive_link=gdrive_link))
    else:
        print(format_json(top, len(tickers), len(valid_results)))


if __name__ == "__main__":
    main()
