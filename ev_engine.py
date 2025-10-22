"""
Expected Value (EV) computation engine for BovadaEVBot.

This module provides utilities to compute expected value for betting
markets, including odds normalization, implied probability estimation, and
stake sizing inputs for bankroll strategies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import requests


class EVEngine:
    """Compute expected value metrics for candidate bets."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initialize EV engine with TheOddsAPI key.
        
        Args:
            api_key: TheOddsAPI key for fetching odds data. Defaults to hardcoded key.
        """
        self.api_key = api_key or "f4a9cd21db6a47cf6cb8dd139d925243"
        self.base_url = "https://api.the-odds-api.com/v4"
        self.logger = logging.getLogger(__name__)

    def fetch_odds(self, sport: str = "basketball_nba", markets: str = "h2h,spreads,totals") -> List[Dict[str, Any]]:
        """Pull JSON odds from TheOddsAPI.
        
        Args:
            sport: Sport to fetch odds for (default: basketball_nba).
            markets: Comma-separated markets (h2h=moneyline, spreads, totals).
            
        Returns:
            List of game dictionaries with odds data.
            
        Raises:
            requests.RequestException: If API request fails.
            ValueError: If API key is missing or response is invalid.
        """
        if not self.api_key:
            raise ValueError("TheOddsAPI key is required")
            
        url = f"{self.base_url}/sports/{sport}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
            "daysFrom": 1,
        }
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not isinstance(data, list):
                raise ValueError("Invalid API response format")
                
            self.logger.info(f"Fetched {len(data)} games from TheOddsAPI")
            return data
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch odds: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error fetching odds: {e}")
            raise ValueError(f"Failed to process odds data: {e}")

    def calc_fair_line(self, game_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate fair probabilities from all books.
        
        - For h2h: compute per-outcome fair probability by averaging per-book normalized
          probabilities across all bookmakers for each outcome name (handles 2- and 3-way).
        - For spreads/totals: average the American odds directly across all outcomes.
        """
        fair_lines: Dict[str, Any] = {}

        # Moneyline (h2h): per outcome fair probability with de-vig per bookmaker
        outcome_prob_accum: Dict[str, List[float]] = {}
        for bookmaker in game_data.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                # Collect implied probs for this bookmaker
                book_probs: List[tuple[str, float]] = []
                for outcome in market.get("outcomes", []):
                    price = outcome.get("price")
                    name = outcome.get("name") or outcome.get("description") or ""
                    if isinstance(price, (int, float)) and name:
                        prob = self._american_to_prob(float(price))
                        if prob > 0:
                            book_probs.append((name, prob))
                if not book_probs:
                    continue
                # Normalize to remove this bookmaker's overround
                total_prob = sum(p for _, p in book_probs)
                if total_prob <= 0:
                    continue
                for name, p in book_probs:
                    norm_p = p / total_prob
                    outcome_prob_accum.setdefault(name, []).append(norm_p)

        if outcome_prob_accum:
            # Average normalized probs across books
            averaged: Dict[str, float] = {
                name: (sum(probs) / len(probs)) for name, probs in outcome_prob_accum.items() if probs
            }
            # Renormalize the averaged probabilities to ensure they sum to 1.0
            total_avg = sum(averaged.values())
            h2h_outcomes: Dict[str, Dict[str, float]] = {}
            if total_avg > 0:
                for name, p in averaged.items():
                    fair_prob = p / total_avg
                    h2h_outcomes[name] = {
                        "fair_prob": fair_prob,
                        "fair_odds": self._prob_to_american(fair_prob),
                    }
            if h2h_outcomes:
                fair_lines["h2h"] = {"outcomes": h2h_outcomes}

        # Spreads and Totals: average odds
        for market_type in ["spreads", "totals"]:
            prices: List[float] = []
            for bookmaker in game_data.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != market_type:
                        continue
                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price")
                        if isinstance(price, (int, float)):
                            prices.append(float(price))
            if prices:
                fair_lines[market_type] = {"fair_odds": sum(prices) / len(prices)}

        return fair_lines

    def calc_ev(self, bovada_odds: float, fair_odds: float) -> float:
        """Compute expected value for Bovada odds vs fair line.
        
        EV is computed per unit stake (e.g., $1):
        - fair_prob = implied probability from fair_odds
        - profit_per_unit = american odds profit for a $1 stake
        - EV = fair_prob * profit_per_unit - (1 - fair_prob) * 1
        """
        if not isinstance(bovada_odds, (int, float)) or not isinstance(fair_odds, (int, float)):
            return 0.0
        if bovada_odds == 0 or fair_odds == 0:
            return 0.0

        fair_prob = self._american_to_prob(fair_odds)
        if fair_prob <= 0 or fair_prob >= 1:
            return 0.0

        profit_per_unit = (bovada_odds / 100.0) if bovada_odds > 0 else (100.0 / abs(bovada_odds))
        ev = (fair_prob * profit_per_unit) - (1.0 - fair_prob)
        return ev

    def get_top_bets(
        self,
        games_data: List[Dict[str, Any]],
        n: int = 10,
        min_edge: float = 0.02,
        bookmaker_aliases: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return list of top bets using selection with fallback logic.
        
        Args:
            games_data: List of game dictionaries from fetch_odds().
            n: Maximum number of bets to return.
            min_edge: Minimum edge required (e.g., 0.02 for 2%).
            bookmaker_aliases: Optional list of lowercase substrings to match bookmaker title.
            
        Returns:
            List of bet dictionaries sorted by EV (highest first).
        """
        candidates: List[Dict[str, Any]] = []
        if not bookmaker_aliases:
            bookmaker_aliases = ["bovada", "bodog"]
        
        for game in games_data:
            fair_lines = self.calc_fair_line(game)
            
            for bookmaker in game.get("bookmakers", []):
                name = (bookmaker.get("title", "") or "").lower()
                if not any(alias in name for alias in bookmaker_aliases):
                    continue
                for market in bookmaker.get("markets", []):
                    mkey = market.get("key")
                    if mkey not in ["h2h", "spreads", "totals"]:
                        continue

                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price")
                        if not isinstance(price, (int, float)) or price == 0:
                            continue

                        # Determine fair_odds for this specific outcome/market
                        fair_odds_val: Optional[float] = None
                        if mkey == "h2h":
                            outcome_name = outcome.get("name") or outcome.get("description", "")
                            per_outcomes = fair_lines.get("h2h", {}).get("outcomes", {})
                            fair_info = per_outcomes.get(outcome_name)
                            if fair_info:
                                fair_odds_val = float(fair_info.get("fair_odds", 0))
                        else:
                            fair_odds_val = float(fair_lines.get(mkey, {}).get("fair_odds", 0))

                        if not fair_odds_val:
                            continue

                        bovada_odds = float(price)
                        ev = self.calc_ev(bovada_odds, fair_odds_val)

                        outcome_name = outcome.get("name") or outcome.get("description", "")
                        bet_info = {
                            "game": game.get("home_team", "") + " vs " + game.get("away_team", ""),
                            "market": mkey,
                            "outcome": outcome_name,
                            "bovada_odds": bovada_odds,
                            "fair_odds": fair_odds_val,
                            "ev": ev,
                            "edge_pct": ev * 100,
                            "desc": f"{mkey} | {outcome_name}",
                        }
                        candidates.append(bet_info)

        selected = select_top_bets(candidates, min_edge=min_edge, top_n=n)
        return selected

    def _american_to_prob(self, odds: float) -> float:
        """Convert American odds to probability."""
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return abs(odds) / (abs(odds) + 100)

    def _prob_to_american(self, prob: float) -> float:
        """Convert probability to American odds."""
        if prob >= 0.5:
            return (prob / (1 - prob)) * -100
        else:
            return (100 / prob) - 100


def select_top_bets(results: List[Dict[str, Any]], min_edge: float, top_n: int) -> List[Dict[str, Any]]:
    """
    results: list[dict] with at least keys {"ev": float, "desc": str, ...}
    Returns: list[dict] of length <= top_n.
    If >= top_n with ev >= min_edge -> return the best top_n.
    Else -> take all >= min_edge, then fill the rest with the next-best EVs.
    Mark any item with ev < min_edge as a fallback by setting item["fallback"] = True.
    Ensure the final list is sorted by ev DESC.
    """
    if not results:
        return []

    positive = [r for r in results if r.get("ev", 0) >= min_edge]
    positive.sort(key=lambda x: x["ev"], reverse=True)
    if len(positive) >= top_n:
        return positive[:top_n]

    all_sorted = sorted(results, key=lambda x: x.get("ev", 0), reverse=True)
    out: List[Dict[str, Any]] = positive[:]
    for r in all_sorted:
        if len(out) >= top_n:
            break
        if r not in out:
            if r.get("ev", 0) < min_edge:
                r = {**r, "fallback": True}
            out.append(r)

    try:
        print(f"Fallback used: only {len(positive)} >= MIN_EDGE={min_edge}")
    except Exception:
        pass

    out.sort(key=lambda x: x.get("ev", 0), reverse=True)
    return out

