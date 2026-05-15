from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Any
from urllib.request import Request, urlopen
from datetime import datetime, date, timezone

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "nearest_event_fights.csv"
OUTPUT_PATH = ROOT / "data" / "nearest_event_predictions.json"
MIN_UFC_CAGE_TIME_MINUTES = 30.0

import sys

API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from predict import predict_fight  # noqa: E402

SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from track_predictions import log_prediction  # noqa: E402
    TRACKING_ENABLED = True
except ImportError:
    TRACKING_ENABLED = False

try:
    from track_predictions import log_prediction  # noqa: E402
    TRACKING_ENABLED = True
except ImportError:
    TRACKING_ENABLED = False


def fetch_html(url: str) -> str:
    if not url:
        return ""
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        },
    )
    with urlopen(request, timeout=20) as response:  # nosec B310 - trusted UFC stats URL
        return response.read().decode("utf-8", errors="ignore")


def parse_reach_cm(text: str) -> float:
    match = re.search(r"Reach:\s*(\d+(?:\.\d+)?)\s*\"", text)
    if not match:
        return 0.0
    return float(match.group(1)) * 2.54


def parse_dob(text: str) -> date | None:
    match = re.search(r"DOB:\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", text, flags=re.IGNORECASE)
    if not match:
        return None

    value = match.group(1)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_float_stat(text: str, label: str) -> float:
    pattern = rf"{re.escape(label)}\s*:\s*(\d+(?:\.\d+)?)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


def parse_percent_stat(text: str, label: str) -> float:
    pattern = rf"{re.escape(label)}\s*:\s*(\d+(?:\.\d+)?)%"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


def parse_stance(text: str) -> str:
    match = re.search(
        r"STANCE:\s*([A-Za-z\-\s]+?)\s+(?:DOB:|SLpM:|SApM:|TD\s+Avg\.:|Sub\.\s+Avg\.:|Str\.\s+Def:|TD\s+Def\.:|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _parse_mmss_to_seconds(value: str) -> float:
    text = (value or "").strip()
    if not text or text == "---" or text == "--":
        return 0.0
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return 0.0
    return float(int(match.group(1)) * 60 + int(match.group(2)))


def _extract_two_values(column_html: str) -> tuple[float, float] | None:
    cleaned = re.sub(r"<[^>]+>", " ", column_html)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    values = re.findall(r"-?\d+(?:\.\d+)?", cleaned)
    if len(values) < 2:
        return None
    return float(values[0]), float(values[1])


def _extract_cell_values(cell_html: str) -> list[str]:
    items = re.findall(r"<p[^>]*>([\s\S]*?)</p>", cell_html)
    if not items:
        cleaned = re.sub(r"<[^>]+>", " ", cell_html)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return [cleaned] if cleaned else []

    values: list[str] = []
    for item in items:
        cleaned = re.sub(r"<[^>]+>", " ", item)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        values.append(cleaned)
    return values


def _parse_of_value(value: str) -> tuple[float, float]:
    match = re.search(r"(\d+)\s*of\s*(\d+)", value, flags=re.IGNORECASE)
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2))


def parse_fight_detail_stats(fight_url: str, cache: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    cache_key = f"fight_detail_stats:{fight_url}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        html = fetch_html(fight_url)
    except Exception:
        cache[cache_key] = {}
        return {}

    fighter_urls = re.findall(
        r"<a[^>]*class=['\"][^'\"]*b-fight-details__person-link[^'\"]*['\"][^>]*href=['\"]?(http://ufcstats.com/fighter-details/[a-z0-9]+)['\"]?",
        html,
        flags=re.IGNORECASE,
    )
    if len(fighter_urls) < 2:
        cache[cache_key] = {}
        return {}

    row_pattern = r'<tr[^>]*class="b-fight-details__table-row[^\"]*"[\s\S]*?</tr>'
    td_pattern = r'<td class="b-fight-details__table-col[^\"]*">([\s\S]*?)</td>'
    stat_columns: list[str] = []
    for row_html in re.findall(row_pattern, html):
        columns = re.findall(td_pattern, row_html)
        if len(columns) >= 10:
            stat_columns = columns
            break

    if not stat_columns:
        cache[cache_key] = {}
        return {}

    sig_vals = _extract_cell_values(stat_columns[2])
    kd_vals = _extract_cell_values(stat_columns[1])
    td_vals = _extract_cell_values(stat_columns[5])
    sub_vals = _extract_cell_values(stat_columns[7])
    ctrl_vals = _extract_cell_values(stat_columns[9])

    stats_map: Dict[str, Dict[str, float]] = {}
    for idx in range(2):
        sig_text = sig_vals[idx] if idx < len(sig_vals) else ""
        kd_text = kd_vals[idx] if idx < len(kd_vals) else ""
        td_text = td_vals[idx] if idx < len(td_vals) else ""
        sub_text = sub_vals[idx] if idx < len(sub_vals) else ""
        ctrl_text = ctrl_vals[idx] if idx < len(ctrl_vals) else ""

        sig_landed, sig_attempted = _parse_of_value(sig_text)
        kd_match = re.search(r"(\d+)", kd_text)
        kd_landed = float(kd_match.group(1)) if kd_match else 0.0
        td_landed, td_attempted = _parse_of_value(td_text)
        sub_match = re.search(r"(\d+)", sub_text)
        sub_attempts = float(sub_match.group(1)) if sub_match else 0.0
        ctrl_seconds = _parse_mmss_to_seconds(ctrl_text)

        stats_map[fighter_urls[idx]] = {
            "sig_landed": sig_landed,
            "sig_attempted": sig_attempted,
            "kd_landed": kd_landed,
            "td_landed": td_landed,
            "td_attempted": td_attempted,
            "sub_attempts": sub_attempts,
            "ctrl_seconds": ctrl_seconds,
        }

    cache[cache_key] = stats_map
    return stats_map


def parse_ufc_fight_history(html: str, fighter_url: str, cache: Dict[str, Any]) -> Dict[str, float]:
    wins = 0
    losses = 0
    draws = 0
    sub_wins = 0
    decision_wins = 0
    ko_tko_wins = 0
    sub_losses = 0
    ko_tko_losses = 0
    decision_losses = 0
    times_finished = 0
    wins_by_finish = 0
    finish_wins_round1 = 0
    finish_wins_late = 0
    total_minutes = 0.0
    sig_strikes_landed = 0.0
    sig_strikes_attempted = 0.0
    sig_strikes_absorbed = 0.0
    sig_strikes_defended_against = 0.0
    knockdowns_landed = 0.0
    knockdowns_absorbed = 0.0
    takedowns_landed = 0.0
    takedowns_attempted = 0.0
    takedowns_allowed = 0.0
    takedowns_defended_against = 0.0
    submissions_attempted = 0.0
    top_control_seconds = 0.0
    row_pattern = r'<tr[^>]*class="b-fight-details__table-row[^\"]*"[\s\S]*?</tr>'
    td_pattern = r'<td class="b-fight-details__table-col[^\"]*">([\s\S]*?)</td>'

    for row_html in re.findall(row_pattern, html):
        if "http://ufcstats.com/fight-details/" not in row_html:
            continue
        if "b-link b-link_style_black" not in row_html:
            continue

        fight_url_match = re.search(r"http://ufcstats.com/fight-details/[a-z0-9]+", row_html)
        if not fight_url_match:
            continue
        fight_url = fight_url_match.group(0)

        columns = re.findall(td_pattern, row_html)
        if len(columns) < 10:
            continue

        fighter_links = re.findall(r'href="(http://ufcstats.com/fighter-details/[a-z0-9]+)"', columns[1])
        if len(fighter_links) < 2:
            continue
        if fighter_links[0] == fighter_url:
            fighter_index = 0
        elif fighter_links[1] == fighter_url:
            fighter_index = 1
        else:
            continue
        opponent_index = 1 - fighter_index

        result_text = re.sub(r"<[^>]+>", " ", columns[0])
        result_text = re.sub(r"\s+", " ", result_text).strip().lower()
        if "win" in result_text:
            wins += 1
        elif "loss" in result_text:
            losses += 1
        elif "draw" in result_text:
            draws += 1

        method_text = re.sub(r"<[^>]+>", " ", columns[7])
        method_text = re.sub(r"\s+", " ", method_text).strip().lower()
        if "win" in result_text:
            if "submission" in method_text:
                sub_wins += 1
                wins_by_finish += 1
            elif "decision" in method_text:
                decision_wins += 1
            elif "ko/tko" in method_text or "tko" in method_text or "ko" in method_text:
                ko_tko_wins += 1
                wins_by_finish += 1
        elif "loss" in result_text:
            if "submission" in method_text:
                sub_losses += 1
                times_finished += 1
            elif "ko/tko" in method_text or "tko" in method_text or "ko" in method_text:
                ko_tko_losses += 1
                times_finished += 1
            elif "decision" in method_text:
                decision_losses += 1

        round_text = re.sub(r"<[^>]+>", " ", columns[8])
        round_text = re.sub(r"\s+", " ", round_text).strip()
        time_text = re.sub(r"<[^>]+>", " ", columns[9])
        time_text = re.sub(r"\s+", " ", time_text).strip()

        round_match = re.search(r"(\d+)", round_text)
        time_match = re.search(r"(\d{1,2}):(\d{2})", time_text)
        if not round_match or not time_match:
            continue

        round_num = int(round_match.group(1))
        minute_part = int(time_match.group(1))
        second_part = int(time_match.group(2))
        elapsed_minutes = max(0, round_num - 1) * 5.0
        elapsed_minutes += minute_part + (second_part / 60.0)
        total_minutes += elapsed_minutes

        if "win" in result_text and ("submission" in method_text or "ko/tko" in method_text or "tko" in method_text or "ko" in method_text):
            if round_num == 1:
                finish_wins_round1 += 1
            elif round_num >= 3:
                finish_wins_late += 1

        detail_stats = parse_fight_detail_stats(fight_url, cache)
        fighter_stats = detail_stats.get(fighter_url)
        opponent_url = fighter_links[opponent_index]
        opponent_stats = detail_stats.get(opponent_url)
        if not fighter_stats or not opponent_stats:
            continue

        sig_strikes_landed += float(fighter_stats.get("sig_landed", 0.0) or 0.0)
        sig_strikes_attempted += float(fighter_stats.get("sig_attempted", 0.0) or 0.0)
        knockdowns_landed += float(fighter_stats.get("kd_landed", 0.0) or 0.0)
        takedowns_landed += float(fighter_stats.get("td_landed", 0.0) or 0.0)
        takedowns_attempted += float(fighter_stats.get("td_attempted", 0.0) or 0.0)
        submissions_attempted += float(fighter_stats.get("sub_attempts", 0.0) or 0.0)
        top_control_seconds += float(fighter_stats.get("ctrl_seconds", 0.0) or 0.0)

        sig_strikes_absorbed += float(opponent_stats.get("sig_landed", 0.0) or 0.0)
        sig_strikes_defended_against += float(opponent_stats.get("sig_attempted", 0.0) or 0.0)
        knockdowns_absorbed += float(opponent_stats.get("kd_landed", 0.0) or 0.0)
        takedowns_allowed += float(opponent_stats.get("td_landed", 0.0) or 0.0)
        takedowns_defended_against += float(opponent_stats.get("td_attempted", 0.0) or 0.0)

    slpm = (sig_strikes_landed / total_minutes) if total_minutes > 0 else 0.0
    sapm = (sig_strikes_absorbed / total_minutes) if total_minutes > 0 else 0.0
    td_avg = ((takedowns_landed * 15.0) / total_minutes) if total_minutes > 0 else 0.0
    sub_avg = ((submissions_attempted * 15.0) / total_minutes) if total_minutes > 0 else 0.0
    knockdowns_per_15 = ((knockdowns_landed * 15.0) / total_minutes) if total_minutes > 0 else 0.0
    knockdowns_absorbed_per_15 = ((knockdowns_absorbed * 15.0) / total_minutes) if total_minutes > 0 else 0.0
    control_minutes_per_15 = ((top_control_seconds / 60.0) * 15.0 / total_minutes) if total_minutes > 0 else 0.0
    top_control_minutes_per_td = (top_control_seconds / 60.0) / max(1.0, takedowns_landed)
    ground_time_ratio = top_control_seconds / max(1.0, total_minutes * 60.0)
    str_def = 0.0
    if sig_strikes_defended_against > 0:
        str_def = (1.0 - (sig_strikes_absorbed / sig_strikes_defended_against)) * 100.0
    td_def = 0.0
    if takedowns_defended_against > 0:
        td_def = (1.0 - (takedowns_allowed / takedowns_defended_against)) * 100.0
    str_def = max(0.0, min(100.0, str_def))
    td_def = max(0.0, min(100.0, td_def))

    win_total = max(1, wins)
    loss_total = max(1, losses)
    sub_win_rate = float(sub_wins) / float(win_total) if wins > 0 else 0.0
    decision_win_rate = float(decision_wins) / float(win_total) if wins > 0 else 0.0
    ko_tko_win_rate = float(ko_tko_wins) / float(win_total) if wins > 0 else 0.0
    sub_loss_rate = float(sub_losses) / float(loss_total) if losses > 0 else 0.0
    ko_tko_loss_rate = float(ko_tko_losses) / float(loss_total) if losses > 0 else 0.0
    decision_loss_rate = float(decision_losses) / float(loss_total) if losses > 0 else 0.0
    finish_wins_round1_rate = float(finish_wins_round1) / float(max(1, wins_by_finish)) if wins_by_finish > 0 else 0.0
    late_finish_wins_rate = float(finish_wins_late) / float(max(1, wins_by_finish)) if wins_by_finish > 0 else 0.0
    avg_fight_minutes = float(total_minutes) / float(max(1, wins + losses + draws)) if (wins + losses + draws) > 0 else 0.0
    pace_score = ((slpm * 0.6) + (td_avg * 0.9)) / max(1.0, avg_fight_minutes)
    cardio_risk = max(0.0, (pace_score - 1.05))
    sample_confidence = min(1.0, float(total_minutes) / 120.0)

    return {
        "wins": float(wins),
        "losses": float(losses),
        "draws": float(draws),
        "ufc_cage_time_minutes": float(total_minutes),
        "slpm": float(slpm),
        "sapm": float(sapm),
        "td_avg": float(td_avg),
        "sub_avg": float(sub_avg),
        "knockdowns_per_15": float(knockdowns_per_15),
        "knockdowns_absorbed_per_15": float(knockdowns_absorbed_per_15),
        "control_minutes_per_15": float(control_minutes_per_15),
        "top_control_minutes_per_td": float(top_control_minutes_per_td),
        "ground_time_ratio": float(ground_time_ratio),
        "str_def": float(str_def),
        "td_def": float(td_def),
        "sub_win_rate": float(sub_win_rate),
        "decision_win_rate": float(decision_win_rate),
        "ko_tko_win_rate": float(ko_tko_win_rate),
        "ko_tko_wins": float(ko_tko_wins),
        "wins_by_finish": float(wins_by_finish),
        "sub_loss_rate": float(sub_loss_rate),
        "ko_tko_loss_rate": float(ko_tko_loss_rate),
        "decision_loss_rate": float(decision_loss_rate),
        "times_finished": float(times_finished),
        "finish_wins_round1_rate": float(finish_wins_round1_rate),
        "late_finish_wins_rate": float(late_finish_wins_rate),
        "avg_fight_minutes": float(avg_fight_minutes),
        "pace_score": float(pace_score),
        "cardio_risk": float(cardio_risk),
        "sample_confidence": float(sample_confidence),
    }


def extract_recent_opponent_urls(html: str, fighter_url: str, limit: int = 5) -> list[str]:
    links = re.findall(r'href="(http://ufcstats.com/fighter-details/[a-z0-9]+)"', html)
    seen: set[str] = set()
    opponents: list[str] = []
    for link in links:
        if link == fighter_url:
            continue
        if link in seen:
            continue
        seen.add(link)
        opponents.append(link)
        if len(opponents) >= limit:
            break
    return opponents


def compute_sos_score(opponent_profiles: list[Dict[str, Any]]) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for index, opponent in enumerate(opponent_profiles):
        wins = int(opponent.get("wins", 0) or 0)
        losses = int(opponent.get("losses", 0) or 0)
        draws = int(opponent.get("draws", 0) or 0)
        total = wins + losses + draws
        if total == 0:
            continue
        win_rate = wins / total
        recency_weight = float(len(opponent_profiles) - index)
        experience_weight = min(2.0, 1.0 + math.log1p(total) / 4.0)
        weight = recency_weight * experience_weight
        weighted_sum += win_rate * weight
        weight_total += weight

    if weight_total <= 0:
        return 0.5
    return weighted_sum / weight_total


def stabilize_rate(rate_value: float, minutes: float, prior_rate: float, prior_minutes: float) -> float:
    total_minutes = max(0.0, float(minutes))
    return ((rate_value * total_minutes) + (prior_rate * prior_minutes)) / max(1.0, total_minutes + prior_minutes)


def parse_fighter_profile(url: str, cache: Dict[str, Any], include_sos: bool = True) -> Dict[str, Any]:
    if not url:
        return {}
    cache_key = url if include_sos else f"{url}#nosos"
    if cache_key in cache:
        return cache[cache_key]
    if include_sos and url in cache:
        return cache[url]

    try:
        html = fetch_html(url)
    except Exception:
        cache[cache_key] = {}
        if include_sos:
            cache[url] = {}
        return cache[cache_key]

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()

    ufc_history = parse_ufc_fight_history(html, url, cache)
    wins = int(ufc_history.get("wins", 0.0) or 0.0)
    losses = int(ufc_history.get("losses", 0.0) or 0.0)
    draws = int(ufc_history.get("draws", 0.0) or 0.0)
    reach_cm = parse_reach_cm(text)
    dob = parse_dob(text)

    profile = {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "reach_cm": reach_cm,
        "ufc_cage_time_minutes": float(ufc_history.get("ufc_cage_time_minutes", 0.0) or 0.0),
        "dob": dob,
        "raw_slpm": float(ufc_history.get("slpm", 0.0) or 0.0),
        "raw_td_avg": float(ufc_history.get("td_avg", 0.0) or 0.0),
        "raw_sub_avg": float(ufc_history.get("sub_avg", 0.0) or 0.0),
        "raw_kd_per15": float(ufc_history.get("knockdowns_per_15", 0.0) or 0.0),
        "slpm": float(ufc_history.get("slpm", 0.0) or 0.0),
        "sapm": float(ufc_history.get("sapm", 0.0) or 0.0),
        "td_avg": float(ufc_history.get("td_avg", 0.0) or 0.0),
        "sub_avg": float(ufc_history.get("sub_avg", 0.0) or 0.0),
        "knockdowns_per_15": float(ufc_history.get("knockdowns_per_15", 0.0) or 0.0),
        "knockdowns_absorbed_per_15": float(ufc_history.get("knockdowns_absorbed_per_15", 0.0) or 0.0),
        "control_minutes_per_15": float(ufc_history.get("control_minutes_per_15", 0.0) or 0.0),
        "top_control_minutes_per_td": float(ufc_history.get("top_control_minutes_per_td", 0.0) or 0.0),
        "ground_time_ratio": float(ufc_history.get("ground_time_ratio", 0.0) or 0.0),
        "str_acc": parse_percent_stat(text, "Str. Acc."),
        "str_def": float(ufc_history.get("str_def", 0.0) or 0.0),
        "td_def": float(ufc_history.get("td_def", 0.0) or 0.0),
        "stance": parse_stance(text),
        "sub_win_rate": float(ufc_history.get("sub_win_rate", 0.0) or 0.0),
        "decision_win_rate": float(ufc_history.get("decision_win_rate", 0.0) or 0.0),
        "ko_tko_win_rate": float(ufc_history.get("ko_tko_win_rate", 0.0) or 0.0),
        "ko_tko_wins": float(ufc_history.get("ko_tko_wins", 0.0) or 0.0),
        "wins_by_finish": float(ufc_history.get("wins_by_finish", 0.0) or 0.0),
        "sub_loss_rate": float(ufc_history.get("sub_loss_rate", 0.0) or 0.0),
        "ko_tko_loss_rate": float(ufc_history.get("ko_tko_loss_rate", 0.0) or 0.0),
        "decision_loss_rate": float(ufc_history.get("decision_loss_rate", 0.0) or 0.0),
        "times_finished": float(ufc_history.get("times_finished", 0.0) or 0.0),
        "finish_wins_round1_rate": float(ufc_history.get("finish_wins_round1_rate", 0.0) or 0.0),
        "late_finish_wins_rate": float(ufc_history.get("late_finish_wins_rate", 0.0) or 0.0),
        "avg_fight_minutes": float(ufc_history.get("avg_fight_minutes", 0.0) or 0.0),
        "pace_score": float(ufc_history.get("pace_score", 0.0) or 0.0),
        "cardio_risk": float(ufc_history.get("cardio_risk", 0.0) or 0.0),
        "sample_confidence": float(ufc_history.get("sample_confidence", 0.0) or 0.0),
        "stabilized_slpm": 0.0,
        "stabilized_td_avg": 0.0,
        "stabilized_sub_avg": 0.0,
        "stabilized_kd_per15": 0.0,
        "opp_quality_factor": 1.0,
        "adjusted_slpm": 0.0,
        "adjusted_td_avg": 0.0,
        "adjusted_sub_avg": 0.0,
        "adjusted_kd_per15": 0.0,
        "sos_score": 0.5,
    }

    if include_sos:
        opponent_urls = extract_recent_opponent_urls(html, url, limit=5)
        opponent_profiles = [
            parse_fighter_profile(opponent_url, cache, include_sos=False)
            for opponent_url in opponent_urls
        ]
        opponent_profiles = [opponent for opponent in opponent_profiles if opponent]
        profile["sos_score"] = compute_sos_score(opponent_profiles)

    minutes = float(profile.get("ufc_cage_time_minutes", 0.0) or 0.0)
    sos_score = float(profile.get("sos_score", 0.5) or 0.5)
    opp_quality_factor = 0.85 + (sos_score * 0.30)

    slpm_stable = stabilize_rate(float(profile.get("raw_slpm", 0.0) or 0.0), minutes, prior_rate=3.25, prior_minutes=90.0)
    td_avg_stable = stabilize_rate(float(profile.get("raw_td_avg", 0.0) or 0.0), minutes, prior_rate=1.55, prior_minutes=90.0)
    sub_avg_stable = stabilize_rate(float(profile.get("raw_sub_avg", 0.0) or 0.0), minutes, prior_rate=0.38, prior_minutes=90.0)
    kd_per15_stable = stabilize_rate(float(profile.get("raw_kd_per15", 0.0) or 0.0), minutes, prior_rate=0.20, prior_minutes=90.0)

    profile["stabilized_slpm"] = max(0.0, slpm_stable)
    profile["stabilized_td_avg"] = max(0.0, td_avg_stable)
    profile["stabilized_sub_avg"] = max(0.0, sub_avg_stable)
    profile["stabilized_kd_per15"] = max(0.0, kd_per15_stable)
    profile["opp_quality_factor"] = opp_quality_factor

    profile["adjusted_slpm"] = max(0.0, slpm_stable * opp_quality_factor)
    profile["adjusted_td_avg"] = max(0.0, td_avg_stable * opp_quality_factor)
    profile["adjusted_sub_avg"] = max(0.0, sub_avg_stable * opp_quality_factor)
    profile["adjusted_kd_per15"] = max(0.0, kd_per15_stable * opp_quality_factor)

    profile["slpm"] = profile["adjusted_slpm"]
    profile["td_avg"] = profile["adjusted_td_avg"]
    profile["sub_avg"] = profile["adjusted_sub_avg"]
    profile["knockdowns_per_15"] = profile["adjusted_kd_per15"]

    cache[cache_key] = profile
    if include_sos:
        cache[url] = profile
    return profile


def is_heavy_division(weight_class_name: str) -> bool:
    lowered = (weight_class_name or "").lower()
    return "heavyweight" in lowered


def is_wmma_division(weight_class_name: str) -> bool:
    lowered = (weight_class_name or "").lower()
    return "women" in lowered


def fighter_base_score(profile: Dict[str, Any]) -> float:
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    win_rate = (wins / total) if total else 0.5
    experience_bonus = math.log1p(total) / 14.0
    sos_score = float(profile.get("sos_score", 0.5) or 0.5)
    sos_bonus = (sos_score - 0.5) * 0.45
    return (win_rate - 0.5) + experience_bonus + sos_bonus


def fighter_age(profile: Dict[str, Any]) -> float | None:
    dob = profile.get("dob")
    if not isinstance(dob, date):
        return None
    return (date.today() - dob).days / 365.25


def profile_metrics(profile: Dict[str, Any]) -> Dict[str, float]:
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    win_rate = (wins / total) if total else 0.5
    reach_cm = float(profile.get("reach_cm", 0.0) or 0.0)
    age_years = fighter_age(profile)
    return {
        "wins": float(wins),
        "losses": float(losses),
        "draws": float(draws),
        "total_fights": float(total),
        "win_rate": float(win_rate),
        "reach_cm": float(reach_cm),
        "ufc_cage_time_minutes": float(profile.get("ufc_cage_time_minutes", 0.0) or 0.0),
        "age_years": float(age_years) if age_years is not None else float("nan"),
        "sos_score": float(profile.get("sos_score", 0.5) or 0.5),
    }


def compute_uncertainty_factor(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> float:
    wins_a = int(profile_a.get("wins", 0) or 0)
    losses_a = int(profile_a.get("losses", 0) or 0)
    draws_a = int(profile_a.get("draws", 0) or 0)
    total_a = wins_a + losses_a + draws_a

    wins_b = int(profile_b.get("wins", 0) or 0)
    losses_b = int(profile_b.get("losses", 0) or 0)
    draws_b = int(profile_b.get("draws", 0) or 0)
    total_b = wins_b + losses_b + draws_b

    avg_total = (total_a + total_b) / 2.0
    volume_factor = min(1.0, avg_total / 12.0)
    low_exp_guard = min(1.0, max(0.0, float(min(total_a, total_b))) / 6.0)

    stat_keys = ["slpm", "sapm", "td_avg", "sub_avg", "str_def", "td_def", "reach_cm"]
    present_a = sum(1 for key in stat_keys if float(profile_a.get(key, 0.0) or 0.0) > 0.0)
    present_b = sum(1 for key in stat_keys if float(profile_b.get(key, 0.0) or 0.0) > 0.0)
    completeness = (present_a + present_b) / float(len(stat_keys) * 2)
    sample_conf_a = float(profile_a.get("sample_confidence", 0.0) or 0.0)
    sample_conf_b = float(profile_b.get("sample_confidence", 0.0) or 0.0)
    sample_confidence = (sample_conf_a + sample_conf_b) / 2.0
    cardio_risk_a = float(profile_a.get("cardio_risk", 0.0) or 0.0)
    cardio_risk_b = float(profile_b.get("cardio_risk", 0.0) or 0.0)
    avg_cardio_risk = (cardio_risk_a + cardio_risk_b) / 2.0

    factor = (volume_factor * 0.45) + (low_exp_guard * 0.20) + (completeness * 0.15) + (sample_confidence * 0.20)
    if is_wmma_division(weight_class_name):
        factor *= 0.92
    if is_heavy_division(weight_class_name):
        factor *= 0.90
    elif (weight_class_name or "").strip() and ("flyweight" in weight_class_name.lower() or "bantamweight" in weight_class_name.lower()):
        factor *= 1.03

    factor *= max(0.86, 1.0 - (avg_cardio_risk * 0.10))
    return max(0.45, min(1.0, factor))


def build_model_feature_payload(
    fighter_a: str,
    fighter_b: str,
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
) -> Dict[str, Any]:
    wins_a = int(profile_a.get("wins", 0) or 0)
    losses_a = int(profile_a.get("losses", 0) or 0)
    draws_a = int(profile_a.get("draws", 0) or 0)
    total_a = wins_a + losses_a + draws_a

    wins_b = int(profile_b.get("wins", 0) or 0)
    losses_b = int(profile_b.get("losses", 0) or 0)
    draws_b = int(profile_b.get("draws", 0) or 0)
    total_b = wins_b + losses_b + draws_b

    slpm_a = float(profile_a.get("slpm", 0.0) or 0.0)
    slpm_b = float(profile_b.get("slpm", 0.0) or 0.0)
    sapm_a = float(profile_a.get("sapm", 0.0) or 0.0)
    sapm_b = float(profile_b.get("sapm", 0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)

    td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
    td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
    td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
    td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)

    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)

    strike_signal_a = (slpm_a - (sapm_a * 0.5)) + (str_def_a / 100.0)
    strike_signal_b = (slpm_b - (sapm_b * 0.5)) + (str_def_b / 100.0)

    takedown_signal_a = td_avg_a + (td_def_a / 100.0)
    takedown_signal_b = td_avg_b + (td_def_b / 100.0)

    age_a = fighter_age(profile_a)
    age_b = fighter_age(profile_b)

    finish_rate_proxy_a = (sub_avg_a * 0.55) + (max(0.0, slpm_a - 2.0) * 0.08)
    finish_rate_proxy_b = (sub_avg_b * 0.55) + (max(0.0, slpm_b - 2.0) * 0.08)

    return {
        "fighterA": fighter_a,
        "fighterB": fighter_b,
        "strike_diff": float(strike_signal_a - strike_signal_b),
        "takedown_diff": float(takedown_signal_a - takedown_signal_b),
        "reach_diff": float(float(profile_a.get("reach_cm", 0.0) or 0.0) - float(profile_b.get("reach_cm", 0.0) or 0.0)),
        "win_streak_diff": 0.0,
        "age_diff": float((age_a or 0.0) - (age_b or 0.0)),
        "experience_diff": float(total_a - total_b),
        "finish_rate_diff": float(finish_rate_proxy_a - finish_rate_proxy_b),
    }


def compute_power_score(profile: Dict[str, Any]) -> float:
    """
    Compute striking POWER score (damage creation ability).
    Weights: KO%, SLpM pressure, accuracy.
    Separates from finisher_score (damage conversion).
    Examples: Derrick Lewis (high), Merab (low).
    """
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    
    if total == 0:
        return 0.0
    
    str_acc = float(profile.get("str_acc", 0.0) or 0.0)
    kd_per15 = float(profile.get("knockdowns_per_15", 0.0) or 0.0)

    ko_win_rate = 0.0
    if wins > 0:
        ko_wins = int(profile.get("ko_tko_wins", 0) or 0)
        ko_win_rate = min(1.0, ko_wins / wins)

    power_components = [
        min(1.0, kd_per15 / 1.0) * 0.55,
        ko_win_rate * 0.35,
        min(1.0, str_acc / 55.0) * 0.10,
    ]
    
    power_score = sum(power_components)
    return min(1.0, power_score)


def compute_finisher_score(profile: Dict[str, Any]) -> float:
    """
    Compute striking FINISHING ability (damage conversion).
    High finisher = swarms after damage, grinds clinch, TKOs from volume.
    High power + low finisher = big punchers who exhaust themselves (e.g., Lewis).
    High finisher + low power = pressure fighters (e.g., Holloway).
    Examples: Max Holloway (high), Francis Ngannou (high power, variable finishing).
    """
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    
    if total == 0:
        return 0.0
    
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    td_avg = float(profile.get("td_avg", 0.0) or 0.0)
    early_finish_rate = float(profile.get("finish_wins_round1_rate", 0.0) or 0.0)
    late_finish_rate = float(profile.get("late_finish_wins_rate", 0.0) or 0.0)
    control_conversion = float(profile.get("top_control_minutes_per_td", 0.0) or 0.0)

    finishing_rate = 0.0
    if wins > 0:
        finishing_wins = int(profile.get("wins_by_finish", 0) or 0)
        finishing_rate = min(1.0, finishing_wins / wins)

    pressure_pace = min(1.0, ((slpm * 0.65) + (td_avg * 0.35)) / 6.8)

    finisher_components = [
        finishing_rate * 0.38,
        min(1.0, early_finish_rate) * 0.12,
        min(1.0, late_finish_rate) * 0.25,
        pressure_pace * 0.17,
        min(1.0, control_conversion / 3.0) * 0.08,
    ]
    
    finisher_score = sum(finisher_components)
    return min(1.0, finisher_score)


def compute_round_winning_score(profile: Dict[str, Any]) -> float:
    """
    Estimate minute-winning / round-banking reliability.

    This is intentionally NOT a finishing metric. It rewards fighters who
    quietly win rounds through volume, defense, control retention, pace, and
    late-fight reliability. Output is bounded to [0.0, 1.0] so it can be used
    only as a support layer.
    """
    slpm = float(profile.get("stabilized_slpm", profile.get("slpm", 0.0)) or 0.0)
    sapm = float(profile.get("sapm", 0.0) or 0.0)
    str_def = float(profile.get("str_def", 0.0) or 0.0)
    td_def = float(profile.get("td_def", 0.0) or 0.0)
    control_minutes_per_15 = float(profile.get("control_minutes_per_15", 0.0) or 0.0)
    top_control_minutes_per_td = float(profile.get("top_control_minutes_per_td", 0.0) or 0.0)
    ground_time_ratio = float(profile.get("ground_time_ratio", 0.0) or 0.0)
    pace_score = float(profile.get("pace_score", 0.0) or 0.0)
    cardio_risk = float(profile.get("cardio_risk", 0.0) or 0.0)
    late_finish_rate = float(profile.get("late_finish_wins_rate", 0.0) or 0.0)
    avg_fight_minutes = float(profile.get("avg_fight_minutes", 0.0) or 0.0)

    strike_diff = max(-3.0, min(3.0, slpm - sapm))
    striking_component = (strike_diff + 3.0) / 6.0

    defense_component = min(1.0, max(0.0,
        (str_def / 100.0) * 0.60 + (td_def / 100.0) * 0.40
    ))

    control_component = min(1.0, max(0.0,
        (control_minutes_per_15 / 6.0) * 0.55
        + (top_control_minutes_per_td / 3.5) * 0.25
        + (ground_time_ratio / 0.45) * 0.20
    ))

    pace_component = min(1.0, max(0.0, pace_score / 0.70))

    late_reliability = min(1.0, max(0.0,
        (1.0 - min(1.0, cardio_risk)) * 0.65
        + min(1.0, late_finish_rate) * 0.20
        + min(1.0, avg_fight_minutes / 15.0) * 0.15
    ))

    round_score = (
        striking_component * 0.35
        + defense_component * 0.25
        + control_component * 0.18
        + pace_component * 0.10
        + late_reliability * 0.12
    )
    return min(1.0, max(0.0, round_score))


def apply_diminishing_returns(raw_bonus: float, compression_factor: float = 0.8) -> float:
    """
    Apply tanh-based diminishing returns to prevent bonus stacking explosions.
    Prevents: wrestler_bonus + sub_bonus + tdd_collapse + ... = 85% favorite
    Instead: compresses via tanh(raw * factor), keeping matchup realism bounded.
    
    Example: raw_bonus=1.2 -> tanh(1.2*0.8)=tanh(0.96)≈0.753 (capped realism)
    """
    if abs(raw_bonus) < 0.01:
        return 0.0
    compressed = math.tanh(raw_bonus * compression_factor)
    return compressed


def tdd_liability(td_def: float) -> float:
    """
    Nonlinear TDD vulnerability scaling.
    80%+ = essentially immune. Below 50% = catastrophic collapse zone.
    Sharper thresholds than before — regime collapse, not smooth scaling.
    """
    x = (td_def - 58.0) / 7.5
    liability = 1.0 / (1.0 + math.exp(x))
    return max(0.02, min(0.95, liability))


def compute_grappling_entry_prob(attacker: Dict[str, Any], defender: Dict[str, Any]) -> float:
    """
    Scalar [0.10, 1.0] representing how reliably the attacker can initiate
    and sustain grappling against THIS specific defender.

    Grappling is not a static attribute — it is a CONDITIONAL phase that
    can only happen if the attacker survives the striker's defensive wall
    and closes the distance.  Three penalizers:

    1. Defender striking defense   — keeps the attacker out / punishes entries.
    2. Defender reach advantage    — longer limbs make closing distance costlier.
    3. Attacker damage absorbed    — high SApM means attacker gets hurt every
                                     time they try to engage; repeated failed
                                     entries accumulate and reduce success rate.
    """
    # 1. Defender can keep fighter at bay with strikes
    str_def_def = float(defender.get("str_def", 50.0) or 50.0) / 100.0
    defense_penalty = 1.0 - str_def_def * 0.50   # 0% def → no reduction; 100% → -50%

    # 2. Defender's reach advantage over the attacker
    reach_att = float(attacker.get("reach_cm", 175.0) or 175.0)
    reach_def = float(defender.get("reach_cm", 175.0) or 175.0)
    reach_gap = max(0.0, (reach_def - reach_att)) / 20.0   # 0→1 over a 20 cm gap
    reach_penalty = 1.0 - min(0.40, reach_gap * 0.40)

    # 3. Attacker absorbs a lot of damage — gets hit repeatedly on entry
    sapm_att = float(attacker.get("sapm", 3.5) or 3.5)
    AVG_SAPM = 3.5
    damage_on_entry = max(0.0, (sapm_att - AVG_SAPM) / 7.0)   # 0 at avg, 0.5 at sapm=7.0
    damage_penalty  = 1.0 - min(0.50, damage_on_entry)

    entry_prob = defense_penalty * reach_penalty * damage_penalty
    return max(0.10, min(1.0, entry_prob))


def str_def_liability(str_def: float) -> float:
    """
    Nonlinear striking defense vulnerability scaling.
    Sharp penalty for poor striking defense (below 45%).
    Asymmetric: TDD matters more for wrestlers.
    """
    if str_def >= 65.0:
        return 0.05
    elif str_def >= 55.0:
        return 0.15
    elif str_def >= 45.0:
        return 0.30
    elif str_def >= 35.0:
        return 0.55
    else:
        return 0.85


def classify_archetype(profile: Dict[str, Any]) -> str:
    """
    Classify fighter archetype based on offensive/defensive profile.
    Categories: pressure_wrestler, submission_grappler, power_striker, technical_striker, balanced.
    """
    td_avg = float(profile.get("td_avg", 0.0) or 0.0)
    sub_avg = float(profile.get("sub_avg", 0.0) or 0.0)
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    str_def = float(profile.get("str_def", 0.0) or 0.0)
    
    power_score_val = compute_power_score(profile)
    
    is_wrestler = td_avg > 2.0
    is_grappler = sub_avg > 0.6
    is_striker = slpm > 4.0
    is_technical = str_def > 55.0
    is_power_striker = power_score_val > 0.6 and is_striker
    
    if is_wrestler and td_avg > 3.5 and power_score_val < 0.5:
        return "pressure_wrestler"
    elif is_grappler and sub_avg > 1.0 and td_avg > 2.5:
        return "submission_grappler"
    elif is_power_striker:
        return "power_striker"
    elif is_technical and str_def > 58.0:
        return "technical_striker"
    else:
        return "balanced"


def compute_control_proxy(profile: Dict[str, Any]) -> float:
    """
    Estimate control time tendency from available stats.
    Higher = more grindy, control-oriented fighter.

    Uses tanh soft saturation to prevent artificial max-out.
    Previously: linear min(1.0, ...) caused Tuco to hit 1.000 (maxed).
    Now: tanh smoothly asymptotes — extreme wrestlers get ~0.95, not 1.00.
    Formula: tanh(td_avg*0.6 + sub_avg*0.3 + wrestling_tendency*0.4)
    """
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws

    if total == 0:
        return 0.0

    td_avg = float(profile.get("td_avg", 0.0) or 0.0)
    sub_avg = float(profile.get("sub_avg", 0.0) or 0.0)
    td_def = float(profile.get("td_def", 0.0) or 0.0)

    wrestling_tendency = max(0.0, 1.0 - (td_def / 100.0))

    raw = (td_avg * 0.6) + (sub_avg * 0.3) + (wrestling_tendency * 0.4)
    return math.tanh(raw)


def compute_anti_wrestling_score(profile: Dict[str, Any]) -> float:
    """
    Estimate fighter's defensive wrestling ability — ability to avoid/escape control.
    DIRECTION: Higher = BETTER defense (harder to hold down).

    Components:
    - TD Def %: primary raw defense signal (50%)
    - SLpM: offensive output discourages control attempts (25%)
    - Sub threat off back: submission threat reduces opponent's top control safety (25%)

    Examples:
    - Oliveira (poor TDD but high sub avg + output): scores HIGH = correctly identified as hard to hold
    - Pure grappling dummy (low TDD, low output, no sub threat): scores LOW = correctly vulnerable
    """
    td_def = float(profile.get("td_def", 0.0) or 0.0)
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    sub_avg = float(profile.get("sub_avg", 0.0) or 0.0)

    td_def_normalized = min(1.0, td_def / 80.0)
    slpm_normalized = min(1.0, slpm / 6.0)
    sub_threat = min(1.0, sub_avg / 1.5)

    components = [
        td_def_normalized * 0.50,
        slpm_normalized * 0.25,
        sub_threat * 0.25,
    ]

    return min(1.0, sum(components))


def compute_wrestling_entry_factor(attacker: Dict[str, Any], defender: Dict[str, Any]) -> float:
    """
    P(attacker successfully closes distance and initiates a takedown attempt).
    Semantic: probability of a clean entry given defender's threat profile.

    Reduces when defender has:
    - High striking output (punishes bad shots)
    - Good striking defense (exchanges favor defender, discourages wrestling)
    - Reach advantage (longer frame = harder clinch access)

    Returns: float in [0.25, 0.90] — even elite defenders don't prevent all entries,
             and even bad defenders resist some.
    """
    defender_slpm = float(defender.get("slpm", 0.0) or 0.0)
    defender_str_def = float(defender.get("str_def", 0.0) or 0.0)

    attacker_reach = float(attacker.get("reach_cm", 175.0) or 175.0)
    defender_reach = float(defender.get("reach_cm", 175.0) or 175.0)
    reach_gap = max(0.0, defender_reach - attacker_reach)

    # Each factor is a named probability component
    p_striking_threat = min(1.0, defender_slpm / 6.0)       # P(defender punishes entry)
    p_defense_quality = min(1.0, defender_str_def / 65.0)   # P(exchanges hurt shooter)
    p_reach_penalty = min(0.30, reach_gap / 70.0)           # P(reach gap blocks clinch)

    # Union of defender's entry-prevention mechanisms
    p_entry_blocked = 1.0 - (1.0 - p_striking_threat * 0.45) * (1.0 - p_defense_quality * 0.35) * (1.0 - p_reach_penalty * 0.20)

    # P(entry succeeds) = 1 - P(entry blocked), floored at 0.25
    return max(0.25, 1.0 - p_entry_blocked * 0.6)


def compute_effective_pressure(attacker: Dict[str, Any], defender: Dict[str, Any]) -> float:
    td_avg_attacker = float(attacker.get("td_avg", 0.0) or 0.0)
    td_def_defender = float(defender.get("td_def", 0.0) or 0.0)
    anti_wrestling_defender = compute_anti_wrestling_score(defender)

    p_chain = min(1.0, td_avg_attacker / 4.5)
    p_entry = compute_wrestling_entry_factor(attacker, defender)
    p_control = tdd_liability(td_def_defender) * (1.0 - anti_wrestling_defender * 0.45)
    p_initiate = 1.0 - (1.0 - p_chain) * (1.0 - p_entry * 0.15)

    return max(0.05, 1.0 - (1.0 - p_initiate) * (1.0 - p_control * p_chain))


def compute_regime_scores(profile_a: Dict[str, Any], profile_b: Dict[str, Any]) -> Dict[str, float]:
    td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
    td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
    td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
    td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)
    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)

    wrestle_a = (td_avg_a / 3.5) * tdd_liability(td_def_b)
    wrestle_b = (td_avg_b / 3.5) * tdd_liability(td_def_a)
    sub_a = sub_avg_a * tdd_liability(td_def_b)
    sub_b = sub_avg_b * tdd_liability(td_def_a)
    power_a = compute_power_score(profile_a)
    power_b = compute_power_score(profile_b)
    strike_a = power_a * str_def_liability(str_def_b)
    strike_b = power_b * str_def_liability(str_def_a)

    wrestling_score = wrestle_a - wrestle_b
    submission_score = sub_a - sub_b
    striking_score = strike_a - strike_b
    contested_score = max(0.0, 1.0 - max(abs(wrestling_score), abs(submission_score), abs(striking_score)))

    return {
        "wrestle_a": wrestle_a,
        "wrestle_b": wrestle_b,
        "sub_a": sub_a,
        "sub_b": sub_b,
        "strike_a": strike_a,
        "strike_b": strike_b,
        "wrestling_regime_score": wrestling_score,
        "submission_regime_score": submission_score,
        "striking_regime_score": striking_score,
        "contested_score": contested_score,
    }


def detect_fragility_flags(profile: Dict[str, Any], opponent_profile: Dict[str, Any] | None = None) -> tuple[bool, float]:
    """
    Detect brittle fighters or dangerous disparities.
    Returns: (has_fragility, uncertainty_multiplier)
    
    Fragility indicators:
    - Absorbs high incoming strikes (SApM > 5.5) + low StrDef
    - Recently suffered KOs (inferred from late losses)
    - Age 35+ in lower weight classes
    - History suggests control vulnerability
    
    When fragile: expand uncertainty slightly (multiply by >1.0)
    This prevents overconfident predictions on brittle matchups.
    """
    sapm = float(profile.get("sapm", 0.0) or 0.0)
    str_def = float(profile.get("str_def", 0.0) or 0.0)
    losses = int(profile.get("losses", 0) or 0)
    dob = profile.get("dob")
    
    uncertainty_mult = 1.0
    fragile = False
    
    if sapm > 5.5 and str_def < 42.0:
        fragile = True
        uncertainty_mult *= 1.08
    
    if dob and isinstance(dob, date):
        age = (date.today() - dob).days / 365.25
        if age > 36 and losses >= 5:
            fragile = True
            uncertainty_mult *= 1.06
    
    if losses >= 4:
        fragile = True
        uncertainty_mult *= 1.04
    
    if opponent_profile:
        opp_td_avg = float(opponent_profile.get("td_avg", 0.0) or 0.0)
        own_td_def = float(profile.get("td_def", 0.0) or 0.0)
        if opp_td_avg > 3.0 and own_td_def < 50.0:
            fragile = True
            uncertainty_mult *= 1.05
    
    return fragile, uncertainty_mult


def matchup_score(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> tuple[float, float]:
    """
    Enhanced matchup scoring with Phase 1 interaction logic.
    Incorporates:
    - Nonlinear TDD/StrDef liability (exploitability)
    - Path-to-victory bonuses (wrestler vs low TDD, power striker vs low StrDef)
    - Power score integration (separates volume from power)
    - Archetype-aware weighting
    """
    slpm_a = float(profile_a.get("slpm", 0.0) or 0.0)
    slpm_b = float(profile_b.get("slpm", 0.0) or 0.0)
    sapm_a = float(profile_a.get("sapm", 0.0) or 0.0)
    sapm_b = float(profile_b.get("sapm", 0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)

    striking_edge = (
        (slpm_a - slpm_b) * 0.9
        + (sapm_b - sapm_a) * 0.5
        + ((str_def_a - str_def_b) / 100.0) * 0.7
    )

    kd_per15_a = float(profile_a.get("knockdowns_per_15", 0.0) or 0.0)
    kd_per15_b = float(profile_b.get("knockdowns_per_15", 0.0) or 0.0)
    kd_abs_a = float(profile_a.get("knockdowns_absorbed_per_15", 0.0) or 0.0)
    kd_abs_b = float(profile_b.get("knockdowns_absorbed_per_15", 0.0) or 0.0)
    knockdown_edge = ((kd_per15_a - kd_per15_b) * 0.38) + ((kd_abs_b - kd_abs_a) * 0.24)
    striking_edge += knockdown_edge

    reach_diff_cm = float(profile_a.get("reach_cm", 0.0) or 0.0) - float(profile_b.get("reach_cm", 0.0) or 0.0)
    reach_combo_bonus = (reach_diff_cm / 20.0) * max(0.0, striking_edge + 0.15)

    stance_a = str(profile_a.get("stance", "") or "").lower()
    stance_b = str(profile_b.get("stance", "") or "").lower()
    stance_adjust = 0.0
    if stance_a and stance_b and stance_a != stance_b:
        southpaw_a = "southpaw" in stance_a
        southpaw_b = "southpaw" in stance_b
        if southpaw_a != southpaw_b:
            if southpaw_a:
                stance_adjust += 0.06 + (0.02 if reach_diff_cm > 0 else 0.0)
            if southpaw_b:
                stance_adjust -= 0.06 + (0.02 if reach_diff_cm < 0 else 0.0)
    striking_edge += stance_adjust

    td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
    td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
    td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
    td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)
    
    grappling_edge = (
        (td_avg_a - td_avg_b) * 0.45
        + (sub_avg_a - sub_avg_b) * 0.55
        + ((td_def_a - td_def_b) / 100.0) * 0.25
    )

    wrestle_pressure_a = (
        max(0.0, td_avg_a - 1.2) * 0.55
        + max(0.0, sub_avg_a - 0.25) * 0.75
        + max(0.0, (55.0 - td_def_b) / 100.0) * 0.55
    )
    wrestle_pressure_b = (
        max(0.0, td_avg_b - 1.2) * 0.55
        + max(0.0, sub_avg_b - 0.25) * 0.75
        + max(0.0, (55.0 - td_def_a) / 100.0) * 0.55
    )

    grappling_edge += (wrestle_pressure_a - wrestle_pressure_b) * 0.5

    wrestle_threat_a = max(0.0, td_avg_a - 1.0) * 0.65 + max(0.0, sub_avg_a - 0.2) * 0.45
    wrestle_threat_b = max(0.0, td_avg_b - 1.0) * 0.65 + max(0.0, sub_avg_b - 0.2) * 0.45

    takedown_vulnerability_a = wrestle_threat_b * max(0.0, (60.0 - td_def_a) / 60.0) * 0.75
    takedown_vulnerability_b = wrestle_threat_a * max(0.0, (60.0 - td_def_b) / 60.0) * 0.75
    takedown_resistance_a = wrestle_threat_b * max(0.0, (td_def_a - 62.0) / 38.0) * 0.20
    takedown_resistance_b = wrestle_threat_a * max(0.0, (td_def_b - 62.0) / 38.0) * 0.20

    grappling_edge += (takedown_vulnerability_b - takedown_vulnerability_a)
    grappling_edge += (takedown_resistance_a - takedown_resistance_b)

    wrestler_style_a = max(0.0, td_avg_a - 1.35)
    wrestler_style_b = max(0.0, td_avg_b - 1.35)
    striking_vulnerability_a = wrestler_style_a * max(0.0, (58.0 - str_def_a) / 58.0) * 0.55
    striking_vulnerability_b = wrestler_style_b * max(0.0, (58.0 - str_def_b) / 58.0) * 0.55

    striking_edge += (striking_vulnerability_b - striking_vulnerability_a)

    if td_avg_a > (td_avg_b + 0.9) and slpm_a < slpm_b:
        striking_edge += 0.12
    if td_avg_b > (td_avg_a + 0.9) and slpm_b < slpm_a:
        striking_edge -= 0.12

    matchup_bonus = 0.0
    raw_bonus = 0.0
    
    # -----------------------------------------------------------------------
    # All interaction signals defined as named probabilities (0.0–1.0)
    # -----------------------------------------------------------------------

    tdd_liability_a = tdd_liability(td_def_a)   # P(A gets controlled given TD lands)
    tdd_liability_b = tdd_liability(td_def_b)   # P(B gets controlled given TD lands)

    str_def_liability_a = str_def_liability(str_def_a)  # P(A is hurt in striking exchanges)
    str_def_liability_b = str_def_liability(str_def_b)  # P(B is hurt in striking exchanges)

    power_score_a = compute_power_score(profile_a)   # P(A creates meaningful damage)
    power_score_b = compute_power_score(profile_b)

    finisher_score_a = compute_finisher_score(profile_a)  # P(A converts damage to stoppage)
    finisher_score_b = compute_finisher_score(profile_b)

    anti_wrestling_a = compute_anti_wrestling_score(profile_a)  # P(A avoids/escapes control)
    anti_wrestling_b = compute_anti_wrestling_score(profile_b)

    # P(attacker has chain wrestling game plan) — scales with TD volume
    p_chain_a = min(1.0, td_avg_a / 4.5)
    p_chain_b = min(1.0, td_avg_b / 4.5)

    # P(entry attempt succeeds against specific defender)
    p_entry_a = compute_wrestling_entry_factor(profile_a, profile_b)
    p_entry_b = compute_wrestling_entry_factor(profile_b, profile_a)

    # P(control succeeds given TD lands) = vulnerability * P(can't escape)
    p_control_b_vs_a = tdd_liability_b * (1.0 - anti_wrestling_b * 0.45)   # B gets stuck
    p_control_a_vs_b = tdd_liability_a * (1.0 - anti_wrestling_a * 0.45)   # A gets stuck

    # P(attack initiates): union of chain game plan + opportunistic entry
    # Union prevents collapse when one factor is near-zero (e.g. Ivan barely shoots)
    # 1 - (1-A)(1-B) ensures even non-wrestlers have floor grappling pressure
    p_initiate_a = 1.0 - (1.0 - p_chain_a) * (1.0 - p_entry_a * 0.15)
    p_initiate_b = 1.0 - (1.0 - p_chain_b) * (1.0 - p_entry_b * 0.15)

    # Effective TDD pressure: union of initiation paths * control success
    # Floor at 0.05 — even strikers create occasional grappling obligation
    effective_tdd_pressure_b_vs_a = max(0.05, 1.0 - (1.0 - p_initiate_a) * (1.0 - p_control_b_vs_a * p_chain_a))
    effective_tdd_pressure_a_vs_b = max(0.05, 1.0 - (1.0 - p_initiate_b) * (1.0 - p_control_a_vs_b * p_chain_b))

    if td_avg_a > 4.0 and td_def_b < 55.0:
        raw_bonus += 0.50 * effective_tdd_pressure_b_vs_a
    elif td_avg_a > 3.0 and td_def_b < 65.0:
        raw_bonus += 0.30 * effective_tdd_pressure_b_vs_a

    if td_avg_b > 4.0 and td_def_a < 55.0:
        raw_bonus -= 0.50 * effective_tdd_pressure_a_vs_b
    elif td_avg_b > 3.0 and td_def_a < 65.0:
        raw_bonus -= 0.30 * effective_tdd_pressure_a_vs_b

    if sub_avg_a > 1.0 and td_def_b < 60.0:
        raw_bonus += 0.35 * effective_tdd_pressure_b_vs_a

    if sub_avg_b > 1.0 and td_def_a < 60.0:
        raw_bonus -= 0.35 * effective_tdd_pressure_a_vs_b

    if power_score_a > 0.70 and str_def_b < 45.0:
        raw_bonus += 0.45 * str_def_liability_b
        if sapm_b > 5.5:
            raw_bonus += 0.15 * str_def_liability_b
    elif power_score_a > 0.60 and str_def_b < 50.0:
        raw_bonus += 0.25 * str_def_liability_b

    if power_score_b > 0.70 and str_def_a < 45.0:
        raw_bonus -= 0.45 * str_def_liability_a
        if sapm_a > 5.5:
            raw_bonus -= 0.15 * str_def_liability_a
    elif power_score_b > 0.60 and str_def_a < 50.0:
        raw_bonus -= 0.25 * str_def_liability_a

    # Anti-wrestling global dampening — symmetric directional signal
    raw_bonus += ((1.0 - anti_wrestling_b) - (1.0 - anti_wrestling_a)) * 0.06

    matchup_bonus = apply_diminishing_returns(raw_bonus, compression_factor=0.8)

    sample_conf_a = float(profile_a.get("sample_confidence", 0.0) or 0.0)
    sample_conf_b = float(profile_b.get("sample_confidence", 0.0) or 0.0)
    avg_sample_conf = (sample_conf_a + sample_conf_b) / 2.0
    bonus_confidence_scale = 0.72 + (0.28 * avg_sample_conf)
    matchup_bonus *= bonus_confidence_scale

    age_adjust = 0.0
    age_a = fighter_age(profile_a)
    age_b = fighter_age(profile_b)
    if age_a is not None and age_b is not None:
        heavier = is_heavy_division(weight_class_name)
        age_gap = age_a - age_b
        if age_a >= 37 and age_b <= 33 and age_gap >= 5:
            age_adjust -= 0.22 if heavier else 0.34
        elif age_b >= 37 and age_a <= 33 and (-age_gap) >= 5:
            age_adjust += 0.22 if heavier else 0.34

    total = ((striking_edge * 0.52) + (grappling_edge * 0.33) + (reach_combo_bonus * 0.75) + 
             age_adjust + (matchup_bonus * 0.35))
    return total, striking_edge


def build_explanation(
    fighter_a: str,
    fighter_b: str,
    weight_class_name: str,
    prob_model_a: float,
    prob_profile_a: float | None,
    prob_final_a: float,
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
) -> Dict[str, Any]:
    favored = fighter_a if prob_final_a >= 0.5 else fighter_b
    underdog = fighter_b if favored == fighter_a else fighter_a
    favored_is_a = favored == fighter_a

    support_factors: list[tuple[float, str]] = []
    risk_factors: list[tuple[float, str]] = []

    def add_factor(score_for_a: float, text_when_a: str, text_when_b: str, weight: float = 1.0) -> None:
        if score_for_a == 0.0:
            return
        strength = abs(score_for_a) * weight
        if score_for_a > 0:
            text = text_when_a
            supports_favored = favored_is_a
        else:
            text = text_when_b
            supports_favored = not favored_is_a
        if supports_favored:
            support_factors.append((strength, text))
        else:
            risk_factors.append((strength, text))

    def add_named_risk(target_name: str, text: str, strength: float) -> None:
        if strength <= 0:
            return
        if target_name == favored:
            risk_factors.append((strength, text))
        else:
            support_factors.append((strength, text))

    metrics_a = profile_metrics(profile_a) if profile_a else {}
    metrics_b = profile_metrics(profile_b) if profile_b else {}
    is_heavy = is_heavy_division(weight_class_name)

    if metrics_a and metrics_b:
        win_rate_diff = metrics_a["win_rate"] - metrics_b["win_rate"]
        if abs(win_rate_diff) >= 0.04:
            add_factor(
                win_rate_diff,
                f"{fighter_a} has the stronger historical win rate",
                f"{fighter_b} has the stronger historical win rate",
            )

        reach_diff = metrics_a["reach_cm"] - metrics_b["reach_cm"]
        strike_a = float(profile_a.get("slpm", 0.0) or 0.0)
        strike_b = float(profile_b.get("slpm", 0.0) or 0.0)
        str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
        str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)
        strike_edge = (strike_a - strike_b) + ((str_def_a - str_def_b) / 100.0)
        td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
        td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
        sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
        sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
        td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
        td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)
        grappling_signal = (
            (td_avg_a - td_avg_b) * 0.45
            + (sub_avg_a - sub_avg_b) * 0.55
            + ((td_def_a - td_def_b) / 100.0) * 0.25
        )
        if abs(reach_diff) >= 6.0 and abs(strike_edge) >= 0.25:
            combo_signal = (reach_diff / 50.0) + (strike_edge / 3.0)
            add_factor(
                combo_signal,
                f"{fighter_a}'s reach + striking edge creates stronger distance control",
                f"{fighter_b}'s reach + striking edge creates stronger distance control",
            )
        elif abs(reach_diff) >= 7.0:
            add_factor(
                reach_diff,
                f"{fighter_a} has a notable reach advantage",
                f"{fighter_b} has a notable reach advantage",
                weight=(1.0 / 130.0),
            )

        if abs(grappling_signal) >= 0.35:
            add_factor(
                grappling_signal,
                f"{fighter_a} projects stronger mat control and grappling pressure",
                f"{fighter_b} projects stronger mat control and grappling pressure",
            )

        wrestle_threat_a = max(0.0, td_avg_a - 1.0) + (max(0.0, sub_avg_a - 0.2) * 0.7)
        wrestle_threat_b = max(0.0, td_avg_b - 1.0) + (max(0.0, sub_avg_b - 0.2) * 0.7)
        if wrestle_threat_b >= 0.8 and td_def_a <= 58.0:
            add_named_risk(
                fighter_a,
                f"{fighter_a} could be vulnerable to {fighter_b}'s takedown pressure due to lower TD defense",
                wrestle_threat_b / 2.4,
            )
        if wrestle_threat_a >= 0.8 and td_def_b <= 58.0:
            add_named_risk(
                fighter_b,
                f"{fighter_b} could be vulnerable to {fighter_a}'s takedown pressure due to lower TD defense",
                wrestle_threat_a / 2.4,
            )

        if td_avg_a >= 1.6 and str_def_a <= 52.0:
            add_named_risk(fighter_a, f"{fighter_a}'s lower striking defense adds risk in stand-up exchanges", 0.22)
        if td_avg_b >= 1.6 and str_def_b <= 52.0:
            add_named_risk(fighter_b, f"{fighter_b}'s lower striking defense adds risk in stand-up exchanges", 0.22)

        exp_diff = metrics_a["total_fights"] - metrics_b["total_fights"]
        if abs(exp_diff) >= 5:
            add_factor(
                exp_diff,
                f"{fighter_a} has more pro fight experience",
                f"{fighter_b} has more pro fight experience",
                weight=(1.0 / 50.0),
            )

        sos_diff = metrics_a["sos_score"] - metrics_b["sos_score"]
        if abs(sos_diff) >= 0.05:
            add_factor(
                sos_diff,
                f"{fighter_a} has faced tougher recent opposition in the last 5 opponents (SoS)",
                f"{fighter_b} has faced tougher recent opposition in the last 5 opponents (SoS)",
                weight=1.9,
            )

        age_a = metrics_a.get("age_years")
        age_b = metrics_b.get("age_years")
        if isinstance(age_a, float) and isinstance(age_b, float) and not math.isnan(age_a) and not math.isnan(age_b):
            older_name = fighter_a if age_a > age_b else fighter_b
            younger_name = fighter_b if older_name == fighter_a else fighter_a
            older_age = max(age_a, age_b)
            younger_age = min(age_a, age_b)
            if older_age >= 37 and younger_age <= 33 and (older_age - younger_age) >= 5:
                weight_note = " (lighter division)" if not is_heavy else ""
                if older_name == favored:
                    risk_factors.append((0.18, f"Age gap is substantial: {older_name} is in late-career range vs {younger_name}{weight_note}"))
                else:
                    support_factors.append((0.18, f"Age gap is substantial: {older_name} is in late-career range vs {younger_name}{weight_note}"))

    support_factors.append((abs(prob_final_a - 0.5), f"Model confidence leans toward {favored}"))
    if prob_profile_a is not None:
        profile_supports_favored = (prob_profile_a >= 0.5 and favored_is_a) or (prob_profile_a < 0.5 and not favored_is_a)
        if profile_supports_favored:
            support_factors.append((abs(prob_profile_a - 0.5), "Profile matchup signal supports the lean"))
        else:
            risk_factors.append((abs(prob_profile_a - 0.5), f"Profile matchup signal leans toward {underdog}"))

    model_supports_favored = (prob_model_a >= 0.5 and favored_is_a) or (prob_model_a < 0.5 and not favored_is_a)
    if model_supports_favored:
        support_factors.append((abs(prob_model_a - 0.5), "Baseline trained model output contributes to the pick"))
    else:
        risk_factors.append((abs(prob_model_a - 0.5), f"Baseline trained model output leans toward {underdog}"))

    top_support = [item[1] for item in sorted(support_factors, key=lambda item: item[0], reverse=True)[:2]]
    top_risk = [f"Risk: {item[1]}" for item in sorted(risk_factors, key=lambda item: item[0], reverse=True)[:1]]
    factors_sorted = top_support + top_risk
    if len(factors_sorted) < 3:
        remaining_support = [item[1] for item in sorted(support_factors, key=lambda item: item[0], reverse=True)[2:]]
        remaining_risk = [f"Risk: {item[1]}" for item in sorted(risk_factors, key=lambda item: item[0], reverse=True)[1:]]
        factors_sorted = (factors_sorted + remaining_support + remaining_risk)[:3]

    summary = f"{favored} is favored over {underdog} based on blended model and profile matchup signals."

    model_edge_name = fighter_a if prob_model_a >= 0.5 else fighter_b
    model_edge_points = abs(prob_model_a - 0.5) * 200.0
    profile_narrative = "Profile signal was unavailable for one or both fighters."
    if prob_profile_a is not None:
        profile_edge_name = fighter_a if prob_profile_a >= 0.5 else fighter_b
        profile_edge_points = abs(prob_profile_a - 0.5) * 200.0
        profile_narrative = f"Matchup profile leans {profile_edge_name} by {profile_edge_points:.1f} points."

    blended_points = abs(prob_final_a - 0.5) * 200.0
    confidence_band = "high" if blended_points >= 24 else ("medium" if blended_points >= 12 else "low")
    favored_pct = prob_final_a * 100.0 if favored == fighter_a else (1.0 - prob_final_a) * 100.0
    detailed_summary = (
        f"{favored} is projected at {favored_pct:.1f}% win probability. "
        f"Baseline model leans {model_edge_name} by {model_edge_points:.1f} points. "
        f"{profile_narrative} "
        f"Final blended edge is {blended_points:.1f} points ({confidence_band} confidence)."
    )

    return {
        "summary": summary,
        "detailed_summary": detailed_summary,
        "factors": factors_sorted,
        "source": "blended-model-profile",
        "blended_prob_fighterA": round(prob_final_a, 6),
        "model_prob_fighterA": round(prob_model_a, 6),
        "profile_prob_fighterA": round(prob_profile_a, 6) if prob_profile_a is not None else None,
    }


def detect_fight_regime(profile_a: Dict[str, Any], profile_b: Dict[str, Any]) -> tuple[str, str | None]:
    """
    Classify dominant fight mode using RELATIVE signed deltas.

    For each domain compute (score_a - score_b).  The domain with the largest
    absolute delta wins the regime label — but only if it also has a
    meaningful lead over the second-best domain (DOMINANCE_RATIO) AND exceeds
    a minimum noise floor (MIN_DELTA).  Otherwise the fight is contested.

    Returns (regime, dominant_side) where dominant_side is 'a', 'b', or None.
    """
    regime_scores = compute_regime_scores(profile_a, profile_b)
    delta_wrestling   = regime_scores["wrestling_regime_score"]   # wrestle_a - wrestle_b
    delta_submission  = regime_scores["submission_regime_score"]  # sub_a - sub_b
    delta_striking    = regime_scores["striking_regime_score"]    # strike_a - strike_b

    # Minimum absolute advantage to claim a regime — filters out noise
    MIN_DELTA = 0.06
    # Winner must be this many times larger than the second-best |delta|
    DOMINANCE_RATIO = 1.40

    candidates = {
        'wrestling_control': delta_wrestling,
        'submission_threat': delta_submission,
        'striking_exchange': delta_striking,
    }
    # Sort by absolute magnitude descending
    ranked = sorted(candidates.items(), key=lambda kv: abs(kv[1]), reverse=True)
    top_name, top_delta   = ranked[0]
    _,         second_val = ranked[1]

    # Claim a regime only if it clears the noise floor AND has a clear lead
    if abs(top_delta) >= MIN_DELTA and abs(top_delta) >= abs(second_val) * DOMINANCE_RATIO:
        dominant_side = 'a' if top_delta > 0 else 'b'
        return top_name, dominant_side

    return 'contested', None


def compute_logit_components_detailed(
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
    weight_class_name: str,
) -> Dict[str, Any]:
    """
    Regime-gated conditional fight simulator.

    Architecture:
    1. Each domain produces two non-negative unilateral advantage scores
       (adv_a, adv_b).  Each is tanh-normalized independently then
       differenced → domain_logit stays well-scaled in ~[-1.0, +1.0].
    2. Regime detection picks ONE domain as 'main', the others as 'support'.
    3. regime_strength amplifies the main path;  regime_weakness compresses
       the support blend so non-dominant paths cannot overrule the regime.
    4. In contested fights all three paths blend equally at unit scale.
    """
    slpm_a    = float(profile_a.get("slpm",    0.0) or 0.0)
    slpm_b    = float(profile_b.get("slpm",    0.0) or 0.0)
    sapm_a    = float(profile_a.get("sapm",    0.0) or 0.0)
    sapm_b    = float(profile_b.get("sapm",    0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)
    td_avg_a  = float(profile_a.get("td_avg",  0.0) or 0.0)
    td_avg_b  = float(profile_b.get("td_avg",  0.0) or 0.0)
    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
    td_def_a  = float(profile_a.get("td_def",  0.0) or 0.0)
    td_def_b  = float(profile_b.get("td_def",  0.0) or 0.0)
    reach_a   = float(profile_a.get("reach_cm", 175.0) or 175.0)
    reach_b   = float(profile_b.get("reach_cm", 175.0) or 175.0)

    tdd_lib_a = tdd_liability(td_def_a)
    tdd_lib_b = tdd_liability(td_def_b)
    power_a   = compute_power_score(profile_a)
    power_b   = compute_power_score(profile_b)
    finisher_a = compute_finisher_score(profile_a)
    finisher_b = compute_finisher_score(profile_b)

    td_vol_a   = min(1.0, td_avg_a / 3.5)
    td_vol_b   = min(1.0, td_avg_b / 3.5)
    tdd_vuln_a = math.sqrt(max(0.08, 1.0 - td_def_a / 100.0))
    tdd_vuln_b = math.sqrt(max(0.08, 1.0 - td_def_b / 100.0))

    sample_conf_a = float(profile_a.get("sample_confidence", 0.30) or 0.30)
    sample_conf_b = float(profile_b.get("sample_confidence", 0.30) or 0.30)
    sample_conf = min(sample_conf_a, sample_conf_b)
    domain_scale = max(0.65, min(1.05, 0.72 + (sample_conf * 0.55)))

    # -----------------------------------------------------------------------
    # ENTRY GATING
    # Grappling is a CONDITIONAL phase — it only happens if the attacker
    # can reliably close the distance and survive the entry attempt.
    # entry_prob_x gates how much of a fighter's raw grappling/submission
    # advantage actually translates into real logit contribution.
    # -----------------------------------------------------------------------
    entry_prob_a = compute_grappling_entry_prob(profile_a, profile_b)
    entry_prob_b = compute_grappling_entry_prob(profile_b, profile_a)

    # -----------------------------------------------------------------------
    # DOMAIN 1 — GRAPPLING
    # adv_a = A's takedown output projected onto B's defensive vulnerability,
    # GATED by A's entry success probability.
    # Without entry gating, a high-TD-avg fighter gets full credit even when
    # they get hit repeatedly trying to close distance.
    # Normalise by 0.50 so a typical strong wrestler (adv ≈ 0.35) gives
    # tanh(0.70) ≈ 0.60 — a clear but not maxed logit.
    # -----------------------------------------------------------------------
    grappling_adv_a = max(0.0, td_vol_a * tdd_vuln_b * entry_prob_a)   # A projects onto B
    grappling_adv_b = max(0.0, td_vol_b * tdd_vuln_a * entry_prob_b)   # B projects onto A
    G_NORM = 0.50
    grappling_logit = math.tanh(grappling_adv_a / G_NORM) - math.tanh(grappling_adv_b / G_NORM)
    grappling_logit *= domain_scale

    # -----------------------------------------------------------------------
    # DOMAIN 2 — STRIKING
    # Per-fighter absolute striking advantage composed of volume, defense,
    # absorption exposure, and power.  Reach contributes only to the already-
    # advantaged side.  Both values are non-negative by construction.
    # Normalise by 1.50 so a typical strong striker (adv ≈ 1.5) gives
    # tanh(1.0) ≈ 0.76.
    # -----------------------------------------------------------------------
    reach_diff_cm = reach_a - reach_b
    sapm_term_b = math.log1p(min(6.0, max(0.0, sapm_b))) * 0.22
    sapm_term_a = math.log1p(min(6.0, max(0.0, sapm_a))) * 0.22
    striking_adv_a = (
        slpm_a * 0.20
        + (str_def_a / 100.0) * 0.35
        + sapm_term_b
        + power_a * 0.50
        + max(0.0,  reach_diff_cm / 100.0) * 0.06
    )
    striking_adv_b = (
        slpm_b * 0.20
        + (str_def_b / 100.0) * 0.35
        + sapm_term_a
        + power_b * 0.50
        + max(0.0, -reach_diff_cm / 100.0) * 0.06
    )
    striking_adv_a = max(0.0, striking_adv_a)
    striking_adv_b = max(0.0, striking_adv_b)
    S_NORM = 1.50
    striking_logit = math.tanh(striking_adv_a / S_NORM) - math.tanh(striking_adv_b / S_NORM)
    striking_logit *= domain_scale

    # -----------------------------------------------------------------------
    # DOMAIN 3 — SUBMISSION
    # Sub threat is gated by the opponent's TDD vulnerability and positional
    # access (td_vol as proxy for takedown entry).  Both values ≥ 0.
    # Normalise by 0.40 so sub_avg 0.8, tdd_lib 0.5, access factor 1.2 →
    # penalty tanh(0.48/0.40) ≈ tanh(1.2) ≈ 0.83.
    # -----------------------------------------------------------------------
    # Submission also requires grappling entry — gate by entry probability.
    # Use additive stacking to avoid multiplicative explosion in low samples.
    sub_adv_a = max(0.0, ((sub_avg_a * 0.55) + (tdd_lib_b * 0.25) + (td_vol_a * 0.20)) * entry_prob_a)
    sub_adv_b = max(0.0, ((sub_avg_b * 0.55) + (tdd_lib_a * 0.25) + (td_vol_b * 0.20)) * entry_prob_b)
    B_NORM = 0.45
    submission_logit = math.tanh(sub_adv_a / B_NORM) - math.tanh(sub_adv_b / B_NORM)
    submission_logit *= domain_scale

    # -----------------------------------------------------------------------
    # INTERACTION — effective grappling pressure differential
    # Gated by entry probability: a fighter with low entry reliability cannot
    # convert their raw pressure rating into actual fight control.
    # -----------------------------------------------------------------------
    eff_pressure_a_to_b = compute_effective_pressure(profile_a, profile_b)
    eff_pressure_b_to_a = compute_effective_pressure(profile_b, profile_a)
    gated_pressure_a = eff_pressure_a_to_b * entry_prob_a
    gated_pressure_b = eff_pressure_b_to_a * entry_prob_b
    interaction_logit = math.tanh((gated_pressure_a - gated_pressure_b) * 1.1) * 0.22

    # -----------------------------------------------------------------------
    # ROUND-WINNING SUPPORT LAYER — bounded, finishing-aware tie-breaker.
    # This should help decision equity / minute-winning profiles without ever
    # dominating the regime engine or turning the model into a generic volume
    # system. High-finishing matchups damp this term slightly.
    # -----------------------------------------------------------------------
    round_win_score_a = compute_round_winning_score(profile_a)
    round_win_score_b = compute_round_winning_score(profile_b)
    round_win_edge = round_win_score_a - round_win_score_b
    finish_volatility = min(1.0, ((power_a + finisher_a + power_b + finisher_b) / 4.0))
    archetype_a = classify_archetype(profile_a)
    archetype_b = classify_archetype(profile_b)
    decision_archetype_weights = {
        "technical_striker": 0.55,
        "pressure_wrestler": 0.50,
        "balanced": 0.30,
        "submission_grappler": 0.12,
        "power_striker": 0.08,
    }
    archetype_decision_bias = min(
        1.0,
        decision_archetype_weights.get(archetype_a, 0.20) + decision_archetype_weights.get(archetype_b, 0.20),
    )
    control_environment = min(1.0, (compute_control_proxy(profile_a) + compute_control_proxy(profile_b)) / 1.6)
    round_bank_environment = min(1.0, (round_win_score_a + round_win_score_b) / 1.30)
    decision_likelihood = min(
        1.0,
        max(
            0.0,
            (1.0 - finish_volatility) * 0.48
            + archetype_decision_bias * 0.28
            + control_environment * 0.14
            + round_bank_environment * 0.10,
        ),
    )

    regime_preview, _ = detect_fight_regime(profile_a, profile_b)

    round_win_weight = 0.16 * (1.0 - finish_volatility * 0.35)
    round_win_weight *= (1.0 + decision_likelihood * 0.55)

    # Higher decision-equity influence in decision-leaning regimes; damp in
    # explosive regimes so this stays a support layer.
    if regime_preview in {"contested", "wrestling_control", "clean_dominance", "coherence_realigned"}:
        round_win_weight *= 1.10
    elif regime_preview in {"striking_exchange", "submission_threat"}:
        round_win_weight *= 0.85

    round_win_weight = min(0.26, max(0.10, round_win_weight))
    round_win_logit = math.tanh(round_win_edge * 2.4) * round_win_weight
    interaction_logit += round_win_logit

    # -----------------------------------------------------------------------
    # REGIME GATING — select main path and compress non-dominant paths
    #
    # regime_strength amplifies the domain that the fight is being fought in.
    # regime_weakness compresses everything else so the regime actually gates
    # instead of just nudging a weighted average.
    # -----------------------------------------------------------------------
    regime, dominant_side = detect_fight_regime(profile_a, profile_b)
    regime_scores = compute_regime_scores(profile_a, profile_b)

    if regime == 'wrestling_control':
        main_logit    = grappling_logit
        support_logit = striking_logit * 0.25 + submission_logit * 0.15
        regime_strength = 1.42
        regime_weakness = 0.50
        dominant_path_name = 'wrestling'

    elif regime == 'striking_exchange':
        main_logit    = striking_logit
        support_logit = grappling_logit * 0.30 + submission_logit * 0.20
        regime_strength = 1.42
        regime_weakness = 0.50
        dominant_path_name = 'striking'

    elif regime == 'submission_threat':
        main_logit    = submission_logit
        support_logit = grappling_logit * 0.40 + striking_logit * 0.25
        regime_strength = 1.42
        regime_weakness = 0.50
        dominant_path_name = 'submission'

    else:  # contested — low amplification, let domains speak naturally
        main_logit    = striking_logit * 0.4 + grappling_logit * 0.4 + submission_logit * 0.2
        support_logit = 0.0
        regime_strength = 1.15
        regime_weakness = 1.0
        dominant_path_name = 'contested'

    # Coherence guard: if the regime label contradicts the quantitative signals,
    # re-evaluate based on domain agreement instead of falling back to contested.
    #
    # CLEAN DOMINANCE: if all three domain logits agree in sign, one fighter
    # wins every phase — this should be amplified, NOT compressed to 50/50.
    #   Amplification: 1.4× (clear but not maxed — hardware reality can upset sims)
    #
    # TRUE CONTESTED: only when domains genuinely split (mixed signs), meaning
    # the fight is structurally balanced.
    all_agree_a = (striking_logit > 0 and grappling_logit > 0 and submission_logit > 0)
    all_agree_b = (striking_logit < 0 and grappling_logit < 0 and submission_logit < 0)

    if all_agree_a or all_agree_b:
        # All domains point the same direction — amplify the consensus signal
        main_logit    = striking_logit * 0.4 + grappling_logit * 0.4 + submission_logit * 0.2
        support_logit = 0.0
        regime_strength = 1.42
        regime_weakness = 1.0
        dominant_path_name = 'clean_dominance'
        regime = 'clean_dominance'
    elif (dominant_side == 'b' and main_logit > 0) or (dominant_side == 'a' and main_logit < 0):
        # Regime label contradicts quantitative domains: realign to the single
        # strongest domain instead of flattening into contested.
        domain_candidates = [
            ('striking', striking_logit),
            ('wrestling', grappling_logit),
            ('submission', submission_logit),
        ]
        dominant_path_name, main_logit = max(domain_candidates, key=lambda x: abs(x[1]))
        support_logit = 0.0
        regime_strength = 1.25
        regime_weakness = 1.0
        regime = 'coherence_realigned'

    return {
        # Primary assembly fields (used by main())
        "main_logit":       main_logit,
        "support_logit":    support_logit,
        "regime_strength":  regime_strength,
        "regime_weakness":  regime_weakness,
        "interaction_logit": interaction_logit,
        "round_win_score_a": round_win_score_a,
        "round_win_score_b": round_win_score_b,
        "round_win_logit": round_win_logit,
        "round_win_weight": round_win_weight,
        "decision_likelihood": decision_likelihood,
        "regime":           regime,
        "dominant_path_name": dominant_path_name,
        # Domain-level diagnostics
        "striking_logit":   striking_logit,
        "grappling_logit":  grappling_logit,
        "submission_logit": submission_logit,
        "effective_pressure_a_to_b": eff_pressure_a_to_b,
        "effective_pressure_b_to_a": eff_pressure_b_to_a,
        "regime_scores":    regime_scores,
        "entry_prob_a":     entry_prob_a,
        "entry_prob_b":     entry_prob_b,
        # Legacy compat keys (debug script + calibration block)
        "dominant_path_logit": main_logit,
        "secondary_logit":  support_logit,
        "regime_multiplier": regime_strength,
    }


def compute_logit_components(
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
    weight_class_name: str,
) -> tuple[float, float, float, str, str]:
    details = compute_logit_components_detailed(profile_a, profile_b, weight_class_name)
    return (
        float(details.get("dominant_path_logit", 0.0) or 0.0),
        float(details.get("secondary_logit", 0.0) or 0.0),
        float(details.get("regime_multiplier", 1.0) or 1.0),
        str(details.get("dominant_path_name", "contested") or "contested"),
        str(details.get("regime", "contested") or "contested"),
    )


def calibrate_probability(probability: float) -> float:
    # Legacy — kept for fallback (no-profile) path only.
    # Do NOT use in the logit-space assembly path.
    return max(0.01, min(0.99, probability))


def apply_matchup_correction(prob_model_a: float, prob_profile_a: float | None) -> tuple[float, float]:
    if prob_profile_a is None:
        return prob_model_a, 0.0

    raw_delta = prob_profile_a - prob_model_a
    model_extremeness = min(1.0, abs(prob_model_a - 0.5) * 2.0)
    correction_scale = 0.10 + (0.04 * model_extremeness)
    correction = math.tanh(raw_delta * 1.1) * correction_scale
    corrected = max(0.01, min(0.99, prob_model_a + correction))
    return corrected, correction


def method_probabilities(
    winner_profile: Dict[str, Any],
    loser_profile: Dict[str, Any],
    confidence: float,
    method_context: Dict[str, Any] | None = None,
) -> Dict[str, float]:
    method_context = method_context or {}
    winner_slpm = float(winner_profile.get("slpm", 0.0) or 0.0)
    loser_slpm = float(loser_profile.get("slpm", 0.0) or 0.0)
    winner_sub_avg = float(winner_profile.get("sub_avg", 0.0) or 0.0)
    loser_sub_avg = float(loser_profile.get("sub_avg", 0.0) or 0.0)
    winner_td_avg = float(winner_profile.get("td_avg", 0.0) or 0.0)
    loser_td_avg = float(loser_profile.get("td_avg", 0.0) or 0.0)

    loser_sapm = float(loser_profile.get("sapm", 0.0) or 0.0)
    winner_sapm = float(winner_profile.get("sapm", 0.0) or 0.0)
    winner_str_def = float(winner_profile.get("str_def", 0.0) or 0.0)
    loser_str_def = float(loser_profile.get("str_def", 0.0) or 0.0)
    winner_td_def = float(winner_profile.get("td_def", 0.0) or 0.0)
    loser_td_def = float(loser_profile.get("td_def", 0.0) or 0.0)
    winner_sub_win_rate = float(winner_profile.get("sub_win_rate", 0.0) or 0.0)
    winner_decision_win_rate = float(winner_profile.get("decision_win_rate", 0.0) or 0.0)
    winner_finish_round1_rate = float(winner_profile.get("finish_wins_round1_rate", 0.0) or 0.0)
    winner_late_finish_rate = float(winner_profile.get("late_finish_wins_rate", 0.0) or 0.0)
    winner_cardio_risk = float(winner_profile.get("cardio_risk", 0.0) or 0.0)
    loser_cardio_risk = float(loser_profile.get("cardio_risk", 0.0) or 0.0)
    winner_top_control_per_td = float(winner_profile.get("top_control_minutes_per_td", 0.0) or 0.0)
    winner_ground_ratio = float(winner_profile.get("ground_time_ratio", 0.0) or 0.0)
    loser_sub_loss_rate = float(loser_profile.get("sub_loss_rate", 0.0) or 0.0)
    loser_ko_tko_loss_rate = float(loser_profile.get("ko_tko_loss_rate", 0.0) or 0.0)
    winner_control_proxy = compute_control_proxy(winner_profile)
    loser_control_proxy = compute_control_proxy(loser_profile)
    loser_anti_wrestling = compute_anti_wrestling_score(loser_profile)

    dominant_regime = str(method_context.get("dominant_regime", "contested") or "contested")
    dominant_path_name = str(method_context.get("dominant_path_name", "contested") or "contested")
    weight_class_name = str(method_context.get("weight_class", "") or "")
    winner_main_logit = max(0.0, float(method_context.get("winner_main_logit", 0.0) or 0.0))
    winner_interaction_logit = max(0.0, float(method_context.get("winner_interaction_logit", 0.0) or 0.0))
    winner_round_win_logit = float(method_context.get("winner_round_win_logit", 0.0) or 0.0)
    entry_prob_winner = float(method_context.get("entry_prob_winner", 0.55) or 0.55)
    entry_prob_loser = float(method_context.get("entry_prob_loser", 0.55) or 0.55)

    winner_power_score = compute_power_score(winner_profile)
    winner_finisher_score = compute_finisher_score(winner_profile)
    winner_round_score = compute_round_winning_score(winner_profile)

    confidence_signal = min(1.0, max(0.0, (confidence - 0.50) / 0.35))
    loser_strike_fragility = max(0.0, (58.0 - loser_str_def) / 58.0)
    loser_wrestle_fragility = max(0.0, (60.0 - loser_td_def) / 60.0)
    loser_finish_fragility = min(
        1.0,
        (loser_ko_tko_loss_rate * 0.55) + (loser_sub_loss_rate * 0.45) + (loser_cardio_risk * 0.20),
    )

    finish_pressure = (
        0.18
        + confidence_signal * 0.42
        + min(0.35, winner_main_logit * 0.28)
        + min(0.25, winner_interaction_logit * 0.45)
        + (winner_finisher_score * 0.20)
    )

    striking_edge = (
        (winner_slpm - loser_slpm) * 0.9
        + (loser_sapm - winner_sapm) * 0.35
        + ((winner_str_def - loser_str_def) / 100.0) * 0.5
    )
    ko_signal = (
        -0.08
        + max(0.0, striking_edge) * 0.85
        + loser_strike_fragility * 0.70
        + winner_power_score * 0.55
        + winner_finisher_score * 0.45
        + (loser_ko_tko_loss_rate * 0.40)
        + (winner_finish_round1_rate * 0.22)
        + (confidence_signal * 0.20)
    )

    control_pressure = (
        max(0.0, winner_td_avg - loser_td_avg) * 0.9
        + max(0.0, winner_control_proxy - loser_control_proxy) * 0.8
        + max(0.0, (100.0 - loser_td_def) / 100.0 - 0.35) * 0.7
        + max(0.0, winner_top_control_per_td - 0.7) * 0.35
        + max(0.0, winner_ground_ratio - 0.22) * 0.25
    )

    submission_hunter_signal = (
        max(0.0, winner_sub_avg - 0.45) * 1.0
        + winner_sub_win_rate * 0.95
        + loser_sub_loss_rate * 0.75
        + max(0.0, 0.55 - loser_anti_wrestling) * 0.9
    )
    sub_entry_support = max(0.0, winner_td_avg - 0.8) * 0.25
    grappling_finish_signal = submission_hunter_signal + sub_entry_support
    sub_activation = 0.0
    if winner_sub_avg >= 0.70:
        sub_activation += 0.35
    if winner_sub_win_rate >= 0.25:
        sub_activation += 0.30
    if loser_sub_loss_rate >= 0.20:
        sub_activation += 0.20
    if loser_anti_wrestling <= 0.45:
        sub_activation += 0.20
    sub_activation = min(1.0, sub_activation)
    sub_signal = (
        -0.12
        + max(0.0, (grappling_finish_signal * sub_activation) - (control_pressure * 0.30))
        + loser_wrestle_fragility * 0.35
        + loser_sub_loss_rate * 0.40
        + max(0.0, entry_prob_winner - 0.45) * 0.85
        + winner_finisher_score * 0.12
    )

    closeness = max(0.0, 0.62 - abs(confidence - 0.5))
    dec_signal = 0.10 + (closeness * 0.85)
    dec_signal += (control_pressure * 0.38) + (winner_decision_win_rate * 0.40)
    dec_signal += max(0.0, winner_top_control_per_td - 0.75) * 0.15
    dec_signal += max(0.0, winner_late_finish_rate - 0.25) * 0.08
    dec_signal += winner_round_score * 0.22
    dec_signal += max(0.0, winner_round_win_logit) * 0.12
    dec_signal += max(0.0, entry_prob_loser - 0.55) * 0.20
    dec_signal -= winner_cardio_risk * 0.18
    dec_signal -= finish_pressure * 0.65
    dec_signal -= loser_finish_fragility * 0.20

    if dominant_regime == "submission_threat":
        sub_signal += 0.55
        dec_signal -= 0.28
        ko_signal -= 0.06
    elif dominant_regime == "wrestling_control":
        sub_signal += 0.22
        dec_signal += 0.12
        ko_signal -= 0.08
    elif dominant_regime == "striking_exchange":
        ko_signal += 0.45
        dec_signal -= 0.20
        sub_signal -= 0.08
    elif dominant_regime == "clean_dominance":
        if dominant_path_name == "submission":
            sub_signal += 0.40
            dec_signal -= 0.20
        elif dominant_path_name == "striking":
            ko_signal += 0.35
            dec_signal -= 0.18
        elif dominant_path_name == "wrestling":
            sub_signal += 0.12
            dec_signal += 0.10

    if "Heavyweight" in weight_class_name or "Light Heavyweight" in weight_class_name:
        ko_signal += 0.18
        dec_signal -= 0.16

    logits = {
        "KO/TKO": max(0.01, ko_signal),
        "Submission": max(0.01, sub_signal),
        "Decision": max(0.01, dec_signal),
    }

    max_logit = max(logits.values())
    exp_values = {key: math.exp(value - max_logit) for key, value in logits.items()}
    total = sum(exp_values.values())
    if total <= 0:
        return {"KO/TKO": 0.33, "Submission": 0.33, "Decision": 0.34}

    probs = {key: value / total for key, value in exp_values.items()}

    finish_floor = 0.30 + (confidence_signal * 0.16)
    if dominant_regime in {"submission_threat", "striking_exchange"}:
        finish_floor += 0.08
    elif dominant_regime == "clean_dominance":
        finish_floor += 0.06
    if "Heavyweight" in weight_class_name or "Light Heavyweight" in weight_class_name:
        finish_floor += 0.06
    finish_floor = min(0.68, max(0.28, finish_floor))

    decision_cap = 1.0 - finish_floor
    if probs["Decision"] > decision_cap:
        excess = probs["Decision"] - decision_cap
        probs["Decision"] = decision_cap
        finish_total = probs["KO/TKO"] + probs["Submission"]
        if finish_total <= 1e-9:
            probs["KO/TKO"] += excess * 0.60
            probs["Submission"] += excess * 0.40
        else:
            probs["KO/TKO"] += excess * (probs["KO/TKO"] / finish_total)
            probs["Submission"] += excess * (probs["Submission"] / finish_total)

    norm = probs["KO/TKO"] + probs["Submission"] + probs["Decision"]
    if norm <= 0:
        return {"KO/TKO": 0.33, "Submission": 0.33, "Decision": 0.34}
    return {k: v / norm for k, v in probs.items()}


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def build_fight_key(fighter_a: str, fighter_b: str) -> str:
    return hashlib.md5(f"{fighter_a}|{fighter_b}".encode("utf-8")).hexdigest()


def build_feature_signature(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> str:
    keys = ["wins", "losses", "draws", "reach_cm", "slpm", "sapm", "td_avg", "sub_avg", "str_def", "td_def"]
    tuple_a = tuple(float(profile_a.get(key, 0.0) or 0.0) for key in keys)
    tuple_b = tuple(float(profile_b.get(key, 0.0) or 0.0) for key in keys)
    payload = f"{weight_class_name}|{tuple_a}|{tuple_b}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing input file: {DATA_PATH}")

    with DATA_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "fighterA" not in reader.fieldnames or "fighterB" not in reader.fieldnames:
            raise ValueError("Input CSV must include fighterA and fighterB columns")

        rows = list(reader)

    predictions: Dict[str, Any] = {}
    profile_cache: Dict[str, Dict[str, Any]] = {}
    probability_values: list[float] = []
    method_values: list[str] = []
    feature_signatures: list[str] = []
    missing_profile_fights = 0
    skipped_low_cage_time_fights = 0

    for row in rows:
        fighter_a = str(row.get("fighterA", "")).strip()
        fighter_b = str(row.get("fighterB", "")).strip()
        if not fighter_a or not fighter_b:
            continue

        fighter_a_url = str(row.get("fighterA_url", "")).strip()
        fighter_b_url = str(row.get("fighterB_url", "")).strip()
        weight_class_name = str(row.get("weight_class_name", "")).strip()

        profile_a = parse_fighter_profile(fighter_a_url, profile_cache)
        profile_b = parse_fighter_profile(fighter_b_url, profile_cache)
        if not profile_a or not profile_b:
            missing_profile_fights += 1

        cage_time_a = float((profile_a or {}).get("ufc_cage_time_minutes", 0.0) or 0.0)
        cage_time_b = float((profile_b or {}).get("ufc_cage_time_minutes", 0.0) or 0.0)
        if cage_time_a < MIN_UFC_CAGE_TIME_MINUTES or cage_time_b < MIN_UFC_CAGE_TIME_MINUTES:
            skipped_low_cage_time_fights += 1
            continue

        if profile_a and profile_b:
            payload = build_model_feature_payload(fighter_a, fighter_b, profile_a, profile_b)
        else:
            payload = {
                "fighterA": fighter_a,
                "fighterB": fighter_b,
            }

        winner, probabilities = predict_fight(payload)

        prob_model_a_raw = float(probabilities.get(fighter_a, 0.5))
        prob_model_a = calibrate_probability(prob_model_a_raw)

        feature_signatures.append(build_feature_signature(profile_a or {}, profile_b or {}, weight_class_name))

        prob_profile_a = None
        logit_components = 0.0
        uncertainty_factor = 1.0
        dominant_path_name = "contested"
        dominant_regime = "contested"
        dominant_path_logit = 0.0
        secondary_logit = 0.0
        interaction_logit = 0.0
        logit_details: Dict[str, Any] = {}
        if profile_a and profile_b:
            # -----------------------------------------------------------------
            # Logit-space assembly (Phase 2 — pure logit, no p-space blending)
            # -----------------------------------------------------------------
            # Base logit: ML model prior in true logit space.
            # Keep it weak so simulation remains primary, but avoid pseudo-logit
            # compression so scales stay consistent.
            prob_model_a_raw_clamped = max(0.01, min(0.99, prob_model_a_raw))
            base_logit_raw = math.log(prob_model_a_raw_clamped / (1.0 - prob_model_a_raw_clamped))
            logit_base = base_logit_raw * 0.35  # ML prior = ~35% of signal in true logit space

            # Regime-gated conditional path assembly
            logit_details = compute_logit_components_detailed(profile_a, profile_b, weight_class_name)
            main_logit      = float(logit_details.get("main_logit",      0.0) or 0.0)
            support_logit   = float(logit_details.get("support_logit",   0.0) or 0.0)
            regime_strength = float(logit_details.get("regime_strength", 1.0) or 1.0)
            regime_weakness = float(logit_details.get("regime_weakness", 1.0) or 1.0)
            interaction_logit = float(logit_details.get("interaction_logit", 0.0) or 0.0)
            dominant_path_name = str(logit_details.get("dominant_path_name", "contested") or "contested")
            dominant_regime    = str(logit_details.get("regime", "contested") or "contested")
            # Legacy compat aliases kept for calibration block
            dominant_path_logit = main_logit
            secondary_logit     = support_logit
            regime_multiplier   = regime_strength

            # Age adjustment in logit space
            age_adjust_logit = 0.0
            age_a = fighter_age(profile_a)
            age_b = fighter_age(profile_b)
            if age_a is not None and age_b is not None:
                heavier = is_heavy_division(weight_class_name)
                age_gap = age_a - age_b
                if age_a >= 37 and age_b <= 33 and age_gap >= 5:
                    age_adjust_logit = -0.35 if heavier else -0.55
                elif age_b >= 37 and age_a <= 33 and (-age_gap) >= 5:
                    age_adjust_logit = 0.35 if heavier else 0.55

            # Uncertainty is retained for diagnostics/calibration only.
            uncertainty_factor = compute_uncertainty_factor(profile_a, profile_b, weight_class_name)

            # Final assembly (simulation-first with ML prior anchor):
            #   logit_components    = primary fight simulation signal
            #   logit_base          = ML prior (~25%) in true logit space
            #   uncertainty_factor  = reliability applied to prior only
            #   main * strength     = dominant fight dimension   (amplified 1.35-1.8x)
            #   support * weakness  = secondary paths            (compressed)
            #   interaction         = entry-gated grappling pressure differential
            #   age_adjust          = career-stage penalty
            logit_components = (main_logit * regime_strength
                                 + support_logit * regime_weakness
                                 + interaction_logit
                                 + age_adjust_logit)

            # Regime strength is already embedded in main/support assembly.
            # Apply uncertainty only to the prior to avoid draw-magnet compression.
            logit_p = logit_components + (logit_base * uncertainty_factor)

            # Probability mapping: apply logit directly to sigmoid.
            prob_a = sigmoid(logit_p)
            prob_profile_a = sigmoid(logit_components)  # for explanation layer only
            matchup_correction = logit_components   # simulation contribution (for calibration logging)
        else:
            prob_a = prob_model_a
            matchup_correction = 0.0

        prob_a = max(0.01, min(0.99, prob_a))
        prob_b = 1.0 - prob_a
        probability_values.append(round(prob_a, 6))
        winner = fighter_a if prob_a >= prob_b else fighter_b
        winner_profile = profile_a if winner == fighter_a else profile_b
        loser_profile = profile_b if winner == fighter_a else profile_a
        winner_is_a = winner == fighter_a
        winner_confidence = max(prob_a, prob_b)
        method_context = {
            "dominant_regime": dominant_regime,
            "dominant_path_name": dominant_path_name,
            "weight_class": weight_class_name,
            "winner_main_logit": dominant_path_logit if winner_is_a else -dominant_path_logit,
            "winner_interaction_logit": interaction_logit if winner_is_a else -interaction_logit,
            "winner_round_win_logit": float(logit_details.get("round_win_logit", 0.0) or 0.0) if winner_is_a else -float(logit_details.get("round_win_logit", 0.0) or 0.0),
            "winner_logit_components": logit_components if winner_is_a else -logit_components,
            "entry_prob_winner": float(logit_details.get("entry_prob_a", 0.55) or 0.55) if winner_is_a else float(logit_details.get("entry_prob_b", 0.55) or 0.55),
            "entry_prob_loser": float(logit_details.get("entry_prob_b", 0.55) or 0.55) if winner_is_a else float(logit_details.get("entry_prob_a", 0.55) or 0.55),
        }
        method_probs = method_probabilities(winner_profile or {}, loser_profile or {}, winner_confidence, method_context)
        predicted_method = max(method_probs, key=method_probs.get)
        method_values.append(predicted_method)

        predictions[build_fight_key(fighter_a, fighter_b)] = {
            "winner": winner,
            "predicted_method": predicted_method,
            "predicted_method_probabilities": {
                "KO/TKO": round(method_probs.get("KO/TKO", 0.0), 6),
                "Submission": round(method_probs.get("Submission", 0.0), 6),
                "Decision": round(method_probs.get("Decision", 0.0), 6),
            },
            "probabilities": {
                fighter_a: prob_a,
                fighter_b: prob_b,
            },
            "explanation": build_explanation(
                fighter_a=fighter_a,
                fighter_b=fighter_b,
                weight_class_name=weight_class_name,
                prob_model_a=prob_model_a,
                prob_profile_a=prob_profile_a,
                prob_final_a=prob_a,
                profile_a=profile_a,
                profile_b=profile_b,
            ),
            "calibration": {
                "raw_model_prob_fighterA": round(prob_model_a_raw, 6),
                "calibrated_model_prob_fighterA": round(prob_model_a, 6),
                "matchup_correction": round(matchup_correction, 6),
                "uncertainty_factor": round(uncertainty_factor, 6),
                "dominant_path": dominant_path_name,
                "dominant_regime": dominant_regime,
                "dominant_path_logit": round(dominant_path_logit, 6),
                "secondary_logit": round(secondary_logit, 6),
                "interaction_logit": round(interaction_logit, 6),
                "striking_logit": round(float(logit_details.get("striking_logit", 0.0) or 0.0), 6),
                "grappling_logit": round(float(logit_details.get("grappling_logit", 0.0) or 0.0), 6),
                "submission_logit": round(float(logit_details.get("submission_logit", 0.0) or 0.0), 6),
                "effective_pressure_a_to_b": round(float(logit_details.get("effective_pressure_a_to_b", 0.0) or 0.0), 6),
                "effective_pressure_b_to_a": round(float(logit_details.get("effective_pressure_b_to_a", 0.0) or 0.0), 6),
            },
        }

        # Log prediction for tracking/calibration
        if TRACKING_ENABLED:
            try:
                log_prediction(
                    fighter_a=fighter_a,
                    fighter_b=fighter_b,
                    prob_a=prob_a,
                    prob_b=prob_b,
                    regime=dominant_regime,
                    weight_class=weight_class_name,
                    dom_logit=round(dominant_path_logit, 4),
                    interaction_logit=round(interaction_logit, 4),
                    round_win_logit=round(float(logit_details.get("round_win_logit", 0.0) or 0.0), 4)
                )
            except Exception as e:
                pass  # Silently skip tracking errors to not break prediction generation

    total_fights = len(probability_values)
    unique_probs = len(set(probability_values))
    unique_feature_signatures = len(set(feature_signatures))

    if total_fights >= 5 and unique_probs <= 2:
        raise RuntimeError(
            f"Sanity check failed: only {unique_probs} unique probabilities across {total_fights} fights."
        )

    if total_fights >= 5 and unique_feature_signatures <= 2:
        raise RuntimeError(
            f"Sanity check failed: feature signatures collapsed to {unique_feature_signatures} unique values."
        )

    if total_fights > 0 and missing_profile_fights == total_fights:
        raise RuntimeError("All fighter profile fetches failed; refusing to emit constant-like fallback predictions.")

    method_counts = Counter(method_values)
    warnings: list[str] = []
    if total_fights >= 6:
        top_method, top_count = method_counts.most_common(1)[0]
        if top_count / total_fights >= 0.85:
            warnings.append(
                f"Method concentration warning: {top_method} selected for {top_count}/{total_fights} fights."
            )

    predictions["__meta__"] = {
        "total_fights": total_fights,
        "unique_probabilities": unique_probs,
        "unique_feature_signatures": unique_feature_signatures,
        "missing_profile_fights": missing_profile_fights,
        "skipped_low_cage_time_fights": skipped_low_cage_time_fights,
        "min_ufc_cage_time_minutes": MIN_UFC_CAGE_TIME_MINUTES,
        "method_counts": dict(method_counts),
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(predictions, indent=2), encoding="utf-8")

    print(f"Wrote {len(predictions)} predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
