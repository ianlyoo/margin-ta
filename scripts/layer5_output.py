"""
Layer 5 — Output: Rich 콘솔 테이블 + 매트플롯립 차트 + TradingView 링크
"""
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
import os


# ─── 전략 유형 메타 (layer4와 동기화) ────────────────────────────

_PLAN_META = {
    "support_bounce": {"emoji": "📍", "label": "Support Bounce"},
    "trend_confirm":  {"emoji": "📈", "label": "Trend Confirm"},
    "breakout":       {"emoji": "⚡", "label": "Breakout"},
    "no_signal":      {"emoji": "🚫", "label": "No Signal"},
}


def _render_plan_card(console, _fmt_price, plan, role="recommended"):
    """진입 전략 카드 렌더링. role: recommended / alternative / no_signal."""
    meta = _PLAN_META.get(plan["type"], {"emoji": "❓", "label": plan["type"]})
    emoji = meta["emoji"]
    label = meta["label"]
    quality = plan.get("quality", 0)

    # ── 역할별 스타일 ──
    if role == "recommended":
        border = "green" if quality >= 60 else "yellow"
        prefix = "⭐ 추천"
        if plan.get("conditional"):
            prefix = "⚡ 조건부 추천"
    elif role == "no_signal":
        border = "red"
        prefix = "🚫 관망"
    else:
        border = "dim"
        prefix = "📋 대안"

    # ── 카드 본문 ──
    lines = []

    # 트리거
    trigger = plan.get("trigger", "")
    if trigger:
        lines.append(f"  TRIGGER │ {trigger}")

    # 가격 정보 (진입/손절/리스크)
    if plan.get("entry"):
        entry_s = _fmt_price(plan["entry"])
        stop_s = _fmt_price(plan["stop"])
        risk_s = f"{plan['risk_pct']}%"
        lines.append(f"  ENTRY   │ {entry_s}   STOP {stop_s}   Risk {risk_s}")
    else:
        lines.append(f"  ENTRY   │ 없음")

    # 타겟 (인라인)
    targets = plan.get("targets") or []
    if targets:
        tgt_parts = []
        for t in targets[:3]:
            p = _fmt_price(t["price"])
            g = f"+{t['gain_pct']}%"
            # R:R 계산
            rr = ""
            if plan.get("entry") and plan.get("stop") and plan["entry"] > plan["stop"]:
                risk = plan["entry"] - plan["stop"]
                reward = t["price"] - plan["entry"]
                if risk > 0:
                    rr = f" R:R 1:{reward/risk:.1f}"
            tgt_parts.append(f"T{t['level']} {p}({g}){rr}")
        lines.append(f"  TARGETS │ {' · '.join(tgt_parts)}")

    # 사이즈 & 홀딩
    size = plan.get("size_pct", plan.get("size_pct_base", 0))
    hold = plan.get("hold_period")
    if size or hold:
        parts = []
        if size:
            parts.append(f"SIZE {size}%")
        if hold:
            parts.append(f"HOLD {hold}")
        lines.append(f"  META    │ {' · '.join(parts)}")

    # 유효기간
    valid = plan.get("valid_until")
    if valid:
        lines.append(f"  VALID   │ {valid}")

    # 무효화 조건
    inv = plan.get("invalidation")
    if inv:
        lines.append(f"  ❌ IF   │ {inv}")

    # 관망일 때 다음 조건
    next_conds = plan.get("next_conditions") or []
    if next_conds:
        lines.append(f"  👀 NEXT │ {next_conds[0]}")
        for nc in next_conds[1:]:
            lines.append(f"          │ {nc}")

    # 사이즈 노트
    size_note = plan.get("size_note")
    if size_note:
        lines.append(f"  ⚖ NOTE │ {size_note}")

    body = "\n".join(lines)
    title = f"{prefix}: {emoji} {label}"

    console.print(Panel(body, title=title, border_style=border, padding=(0, 1)))


# ─── Discord 전용 출력 포맷 ──────────────────────────────────────

def _fp(value, currency="USD"):
    """Discord용 가격 포맷."""
    if not isinstance(value, (int, float)):
        return "N/A"
    c = str(currency or "USD").upper()
    if c == "KRW":
        return f"₩{value:,.0f}"
    if c == "JPY":
        return f"¥{value:,.0f}"
    return f"${value:,.2f}"


def _box_line(content, width=56):
    """│ content + 우측 패딩 │"""
    # 이모지/한글은 폭 계산이 다르므로 대략적으로 맞춤
    visible = len(content)
    pad = max(1, width - visible - 2)
    return f"│ {content}{' ' * pad}│"


def _box_top(width=56):
    return "╭" + "─" * (width - 2) + "╮"


def _box_bot(width=56):
    return "╰" + "─" * (width - 2) + "╯"


def _box_sep(width=56):
    return "├" + "─" * (width - 2) + "┤"


def format_discord_output(data: dict) -> str:
    """분석 결과를 Discord 친화적 텍스트로 포맷."""
    lines = []
    W = 58  # 박스 너비

    info = data.get("info", {})
    signals = data.get("signals", {})
    pricing = data.get("pricing", {})
    derived = data.get("derived", {})
    market = data.get("market", {})
    liquidity = data.get("liquidity", {})
    warnings = data.get("warnings", [])
    tv = data.get("tradingview", {})
    session_quote = data.get("session_quote") or {}

    symbol = info.get("symbol", "?")
    name = info.get("name", "")
    currency = str(info.get("currency") or data.get("currency") or "USD").upper()
    current_price = info.get("previous_close") or data.get("current_price", 0)

    def fp(v):
        return _fp(v, currency)

    # ── 헤더 (마크다운) ──
    score = signals.get("entry_score", {})
    sc = score.get("score", 0)
    verdict = score.get("verdict", "")
    sc_icon = "🟢" if sc >= 70 else "🟡" if sc >= 50 else "🔴"

    lines.append(f"**📊 {symbol}** `{fp(current_price)}` — {name}")
    lines.append(f"{sc_icon} **Entry Score {sc}/100** {verdict}")
    lines.append("")

    # ── 가격/시장 박스 (코드블록) ──
    b = []
    b.append(_box_top(W))
    atr = derived.get("atr")
    atr_pct = derived.get("atr_pct")
    if atr:
        b.append(_box_line(f"📐 ATR(14)  {fp(atr)} ({atr_pct}%)", W))

    vix = (market.get("vix") or {})
    if vix.get("value"):
        b.append(_box_line(f"🌡 VIX {vix['value']:.1f} · {vix.get('regime','')} · {vix.get('trend','')}", W))

    # 유동성 구조
    liq = liquidity or {}
    avwap_lvls = (liq.get("anchored_vwap") or {}).get("levels", [])
    vp = liq.get("volume_profile") or {}

    if avwap_lvls or vp.get("poc"):
        b.append(_box_sep(W))
        b.append(_box_line("💧 Liquidity", W))
        for lv in avwap_lvls[:2]:
            name_s = (lv.get("name") or "AVWAP")[:12]
            role = lv.get("role", "")
            b.append(_box_line(f"  {name_s}  {fp(lv['price'])}  {lv.get('distance_pct',0):.1f}% {role}", W))
        if vp.get("poc"):
            in_va = "in VA" if vp.get("in_value_area") else "out VA"
            b.append(_box_line(f"  POC      {fp(vp['poc'])}  {abs(vp.get('dist_from_poc_pct',0)):.1f}% {in_va}", W))

    b.append(_box_bot(W))
    lines.append("```\n" + "\n".join(b) + "\n```")
    lines.append("")

    # ── 진입 전략 (코드블록) ──
    entry_plans = pricing.get("entry_plans") or {}
    recommended = entry_plans.get("recommended")
    alternatives = entry_plans.get("alternatives") or []

    plan_blocks = []
    if recommended:
        plan_blocks.append(_format_plan_discord(recommended, "recommended", fp, W))
    elif entry_plans.get("all_plans"):
        ns = next((p for p in entry_plans["all_plans"] if p["type"] == "no_signal"), None)
        if ns:
            plan_blocks.append(_format_plan_discord(ns, "no_signal", fp, W))

    for alt in alternatives[:2]:
        plan_blocks.append(_format_plan_discord(alt, "alternative", fp, W))

    # 레거시 fallback
    if not entry_plans and pricing.get("strategies"):
        for s in pricing["strategies"]:
            plan_blocks.append(_format_legacy_strategy(s, fp))

    if plan_blocks:
        lines.append("```\n" + "\n".join(plan_blocks) + "\n```")
        lines.append("")

    # ── Key Levels (코드블록) ──
    all_levels = (signals.get("all_levels") or {})
    supports = all_levels.get("supports", [])
    resistances = all_levels.get("resistances", [])

    if supports or resistances:
        lb = []
        lb.append(_box_top(W))
        lb.append(_box_line("📐 Key Levels", W))
        lb.append(_box_sep(W))
        for s in supports[:3]:
            dist = abs(current_price - s["price"]) / current_price * 100 if current_price else 0
            src = (s.get("source", "") or "")[:16]
            lb.append(_box_line(f"  SUP  {fp(s['price']):>10}  {dist:.1f}%↓  {src}", W))
        lb.append(_box_line("  " + "─" * (W - 6), W))
        for r in resistances[:3]:
            dist = abs(r["price"] - current_price) / current_price * 100 if current_price else 0
            src = (r.get("source", "") or "")[:16]
            lb.append(_box_line(f"  RES  {fp(r['price']):>10}  {dist:.1f}%↑  {src}", W))
        lb.append(_box_bot(W))
        lines.append("```\n" + "\n".join(lb) + "\n```")
        lines.append("")

    # ── Signal Details (마크다운) ──
    details = score.get("details", [])
    if details:
        total = sum(pts for _, pts in details)
        lines.append(f"🔍 **Signals** ({'+' if total >= 0 else ''}{total})")
        for name_s, pts in details:
            icon = "+" if pts > 0 else ""
            lines.append(f"  {'🟢' if pts > 0 else '🔴'} {icon}{pts} {name_s}")
        lines.append("")

    # ── TradingView (마크다운) ──
    tv_link = f"https://www.tradingview.com/chart/?symbol={info.get('exchange', 'NASDAQ')}:{symbol}"
    lines.append(f"🔗 [TradingView]({tv_link})")

    # ── Warnings ──
    if warnings:
        lines.append("")
        for w in warnings[:3]:
            lines.append(f"⚠️ {w}")

    return "\n".join(lines)


def _format_plan_discord(plan, role, fp, W):
    """단일 전략을 Discord 박스로 포맷."""
    meta = _PLAN_META.get(plan["type"], {"emoji": "❓", "label": plan["type"]})
    emoji = meta["emoji"]
    label = meta["label"]
    quality = plan.get("quality", 0)

    if role == "recommended":
        icon = "⭐ 추천" if not plan.get("conditional") else "⚡ 조건부"
        border = "🟢" if quality >= 60 else "🟡"
    elif role == "no_signal":
        icon = "🚫 관망"
        border = "🔴"
    else:
        icon = "📋 대안"
        border = "⚪"

    b = []
    b.append(_box_top(W))
    b.append(_box_line(f"{border} {icon}: {emoji} {label} (Q:{quality})", W))
    b.append(_box_sep(W))

    # 트리거
    trigger = plan.get("trigger", "")
    if trigger:
        # 길면 2줄로
        if len(trigger) > W - 6:
            words = trigger.split(" · ")
            line1, line2 = "", ""
            for w in words:
                if len(line1) + len(w) + 3 < W - 6:
                    line1 = f"{line1} · {w}" if line1 else w
                else:
                    line2 = f"{line2} · {w}" if line2 else w
            b.append(_box_line(f"  🎯 {line1}", W))
            if line2:
                b.append(_box_line(f"     {line2}", W))
        else:
            b.append(_box_line(f"  🎯 {trigger}", W))

    # 가격
    if plan.get("entry"):
        entry_s = fp(plan["entry"])
        stop_s = fp(plan["stop"])
        b.append(_box_line(f"  ▶ ENTRY {entry_s}  ✋ STOP {stop_s}  ⚠ {plan['risk_pct']}%", W))
    else:
        b.append(_box_line("  ▶ ENTRY 없음", W))

    # 타겟
    targets = plan.get("targets") or []
    if targets:
        tgt_parts = []
        for t in targets[:3]:
            p = fp(t["price"])
            g = f"+{t['gain_pct']}%"
            rr = ""
            if plan.get("entry") and plan.get("stop") and plan["entry"] > plan["stop"]:
                risk = plan["entry"] - plan["stop"]
                reward = t["price"] - plan["entry"]
                if risk > 0:
                    rr = f" (R:R 1:{reward/risk:.1f})"
            tgt_parts.append(f"T{t['level']} {p}{g}{rr}")
        for tp in tgt_parts:
            b.append(_box_line(f"  🎯 {tp}", W))

    # 메타
    size = plan.get("size_pct", plan.get("size_pct_base", 0))
    hold = plan.get("hold_period")
    valid = plan.get("valid_until")
    if size or hold:
        meta_parts = []
        if size:
            meta_parts.append(f"SIZE {size}%")
        if hold:
            meta_parts.append(f"HOLD {hold}")
        if valid:
            meta_parts.append(f"VALID {valid}")
        b.append(_box_line(f"  📊 {' · '.join(meta_parts)}", W))

    # 무효화
    inv = plan.get("invalidation")
    if inv:
        b.append(_box_line(f"  ❌ {inv}", W))

    # 관망 다음 조건
    for nc in (plan.get("next_conditions") or [])[:2]:
        b.append(_box_line(f"  👀 {nc}", W))

    # 사이즈 노트
    sn = plan.get("size_note")
    if sn:
        b.append(_box_line(f"  ⚖ {sn}", W))

    b.append(_box_bot(W))
    return "\n".join(b)


def _format_legacy_strategy(s, fp):
    """레거시 전략 포맷 (fallback)."""
    lines = []
    lines.append(f"**{s['type']}**")
    if s.get("entry"):
        lines.append(f"  ▶ {fp(s['entry'])} / STOP {fp(s['stop'])} / Risk {s['risk_pct']}%")
        if s.get("targets_rr"):
            rr = " · ".join(f"T{t['level']}: 1:{t['rr_ratio']}" for t in s["targets_rr"][:2])
            lines.append(f"  🎯 {rr}")
    lines.append(f"  _{s.get('condition', '')}_")
    lines.append("")
    return "\n".join(lines)


def print_margin_analysis(data: dict):
    """
    Rich 라이브러리로 전체 분석 결과를 콘솔에 출력.
    """
    console = Console(width=100)
    info = data["info"]
    signals = data["signals"]
    pricing = data["pricing"]
    derived = data.get("derived", {})
    tv = data.get("tradingview", {})
    warnings = data.get("warnings", [])
    liquidity = data.get("liquidity", {})
    market = data.get("market", {})
    flow = data.get("flow") or {}
    ownership = data.get("ownership") or {}
    session_quote = data.get("session_quote") or {}
    options_data = data.get("options") or {}
    currency = str(info.get("currency") or data.get("currency") or "USD").upper()

    def _fmt_price(value):
        if not isinstance(value, (int, float)):
            return "N/A"
        if currency == "KRW":
            return f"₩{value:,.0f}"
        if currency == "JPY":
            return f"¥{value:,.0f}"
        if currency == "USD":
            return f"${value:,.2f}"
        return f"{value:,.2f} {currency}"

    def _fmt_market_cap(value):
        if not isinstance(value, (int, float)):
            return "N/A"
        if currency == "KRW":
            return f"₩{value / 1e12:.1f}T" if value >= 1e12 else f"₩{value / 1e9:.1f}B"
        if currency == "USD":
            return f"${value / 1e9:.1f}B"
        return f"{value / 1e9:.1f}B {currency}"

    def _fmt_pct(value, signed=False):
        if not isinstance(value, (int, float)):
            return "N/A"
        return f"{value:+.1f}%" if signed else f"{value:.1f}%"

    # ===== HEADER =====
    title = Text()
    title.append(f"📊 {info['symbol']} ", style="bold white")
    title.append(f"{info['name']}", style="dim")
    console.print(Panel(title, border_style="cyan"))

    # Quick info line
    market_cap_str = _fmt_market_cap(info.get("market_cap"))
    beta_str = f"β={info['beta']:.2f}" if info.get("beta") else ""
    sector_str = info.get("sector", "N/A")
    console.print(f"  {sector_str} · Market Cap: {market_cap_str} · {beta_str}")
    console.print()

    # ===== PRICE & SCORE =====
    current_price = info.get("previous_close") or data["current_price"]
    score = signals["entry_score"]
    score_color = "green" if score["score"] >= 70 else "yellow" if score["score"] >= 50 else "red"

    price_table = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 2))
    price_table.add_column(style="dim")
    price_table.add_column(style="bold")
    price_table.add_row("💰 Current Price", _fmt_price(current_price))
    regular_close = info.get("regular_close")
    active_source = session_quote.get("active_source")
    session_info = session_quote.get("session") or {}
    quote = session_quote.get("quote") or {}
    if active_source and active_source != "yfinance_daily":
        price_table.add_row(
            "🕒 Session Quote",
            f"{session_info.get('session')} · {active_source} · "
            f"{quote.get('excd') or quote.get('market_state') or ''}".strip(" ·"),
        )
        if regular_close:
            change = (current_price / regular_close - 1) * 100
            price_table.add_row("🌙 vs Regular Close", f"{_fmt_price(regular_close)} ({change:+.2f}%)")
    elif session_info.get("session"):
        price_table.add_row(
            "🕒 Session",
            f"{session_info.get('session')} · quote source {active_source or 'yfinance_daily'}",
        )
    price_table.add_row(
        "🎯 Entry Score",
        f"[{score_color}]{score['score']}/100[/{score_color}]  {score['verdict']}"
    )
    # ATR
    atr_val = derived.get("atr") or (pricing["strategies"][0].get("atr") if pricing["strategies"] else None)
    atr_pct_val = derived.get("atr_pct") or (pricing["strategies"][0].get("atr_pct") if pricing["strategies"] else None)
    if atr_val:
        price_table.add_row("📐 ATR (14)", f"{_fmt_price(atr_val)} ({atr_pct_val}%)")
    vix = market.get("vix") or {}
    if vix.get("value"):
        price_table.add_row(
            "🌡 VIX Regime",
            f"{vix['value']:.2f} · {vix.get('regime')} · {vix.get('trend')} · size x{vix.get('size_multiplier', 1.0)}",
        )
    breadth = market.get("breadth") or {}
    if breadth.get("above_200ma_pct") is not None or breadth.get("above_50ma_pct") is not None:
        price_table.add_row(
            "🌐 Breadth",
            f"50MA {breadth.get('above_50ma_pct', 'N/A')}% · "
            f"200MA {breadth.get('above_200ma_pct', 'N/A')}% · {breadth.get('regime')}",
        )
    combined = market.get("combined") or {}
    if combined.get("size_multiplier") and combined.get("size_multiplier") != 1.0:
        price_table.add_row("⚖ Market Size", f"x{combined.get('size_multiplier')} · {combined.get('risk_mode')}")
    console.print(price_table)
    console.print()

    # ===== LIQUIDITY STRUCTURE =====
    avwap_levels = liquidity.get("anchored_vwap", {}).get("levels", [])
    volume_profile = liquidity.get("volume_profile", {})
    if avwap_levels or volume_profile.get("poc"):
        liq_table = Table(box=box.SIMPLE_HEAD, title="💧 Liquidity Structure", padding=(0, 2))
        liq_table.add_column("Type", style="dim")
        liq_table.add_column("Price")
        liq_table.add_column("Dist %")
        liq_table.add_column("Context", style="dim")

        for lv in avwap_levels[:2]:
            liq_table.add_row(
                lv.get("name", "AVWAP"),
                _fmt_price(lv["price"]),
                f"{lv.get('distance_pct', 0):.1f}%",
                f"{lv.get('role')} · {lv.get('slope_regime')} · {lv.get('anchor_reason')}",
            )
        if volume_profile.get("poc"):
            liq_table.add_row(
                "Volume POC",
                _fmt_price(volume_profile["poc"]),
                f"{abs(volume_profile.get('dist_from_poc_pct', 0)):.1f}%",
                "inside VA" if volume_profile.get("in_value_area") else "outside VA",
            )
            liq_table.add_row(
                "Value Area",
                f"{_fmt_price(volume_profile.get('value_area_low'))} - {_fmt_price(volume_profile.get('value_area_high'))}",
                "",
                f"{volume_profile.get('lookback')}d profile",
            )
        console.print(liq_table)
        console.print()

    # ===== OPTIONS STRUCTURE =====
    if options_data:
        opt_table = Table(box=box.SIMPLE_HEAD, title="Options Structure", padding=(0, 2))
        opt_table.add_column("Factor", style="dim")
        opt_table.add_column("Value")
        opt_table.add_column("Context", style="dim")

        selected = options_data.get("selected_expiry") or {}
        quality = options_data.get("chain_quality") or {}
        max_pain = options_data.get("max_pain") or {}
        pcr = options_data.get("put_call_ratios") or {}
        walls = options_data.get("walls") or {}
        vol = options_data.get("volatility") or {}
        greeks = options_data.get("greeks_exposure") or {}
        oi_change = options_data.get("oi_change") or {}
        overlay = options_data.get("score_overlay") or {}

        opt_table.add_row(
            "Expiry",
            selected.get("expiry") or "unavailable",
            f"DTE {selected.get('days_to_expiry', 'N/A')} · size {selected.get('size_score', 0):,.0f}",
        )
        opt_table.add_row(
            "Chain Quality",
            f"{quality.get('quality_status', 'unavailable')} ({quality.get('quality_score', 0)}/100)",
            f"{options_data.get('provider_used') or options_data.get('source')} · "
            f"{quality.get('filtered_contracts', 0)}/{quality.get('raw_contracts', 0)} contracts",
        )
        if max_pain:
            opt_table.add_row(
                "Max Pain",
                _fmt_price(max_pain.get("price")),
                f"spot distance {_fmt_pct(max_pain.get('distance_pct'), signed=True)}",
            )
        opt_table.add_row(
            "Put/Call",
            f"OI {pcr.get('oi', 'N/A')} · Vol {pcr.get('volume', 'N/A')}",
            "open interest / volume ratio",
        )
        opt_table.add_row(
            "Walls",
            f"CW {_fmt_price(walls.get('call_wall'))} · PW {_fmt_price(walls.get('put_wall'))}",
            f"CW {_fmt_pct(walls.get('dist_to_call_wall_pct'), signed=True)} · "
            f"PW {_fmt_pct(walls.get('dist_to_put_wall_pct'), signed=True)} · "
            f"pin {walls.get('top_strike_oi_concentration', 'N/A')}",
        )
        if vol.get("status") == "available":
            opt_table.add_row(
                "Expected Move",
                f"{_fmt_price(vol.get('expected_move'))} ({_fmt_pct(vol.get('expected_move_pct'))})",
                f"ATM {vol.get('atm_strike')} straddle · IV/RV {vol.get('iv_rv_premium', 'N/A')}",
            )
        opt_table.add_row(
            "Gamma",
            greeks.get("gamma_regime") or greeks.get("status", "unavailable"),
            f"wall {_fmt_price(greeks.get('gamma_wall'))} · zero {_fmt_price(greeks.get('zero_gamma_level'))} · "
            f"{greeks.get('source', 'chain')}",
        )
        if oi_change.get("status") == "available":
            top_change = (oi_change.get("top_changes") or [{}])[0]
            opt_table.add_row(
                "OI Change",
                f"{_fmt_price(top_change.get('strike'))} {top_change.get('total_oi_change', 0):+,.0f}",
                f"vs {oi_change.get('previous_generated_at')}",
            )
        else:
            opt_table.add_row("OI Change", "unavailable", oi_change.get("reason", "first local snapshot"))
        opt_table.add_row(
            "Score Overlay",
            f"{overlay.get('clamped_score', 0):+d}" if isinstance(overlay.get("clamped_score"), int) else str(overlay.get("clamped_score", 0)),
            "bounded ±8 · disabled when quality is weak",
        )
        console.print(opt_table)
        console.print()

    # ===== EXTERNAL FLOW =====
    if flow:
        flow_table = Table(box=box.SIMPLE_HEAD, title="🌒 External Flow", padding=(0, 2))
        flow_table.add_column("Type", style="dim")
        flow_table.add_column("Value")
        flow_table.add_column("Context", style="dim")

        dark_pool = flow.get("dark_pool", {})
        current_dp = dark_pool.get("current") or {}
        if current_dp.get("dark_pool_percentage") is not None:
            flow_table.add_row(
                "Dark Pool",
                f"{current_dp.get('dark_pool_percentage'):.2f}% · {current_dp.get('dark_pool_volume', 0):,} sh",
                f"{dark_pool.get('source')} · {current_dp.get('trading_date') or current_dp.get('datetime') or ''}",
            )

        flow_options = {} if options_data else (flow.get("options") or {})
        summaries = flow_options.get("summary_by_expiry") or []
        primary_chain = flow_options.get("primary_chain_feature") or {}
        if primary_chain and primary_chain.get("quality_status") in {"good", "usable"}:
            value_parts = []
            if primary_chain.get("max_pain"):
                value_parts.append(f"MP {_fmt_price(primary_chain.get('max_pain'))}")
            if primary_chain.get("put_call_ratio") is not None:
                value_parts.append(f"P/C {primary_chain.get('put_call_ratio'):.2f}")
            if primary_chain.get("call_wall"):
                value_parts.append(f"CW {_fmt_price(primary_chain.get('call_wall'))}")
            if primary_chain.get("put_wall"):
                value_parts.append(f"PW {_fmt_price(primary_chain.get('put_wall'))}")
            flow_table.add_row(
                "Options",
                " · ".join(value_parts) if value_parts else "chain available",
                f"{primary_chain.get('expiry')} · DTE {primary_chain.get('days_to_expiry')} · "
                f"{primary_chain.get('source')} {primary_chain.get('quality_status')}({primary_chain.get('quality_score')})",
            )
        elif summaries:
            first = next((x for x in summaries if x.get("max_pain")), summaries[0])
            flow_table.add_row(
                "Options",
                f"Max Pain {_fmt_price(first.get('max_pain', 0))} · P/C {first.get('put_call_ratio', 0):.2f}",
                f"{first.get('expiry')} · DTE {first.get('days_to_expiry')} · summary fallback",
            )

        short = flow.get("short") or {}
        current_short = short.get("current") or {}
        if current_short:
            short_parts = []
            if current_short.get("short_percent") is not None:
                short_parts.append(f"SV {current_short.get('short_percent'):.2f}%")
            if current_short.get("short_interest_percent") is not None:
                short_parts.append(f"SI {current_short.get('short_interest_percent'):.2f}%")
            if current_short.get("ctb_rate") is not None:
                short_parts.append(f"CTB {current_short.get('ctb_rate'):.2f}%")
            date = current_short.get("short_volume_date") or current_short.get("short_interest_date") or ""
            context = f"{short.get('source')} · {date}".strip(" ·")
            if current_short.get("pressure"):
                context = f"{context} · {current_short.get('pressure')}".strip(" ·")
            flow_table.add_row(
                "Short",
                " · ".join(short_parts) if short_parts else "available",
                context,
            )
        console.print(flow_table)
        console.print()

    # ===== BENEFICIAL OWNERSHIP =====
    if ownership and ownership.get("event_count"):
        ownership_table = Table(box=box.SIMPLE_HEAD, title="SEC 13D/G Ownership", padding=(0, 2))
        ownership_table.add_column("Form", style="dim")
        ownership_table.add_column("Owner")
        ownership_table.add_column("Stake")
        ownership_table.add_column("Context", style="dim")
        for event in (ownership.get("events") or [])[:3]:
            delta = event.get("ownership_delta_pct")
            delta_text = f"{delta:+.1f}pp" if isinstance(delta, (int, float)) else "n/a"
            flags = []
            if event.get("activist_intent_flag"):
                flags.append("active/control")
            if event.get("passive_holder_flag"):
                flags.append(event.get("owner_type", "passive"))
            if event.get("dropped_below_5pct_flag"):
                flags.append("below 5%")
            if event.get("converted_13g_to_13d_flag"):
                flags.append("13G→13D")
            ownership_table.add_row(
                event.get("form_type", "N/A"),
                str(event.get("reporting_owner") or "holder")[:36],
                f"{event.get('ownership_pct', 'N/A')}% ({delta_text})",
                f"{event.get('filing_date', 'N/A')} · {' · '.join(flags)}".strip(" ·"),
            )
        console.print(ownership_table)
        console.print()

    # ===== ENTRY PLANS (v4.0) =====
    entry_plans = pricing.get("entry_plans") or {}
    recommended = entry_plans.get("recommended")
    alternatives = entry_plans.get("alternatives") or []
    plan_summary = entry_plans.get("summary", "")

    if recommended:
        _render_plan_card(console, _fmt_price, recommended, "recommended")
    elif entry_plans.get("all_plans"):
        # 추천 없으면 no_signal 플랜 표시
        ns = next((p for p in entry_plans["all_plans"] if p["type"] == "no_signal"), None)
        if ns:
            _render_plan_card(console, _fmt_price, ns, "no_signal")

    # 대안 전략 (최대 2개)
    for alt in alternatives:
        _render_plan_card(console, _fmt_price, alt, "alternative")

    # 레거시 fallback (entry_plans 없으면 기존 strategies 표시)
    if not entry_plans and pricing.get("strategies"):
        for s in pricing["strategies"]:
            strat_table = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 2))
            strat_table.add_column(style="dim", width=14)
            strat_table.add_column()
            strat_table.add_row("Strategy", f"[bold]{s['type']}[/bold]")
            if s.get("entry"):
                strat_table.add_row("Entry", f"[green]{_fmt_price(s['entry'])}[/green]")
                strat_table.add_row("Stop-Loss", f"[red]{_fmt_price(s['stop'])}[/red]  (Risk: {s['risk_pct']}%)")
                if "targets_rr" in s and s["targets_rr"]:
                    rr_str = " · ".join(f"T{t['level']}: 1:{t['rr_ratio']}" for t in s["targets_rr"][:2])
                    strat_table.add_row("R:R (vs T1/T2)", rr_str)
                strat_table.add_row("Allocation", f"{s['size_pct']}% of position")
            strat_table.add_row("Condition", f"[dim italic]{s['condition']}[/dim italic]")
            console.print(strat_table)
            console.print()

    # ===== TARGETS (레거시) =====
    if not entry_plans and pricing.get("targets"):
        tgt_table = Table(box=box.SIMPLE_HEAD, title="🎯 Targets", padding=(0, 2))
        tgt_table.add_column("Level", style="dim")
        tgt_table.add_column("Price", style="green")
        tgt_table.add_column("Gain %", style="cyan")
        tgt_table.add_column("Source", style="dim italic")
        for t in pricing["targets"]:
            tgt_table.add_row(f"T{t['level']}", _fmt_price(t["price"]), f"+{t['gain_pct']}%", t.get("source", ""))
        console.print(tgt_table)
        console.print()

    # ===== KEY LEVELS =====
    all_levels = signals["all_levels"]
    lvl_table = Table(box=box.SIMPLE_HEAD, padding=(0, 2))
    lvl_table.add_column("Type", style="dim", width=10)
    lvl_table.add_column("Price", width=12)
    lvl_table.add_column("Source", style="dim", width=18)
    lvl_table.add_column("Dist %", width=8)

    # Supports (top 4)
    for s in all_levels["supports"][:4]:
        dist = abs(current_price - s["price"]) / current_price * 100
        lvl_table.add_row(
            "[green]SUPPORT[/green]",
            f"[green]{_fmt_price(s['price'])}[/green]",
            s.get("source", ""),
            f"{dist:.1f}% ↓",
        )
    # Separator
    lvl_table.add_row("", "───────", "", "")
    # Resistances (top 4)
    for r in all_levels["resistances"][:4]:
        dist = abs(r["price"] - current_price) / current_price * 100
        lvl_table.add_row(
            "[red]RESIST[/red]",
            f"[red]{_fmt_price(r['price'])}[/red]",
            r.get("source", ""),
            f"{dist:.1f}% ↑",
        )
    console.print(Panel(lvl_table, title="📈 Key Levels", border_style="dim"))
    console.print()

    # ===== HORIZONS (multi-horizon stance) =====
    horizons = data.get("horizons") or {}
    if horizons:
        hz_table = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 2))
        hz_table.add_column(style="dim", width=10)
        hz_table.add_column()
        stance_color = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
        for name, label in (("short", "Short"), ("mid", "Mid"), ("long", "Long")):
            hz = horizons.get(name) or {}
            stance = hz.get("stance")
            if stance == "insufficient_data":
                hz_table.add_row(label, "[dim]데이터 부족[/dim]")
            elif stance:
                color = stance_color.get(stance, "white")
                score = hz.get("score", 0)
                hz_table.add_row(label, f"[{color}]{stance}[/{color}] ({score:+d})")
            else:
                hz_table.add_row(label, "[dim]N/A[/dim]")
        hz_table.add_row("Alignment", str(horizons.get("alignment", "unknown")))
        console.print(Panel(hz_table, title="🕐 호라이즌", border_style="dim"))
        console.print()

    # ===== CONSENSUS (지표 합의도 & 충돌) =====
    consensus = data.get("consensus") or {}
    if consensus:
        conflict_labels = {
            "momentum_vs_trend": "모멘텀·추세 대립",
            "volume_vs_price": "가격·거래량 괴리",
            "horizon_conflict": "단기·장기 시야 충돌",
            "bearish_divergence": "약세 다이버전스",
            "bullish_divergence": "강세 다이버전스",
        }
        cons_table = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 2))
        cons_table.add_column(style="dim", width=8)
        cons_table.add_column()
        agreement = consensus.get("agreement")
        agreement_str = f"{agreement}/100" if agreement is not None else "N/A (방향성 지표 부족)"
        cons_table.add_row("합의도", agreement_str)
        conflicts = consensus.get("conflicts") or []
        conflict_str = ", ".join(conflict_labels.get(c, c) for c in conflicts) if conflicts else "없음"
        cons_table.add_row("충돌", conflict_str)
        console.print(Panel(cons_table, title="🤝 컨센서스", border_style="dim"))
        console.print()

    # ===== SR TIERS (핵심 레벨: weekly/monthly/all-time major) =====
    sr_tiers = data.get("sr_tiers") or {}
    if sr_tiers.get("key_below_top3") or sr_tiers.get("key_above_top3"):
        key_table = Table(box=box.SIMPLE_HEAD, padding=(0, 2))
        key_table.add_column("", style="dim", width=8)
        key_table.add_column("Price", width=12)
        key_table.add_column("Source", style="dim", width=18)
        key_table.add_column("Dist %", width=8)
        for lv in sr_tiers.get("key_below_top3", []):
            key_table.add_row(
                "[green]지지[/green]",
                f"[green]{_fmt_price(lv['price'])}[/green]",
                lv.get("source", ""),
                f"{lv['distance_pct']:+.1f}%",
            )
        key_table.add_row("", "───────", "", "")
        for lv in sr_tiers.get("key_above_top3", []):
            key_table.add_row(
                "[red]저항[/red]",
                f"[red]{_fmt_price(lv['price'])}[/red]",
                lv.get("source", ""),
                f"{lv['distance_pct']:+.1f}%",
            )
        console.print(Panel(key_table, title="🏔 핵심 레벨", border_style="dim"))
        console.print()

    # ===== SIGNAL DETAILS =====
    if signals["entry_score"]["details"]:
        detail_text = Text()
        total = 0
        for name, pts in signals["entry_score"]["details"]:
            color = "green" if pts > 0 else "red"
            detail_text.append(f"  {'+' if pts > 0 else ''}{pts}  ", style=color)
            detail_text.append(f"{name}\n", style="dim")
            total += pts
        console.print(Panel(detail_text, title=f"🔍 Signal Details (net: {'+' if total >= 0 else ''}{total})", border_style="dim"))
        console.print()

    # ===== FIBONACCI MULTI-TIMEFRAME CONFLUENCE (v3.2) =====
    fib_data = data.get("fibonacci", {})
    confluence = fib_data.get("confluence", [])
    if confluence:
        cf_table = Table(box=box.SIMPLE_HEAD, title="🔀 Fib Multi-Timeframe Confluence", padding=(0, 2))
        cf_table.add_column("Ratio", style="cyan")
        cf_table.add_column("Price", style="green")
        cf_table.add_column("Timeframes", style="dim")
        cf_table.add_column("Count", style="yellow")
        cf_table.add_column("Role / Dist%", style="dim")
        for cf in confluence[:5]:
            role_str = f"{cf['role']} · {cf['distance_pct']:.1f}%"
            cf_table.add_row(
                f"Fib {cf['ratio']}",
                _fmt_price(cf["price"]),
                " × ".join(cf["timeframes"]),
                f"×{cf['count']}",
                role_str,
            )
        console.print(cf_table)
        console.print()

    # ===== TRADINGVIEW =====
    if tv and not tv.get("error"):
        tv_table = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 2))
        tv_table.add_column(style="dim", width=14)
        tv_table.add_column()
        summary = tv.get("summary", {})
        tv_table.add_row("TV Summary", f"🟢{summary.get('BUY',0)} · ⚪{summary.get('NEUTRAL',0)} · 🔴{summary.get('SELL',0)}")
        ind = tv.get("indicators", {})
        if ind.get("rsi"):
            tv_table.add_row("TV RSI", str(ind["rsi"]))
        if ind.get("sma_50") and ind.get("sma_200"):
            tv_table.add_row("TV MA", f"50={_fmt_price(ind['sma_50'])} · 200={_fmt_price(ind['sma_200'])}")
        console.print(tv_table)

    tv_link = generate_tradingview_link(info["symbol"], info.get("exchange", "NASDAQ"))
    console.print(f"  🔗 [link={tv_link}]TradingView Chart[/link]")
    console.print()

    # ===== WARNINGS =====
    if warnings:
        warn_text = Text()
        for w in warnings:
            warn_text.append(f"  ⚠️  {w}\n", style="yellow")
        console.print(Panel(warn_text, title="⚠️  Risk Alerts", border_style="yellow"))

    console.print()


def generate_tradingview_link(symbol: str, exchange: str = "NASDAQ") -> str:
    """TradingView 원클릭 차트 링크 생성."""
    return f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}"


def generate_chart_png(
    df,
    symbol: str,
    current_price: float,
    all_levels: dict,
    output_dir: str,
    fib_data: dict | None = None,
    options: dict | None = None,
    currency: str = "USD",
) -> str | None:
    """
    matplotlib 5-패널 트레이딩 차트 생성 (v3.1 — Multi-Indicator).
    Panel 1: Price + BB + 4 MAs + S/R + Ichimoku Cloud
    Panel 2: Volume + CMF Money Flow
    Panel 3: MACD (line + signal + histogram)
    Panel 4: RSI (14) oscillator
    Panel 5: Stochastic (%K/%D) + ADX overlay
    Returns file path or None.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        currency = str(currency or "USD").upper()

        def _fmt_chart_price(value):
            if not isinstance(value, (int, float)):
                return "N/A"
            if currency == "KRW":
                return f"₩{value:,.0f}"
            if currency == "JPY":
                return f"¥{value:,.0f}"
            if currency == "USD":
                return f"${value:,.2f}"
            return f"{value:,.2f} {currency}"

        # 커스텀 스타일
        plt.style.use("dark_background")
        fig = plt.figure(figsize=(18, 15), facecolor="#0d1117")
        gs = fig.add_gridspec(
            5, 1,
            height_ratios=[3.5, 1.0, 1.3, 1.3, 1.5],
            hspace=0.08,
        )
        ax1 = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1])
        ax3 = fig.add_subplot(gs[2])
        ax4 = fig.add_subplot(gs[3])
        ax5 = fig.add_subplot(gs[4])

        # 최근 6개월 데이터
        plot_df = df.iloc[-126:].copy()
        dates = plot_df.index

        # ==============================================
        # PANEL 1: Price + BB + MAs + S/R + Ichimoku Cloud
        # ==============================================
        # Close price
        ax1.plot(dates, plot_df.Close, color="#58a6ff", linewidth=1.5, label="Close", zorder=3)

        # Bollinger Bands
        if "BBU_20_2.0" in plot_df.columns:
            ax1.plot(dates, plot_df["BBU_20_2.0"], color="#5a6570", linewidth=0.8, linestyle="--", alpha=0.75)
            ax1.plot(dates, plot_df["BBL_20_2.0"], color="#5a6570", linewidth=0.8, linestyle="--", alpha=0.75)
            ax1.fill_between(
                dates,
                plot_df["BBU_20_2.0"],
                plot_df["BBL_20_2.0"],
                color="#484f58", alpha=0.12,
                label="Bollinger (20,2)",
            )

        # Ichimoku Cloud (span A → span B fill)
        has_cloud = all(c in plot_df.columns for c in ["ICH_span_a", "ICH_span_b"])
        if has_cloud:
            ax1.fill_between(
                dates,
                plot_df["ICH_span_a"],
                plot_df["ICH_span_b"],
                where=plot_df["ICH_span_a"] >= plot_df["ICH_span_b"],
                color="#3fb950", alpha=0.10, interpolate=True,
                label="Ichimoku Cloud",
            )
            ax1.fill_between(
                dates,
                plot_df["ICH_span_a"],
                plot_df["ICH_span_b"],
                where=plot_df["ICH_span_a"] < plot_df["ICH_span_b"],
                color="#f85149", alpha=0.10, interpolate=True,
            )

        # MA lines (EMA20, SMA50, SMA100, SMA200)
        ma_configs = [
            ("EMA_20", "#d2a8ff", 0.85, "-"),
            ("SMA_50", "#ffa657", 0.85, "-"),
            ("SMA_100", "#56d364", 0.55, "-"),
            ("SMA_200", "#f778ba", 0.65, "-"),
        ]
        for ma_col, color, alpha, ls in ma_configs:
            if ma_col in plot_df.columns:
                ax1.plot(dates, plot_df[ma_col], color=color, linewidth=0.9, alpha=alpha, linestyle=ls, label=ma_col.replace("_", " "))

        # S/R lines (horizontal) — clean lines only, no price labels on chart
        support_prices = []
        resistance_prices = []
        for s in all_levels.get("supports", [])[:3]:
            if s["price"] < current_price * 1.1 and s["price"] > current_price * 0.8:
                is_major = s.get("tier") == "major"
                ax1.axhline(
                    y=s["price"], color="#3fb950",
                    linewidth=2.5 if is_major else 1.8,
                    linestyle="--", alpha=1.0 if is_major else 0.85,
                )
                support_prices.append(s["price"])

        for r in all_levels.get("resistances", [])[:3]:
            if r["price"] < current_price * 1.2 and r["price"] > current_price * 0.9:
                is_major = r.get("tier") == "major"
                ax1.axhline(
                    y=r["price"], color="#f85149",
                    linewidth=2.5 if is_major else 1.8,
                    linestyle="--", alpha=1.0 if is_major else 0.85,
                )
                resistance_prices.append(r["price"])

        # Current price line (subtle)
        ax1.axhline(y=current_price, color="#e6edf3", linewidth=0.9, linestyle="-", alpha=0.5)

        # Canonical options reference lines
        if options:
            option_lines = [
                ("Max Pain", (options.get("max_pain") or {}).get("price"), "#f2cc60", ":"),
                ("Call Wall", (options.get("walls") or {}).get("call_wall"), "#f85149", "-."),
                ("Put Wall", (options.get("walls") or {}).get("put_wall"), "#3fb950", "-."),
            ]
            seen_option_prices = set()
            for label, price, color, linestyle in option_lines:
                if not isinstance(price, (int, float)):
                    continue
                if not (current_price * 0.8 < price < current_price * 1.2):
                    continue
                rounded_price = round(float(price), 2)
                if rounded_price in seen_option_prices:
                    continue
                seen_option_prices.add(rounded_price)
                ax1.axhline(y=price, color=color, linewidth=1.1, linestyle=linestyle, alpha=0.75)
                ax1.annotate(
                    label,
                    xy=(dates[-1], price),
                    xytext=(10, 0),
                    textcoords="offset points",
                    fontsize=5.5,
                    color=color,
                    alpha=0.85,
                    va="center",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="#161b22", edgecolor=color, alpha=0.65),
                )

        # Multi-timeframe Fibonacci lines (v3.2)
        if fib_data:
            fib_styles = [
                ("primary", "#f0883e", 0.7, "--"),      # 52W: orange dashed
                ("secondary", "#d2a8ff", 0.45, ":"),    # 104W: purple dotted
                ("alltime", "#8b949e", 0.35, "-."),     # All-Time: grey dash-dot
            ]
            for key, color, alpha_val, ls in fib_styles:
                fib_set = fib_data.get(key)
                if not fib_set or "levels" not in fib_set:
                    continue
                for name, lv in fib_set["levels"].items():
                    p = lv["price"]
                    if current_price * 0.8 < p < current_price * 1.2:
                        ax1.axhline(y=p, color=color, linewidth=1.0, linestyle=ls, alpha=alpha_val)

            # Confluence highlights
            for cf in fib_data.get("confluence", []):
                if cf.get("count", 0) >= 2:
                    p = cf["price"]
                    if current_price * 0.8 < p < current_price * 1.2:
                        ax1.axhline(y=p, color="#f0883e", linewidth=1.8, linestyle="--", alpha=0.75)
                        ax1.annotate(
                            f"Fib {cf['ratio']} ×{cf['count']}",
                            xy=(dates[-1], p),
                            xytext=(10, 0),
                            textcoords="offset points",
                            fontsize=5.5,
                            color="#f0883e",
                            alpha=0.85,
                            va="center",
                            bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a1a2e", edgecolor="#f0883e", alpha=0.7),
                        )

        ax1.set_ylabel(f"Price ({currency})", color="#8b949e", fontsize=8)
        ax1.legend(loc="upper left", fontsize=6.5, framealpha=0.25, facecolor="#161b22",
                   ncol=2, edgecolor="#30363d")
        ax1.grid(color="#30363d", linewidth=0.5, alpha=0.5)
        ax1.tick_params(colors="#8b949e", labelsize=7, bottom=False, labelbottom=False)
        ax1.set_facecolor("#0d1117")

        # ==============================================
        # PANEL 2: Volume + CMF Money Flow
        # ==============================================
        # Volume bars (green/red based on close vs open)
        vol_colors = ["#3fb950" if plot_df.Close.iloc[i] >= plot_df.Open.iloc[i] else "#f85149"
                      for i in range(len(plot_df))]
        ax2.bar(dates, plot_df.Volume, color=vol_colors, alpha=0.65, width=1.0, label="Volume")
        ax2.set_ylabel("Volume", color="#8b949e", fontsize=7)
        ax2.grid(color="#30363d", linewidth=0.4, alpha=0.5)
        ax2.tick_params(colors="#8b949e", labelsize=7, bottom=False, labelbottom=False)
        # Format volume labels
        from matplotlib.ticker import FuncFormatter
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x/1e6:.0f}M" if x >= 1e6 else f"{x/1e3:.0f}K" if x >= 1e3 else f"{x:.0f}"))
        ax2.set_facecolor("#0d1117")

        # CMF overlay on twin axis
        if "CMF_20" in plot_df.columns:
            ax2_cmf = ax2.twinx()
            ax2_cmf.plot(dates, plot_df["CMF_20"], color="#79c0ff", linewidth=1.1, alpha=0.95, label="CMF(20)")
            ax2_cmf.axhline(y=0, color="#8b949e", linewidth=0.5, linestyle="-", alpha=0.5)
            ax2_cmf.set_ylabel("CMF", color="#79c0ff", fontsize=7)
            ax2_cmf.tick_params(colors="#79c0ff", labelsize=6)
            # Legend for CMF
            ax2_cmf.legend(loc="upper right", fontsize=6, framealpha=0.25, facecolor="#161b22", edgecolor="#30363d")
        # Legend for Volume
        ax2.legend(loc="upper left", fontsize=6, framealpha=0.25, facecolor="#161b22", edgecolor="#30363d")

        # ==============================================
        # PANEL 3: MACD
        # ==============================================
        has_macd = all(c in plot_df.columns for c in ["MACD_12_26_9", "MACDs_12_26_9", "MACDh_12_26_9"])
        if has_macd:
            macd_line = plot_df["MACD_12_26_9"]
            signal_line = plot_df["MACDs_12_26_9"]
            histogram = plot_df["MACDh_12_26_9"]

            ax3.plot(dates, macd_line, color="#58a6ff", linewidth=1.2, label="MACD")
            ax3.plot(dates, signal_line, color="#ffa657", linewidth=1.0, label="Signal")
            # Histogram bars
            hist_colors = ["#3fb950" if v >= 0 else "#f85149" for v in histogram]
            ax3.bar(dates, histogram, color=hist_colors, alpha=0.6, width=1.0)

            ax3.axhline(y=0, color="#8b949e", linewidth=0.5, linestyle="-", alpha=0.55)
            ax3.set_ylabel("MACD", color="#8b949e", fontsize=7)
            ax3.legend(loc="upper left", fontsize=6, framealpha=0.25, facecolor="#161b22", ncol=2, edgecolor="#30363d")
        ax3.grid(color="#30363d", linewidth=0.4, alpha=0.5)
        ax3.tick_params(colors="#8b949e", labelsize=7, bottom=False, labelbottom=False)
        ax3.set_facecolor("#0d1117")

        # ==============================================
        # PANEL 4: RSI (14)
        # ==============================================
        if "RSI_14" in plot_df.columns:
            ax4.plot(dates, plot_df["RSI_14"], color="#d2a8ff", linewidth=1.3, label="RSI(14)")
            ax4.axhline(y=70, color="#f85149", linewidth=0.6, linestyle="--", alpha=0.65)
            ax4.axhline(y=30, color="#3fb950", linewidth=0.6, linestyle="--", alpha=0.65)
            ax4.axhline(y=50, color="#8b949e", linewidth=0.4, linestyle=":", alpha=0.4)
            ax4.fill_between(dates, 30, 70, color="#30363d", alpha=0.15)
            # Overbought / oversold fill
            ax4.fill_between(dates, 70, 100, color="#f85149", alpha=0.06)
            ax4.fill_between(dates, 0, 30, color="#3fb950", alpha=0.06)
            ax4.set_ylabel("RSI", color="#d2a8ff", fontsize=7)
            ax4.set_ylim(0, 100)
            ax4.legend(loc="upper left", fontsize=6, framealpha=0.25, facecolor="#161b22", edgecolor="#30363d")
        ax4.grid(color="#30363d", linewidth=0.4, alpha=0.5)
        ax4.tick_params(colors="#8b949e", labelsize=7, bottom=False, labelbottom=False)
        ax4.set_facecolor("#0d1117")

        # ==============================================
        # PANEL 5: Stochastic (%K/%D) + ADX
        # ==============================================
        has_stoch = all(c in plot_df.columns for c in ["STOCHk_14_3_3", "STOCHd_14_3_3"])
        has_adx = "ADX_14" in plot_df.columns

        if has_stoch:
            ax5.plot(dates, plot_df["STOCHk_14_3_3"], color="#58a6ff", linewidth=1.1, label="Stoch %K")
            ax5.plot(dates, plot_df["STOCHd_14_3_3"], color="#ffa657", linewidth=0.9, label="Stoch %D")
            ax5.axhline(y=80, color="#f85149", linewidth=0.6, linestyle="--", alpha=0.6)
            ax5.axhline(y=20, color="#3fb950", linewidth=0.6, linestyle="--", alpha=0.6)
            ax5.fill_between(dates, 20, 80, color="#30363d", alpha=0.10)
            ax5.set_ylabel("Stoch %", color="#58a6ff", fontsize=7)
            ax5.set_ylim(0, 100)
            ax5.legend(loc="upper left", fontsize=6, framealpha=0.25, facecolor="#161b22", ncol=2, edgecolor="#30363d")

            # ADX overlay on twin axis
            if has_adx:
                ax5_adx = ax5.twinx()
                ax5_adx.plot(dates, plot_df["ADX_14"], color="#f778ba", linewidth=1.1, alpha=0.85, linestyle="-", label="ADX(14)")
                ax5_adx.axhline(y=25, color="#f778ba", linewidth=0.5, linestyle=":", alpha=0.45)
                ax5_adx.set_ylabel("ADX", color="#f778ba", fontsize=7)
                ax5_adx.tick_params(colors="#f778ba", labelsize=6)
                ax5_adx.set_ylim(0, 100)
                ax5_adx.legend(loc="upper right", fontsize=6, framealpha=0.25, facecolor="#161b22", edgecolor="#30363d")
        elif has_adx:
            # Fallback: just ADX
            ax5.plot(dates, plot_df["ADX_14"], color="#f778ba", linewidth=1.2, label="ADX(14)")
            ax5.axhline(y=25, color="#f778ba", linewidth=0.5, linestyle=":", alpha=0.5)
            ax5.set_ylabel("ADX", color="#8b949e", fontsize=7)
            ax5.set_ylim(0, 100)
            ax5.legend(loc="upper left", fontsize=6, framealpha=0.25, facecolor="#161b22", edgecolor="#30363d")

        ax5.grid(color="#30363d", linewidth=0.4, alpha=0.5)
        ax5.tick_params(colors="#8b949e", labelsize=7)
        ax5.set_facecolor("#0d1117")

        # ==============================================
        # X-AXIS Formatting (only bottom panel)
        # ==============================================
        ax5.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax5.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax5.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=7)

        # ==============================================
        # Title & Save
        # ==============================================
        fig.suptitle(
            f"{symbol} — Multi-Indicator Technical Chart  |  {_fmt_chart_price(current_price)}",
            color="#e6edf3", fontsize=14, fontweight="bold", y=0.985,
        )
        # Info bar: S/R summary below title
        info_parts = [_fmt_chart_price(current_price)]
        if support_prices:
            info_parts.append("S: " + " / ".join(_fmt_chart_price(p) for p in support_prices))
        if resistance_prices:
            info_parts.append("R: " + " / ".join(_fmt_chart_price(p) for p in resistance_prices))
        if len(info_parts) > 1:  # only show if there's S/R info
            fig.text(0.5, 0.96, "  |  ".join(info_parts),
                     color="#e6edf3", fontsize=9, ha="center", va="top",
                     fontweight="normal", alpha=0.9)
        plt.tight_layout(rect=[0, 0, 1, 0.95])

        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(
            output_dir,
            f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
        )
        plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)

        return path
    except Exception as e:
        print(f"  ⚠️  차트 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return None
