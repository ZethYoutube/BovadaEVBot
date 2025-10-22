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
            if market_type not in game_data:
                continue
                
            market_data = game_data[market_type]
            if not market_data:
                continue
                
            # Collect all odds for this market
            all_odds = []
            for bookmaker in game_data.get("bookmakers", []):
                if market_type in bookmaker:
                    for outcome in bookmaker[market_type]:
                        all_odds.append(outcome.get("price", 0))
            
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
        """Return sorted list of top n bets above min_edge.
        
        Args:
            games_data: List of game dictionaries from fetch_odds().
            n: Maximum number of bets to return.
            min_edge: Minimum edge required (e.g., 0.02 for 2%).
            
        Returns:
            List of bet dictionaries sorted by EV (highest first).
        """
        top_bets = []
        
        for game in games_data:
            fair_lines = self.calc_fair_line(game)
            
            # Check Bovada odds against fair lines
            for bookmaker in game.get("bookmakers", []):
                if bookmaker.get("title", "").lower() != "bovada":
                    continue
                    
                for market_type in ["h2h", "spreads", "totals"]:
                    if market_type not in bookmaker or market_type not in fair_lines:
                        continue
                        
                    for outcome in bookmaker[market_type]:
                        bovada_odds = outcome.get("price", 0)
                        if bovada_odds == 0:
                            continue
                            
                        fair_data = fair_lines[market_type]
                        if market_type == "h2h":
                            fair_odds = fair_data.get("fair_odds", 0)
                        else:
                            fair_odds = fair_data.get("fair_odds", 0)
                            
                        if fair_odds == 0:
                            continue
                            
                        ev = self.calc_ev(bovada_odds, fair_odds)
                        
                        if ev >= min_edge:
                            bet_info = {
                                "game": game.get("home_team", "") + " vs " + game.get("away_team", ""),
                                "market": market_type,
                                "outcome": outcome.get("description", ""),
                                "bovada_odds": bovada_odds,
                                "fair_odds": fair_odds,
                                "ev": ev,
                                "edge_pct": ev * 100
                            }
                            top_bets.append(bet_info)
        
        # Sort by EV (highest first) and return top n
        top_bets.sort(key=lambda x: x["ev"], reverse=True)
        return top_bets[:n]

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


