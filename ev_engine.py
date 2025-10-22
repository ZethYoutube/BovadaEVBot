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
        
        Args:
            game_data: Game dictionary with bookmaker odds.
            
        Returns:
            Dictionary with fair lines for each market type.
        """
        fair_lines = {}
        
        for market_type in ["h2h", "spreads", "totals"]:
            # Collect all odds for this market from bookmakers[].markets[].outcomes[]
            all_odds: List[float] = []
            for bookmaker in game_data.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != market_type:
                        continue
                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price")
                        if isinstance(price, (int, float)):
                            all_odds.append(float(price))
            
            if not all_odds:
                continue
                
            # Calculate fair line (average of all odds)
            if market_type == "h2h":
                # For moneyline, average the implied probabilities
                probs = [self._american_to_prob(odds) for odds in all_odds if odds != 0]
                if probs:
                    fair_prob = sum(probs) / len(probs)
                    fair_lines[market_type] = {
                        "fair_prob": fair_prob,
                        "fair_odds": self._prob_to_american(fair_prob)
                    }
            else:
                # For spreads/totals, average the odds directly
                valid_odds = [odds for odds in all_odds if odds != 0]
                if valid_odds:
                    fair_odds = sum(valid_odds) / len(valid_odds)
                    fair_lines[market_type] = {"fair_odds": fair_odds}
        
        return fair_lines

    def calc_ev(self, bovada_odds: float, fair_odds: float) -> float:
        """Compute expected value for Bovada odds vs fair line.
        
        Args:
            bovada_odds: Bovada's odds in American format.
            fair_odds: Fair market odds in American format.
            
        Returns:
            Expected value as a decimal (e.g., 0.05 for 5% edge).
        """
        if bovada_odds == 0 or fair_odds == 0:
            return 0.0
            
        bovada_prob = self._american_to_prob(bovada_odds)
        fair_prob = self._american_to_prob(fair_odds)
        
        if fair_prob == 0:
            return 0.0
            
        # EV = (probability * payout) - (1 - probability)
        payout = bovada_odds if bovada_odds > 0 else (100 / abs(bovada_odds)) * 100
        ev = (bovada_prob * payout / 100) - (1 - bovada_prob)
        
        return ev

    def get_top_bets(self, games_data: List[Dict[str, Any]], n: int = 10, min_edge: float = 0.02) -> List[Dict[str, Any]]:
        """Return list of top bets using selection with fallback logic.
        
        Args:
            games_data: List of game dictionaries from fetch_odds().
            n: Maximum number of bets to return.
            min_edge: Minimum edge required (e.g., 0.02 for 2%).
            
        Returns:
            List of bet dictionaries sorted by EV (highest first).
        """
        candidates: List[Dict[str, Any]] = []
        
        for game in games_data:
            fair_lines = self.calc_fair_line(game)
            
            # Check Bovada/Bodog odds against fair lines using markets schema
            for bookmaker in game.get("bookmakers", []):
                name = bookmaker.get("title", "").lower()
                if not any(alias in name for alias in ("bovada", "bodog")):
                    continue

                for market in bookmaker.get("markets", []):
                    mkey = market.get("key")
                    if mkey not in ["h2h", "spreads", "totals"]:
                        continue
                    if mkey not in fair_lines:
                        continue

                    for outcome in market.get("outcomes", []):
                        bovada_odds = outcome.get("price", 0)
                        if not isinstance(bovada_odds, (int, float)) or bovada_odds == 0:
                            continue

                        fair_data = fair_lines[mkey]
                        fair_odds = fair_data.get("fair_odds", 0)
                        if not isinstance(fair_odds, (int, float)) or fair_odds == 0:
                            continue

                        ev = self.calc_ev(float(bovada_odds), float(fair_odds))

                        outcome_name = outcome.get("name") or outcome.get("description", "")
                        bet_info = {
                            "game": game.get("home_team", "") + " vs " + game.get("away_team", ""),
                            "market": mkey,
                            "outcome": outcome_name,
                            "bovada_odds": float(bovada_odds),
                            "fair_odds": float(fair_odds),
                            "ev": ev,
                            "edge_pct": ev * 100,
                            "desc": f"{mkey} | {outcome_name}",
                        }
                        candidates.append(bet_info)

        # Select final list with fallback logic
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

