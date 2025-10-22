"""
Market filtering logic specific to Bovada for BovadaEVBot.

This module defines rules and heuristics for selecting relevant markets
from Bovada's offerings, cleaning and normalizing data, and discarding
unwanted or low-quality opportunities before EV evaluation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


class BovadaFilter:
    """Filter and normalize Bovada markets from TheOddsAPI data."""

    def __init__(self) -> None:
        """Initialize the Bovada filter."""
        self.logger = logging.getLogger(__name__)
        self.supported_markets = ["h2h", "spreads", "totals"]
        self.min_odds_threshold = -500  # Filter out extreme favorites
        self.max_odds_threshold = 500   # Filter out extreme underdogs

    def filter_markets(self, markets: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return a filtered list of markets suitable for EV calculations.
        
        Args:
            markets: Iterable of raw market dictionaries from TheOddsAPI.
            
        Returns:
            A list of cleaned and selected market dictionaries.
        """
        filtered_markets = []
        
        for market in markets:
            try:
                # Extract Bovada data from the market
                bovada_data = self._extract_bovada_data(market)
                if not bovada_data:
                    continue
                    
                # Normalize the odds data
                normalized_data = self._normalize_odds(bovada_data)
                if not normalized_data:
                    continue
                    
                # Apply quality filters
                if self._passes_quality_filters(normalized_data):
                    filtered_markets.append(normalized_data)
                    
            except Exception as e:
                self.logger.warning(f"Error processing market: {e}")
                continue
                
        self.logger.info(f"Filtered {len(filtered_markets)} markets from {len(list(markets))} total")
        return filtered_markets

    def _extract_bovada_data(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract Bovada bookmaker data from market.
        
        Args:
            market: Raw market data from TheOddsAPI.
            
        Returns:
            Dictionary with Bovada-specific data or None if not found.
        """
        bovada_bookmaker = None
        
        # Find Bovada in the bookmakers list
        for bookmaker in market.get("bookmakers", []):
            if bookmaker.get("title", "").lower() in ["bovada", "bodog"]:
                bovada_bookmaker = bookmaker
                break
                
        if not bovada_bookmaker:
            return None
            
        # Extract relevant market data
        bovada_data = {
            "game_id": market.get("id"),
            "sport_title": market.get("sport_title"),
            "home_team": market.get("home_team"),
            "away_team": market.get("away_team"),
            "commence_time": market.get("commence_time"),
            "bookmaker": bovada_bookmaker.get("title"),
            "last_update": bovada_bookmaker.get("last_update"),
            "markets": {}
        }
        
        # Extract supported market types
        for market_type in self.supported_markets:
            if market_type in bovada_bookmaker:
                bovada_data["markets"][market_type] = bovada_bookmaker[market_type]
                
        return bovada_data

    def _normalize_odds(self, bovada_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize Bovada odds into standard format.
        
        Args:
            bovada_data: Raw Bovada data dictionary.
            
        Returns:
            Normalized odds dictionary or None if invalid.
        """
        normalized = {
            "game_info": {
                "game_id": bovada_data.get("game_id"),
                "sport": bovada_data.get("sport_title"),
                "home_team": bovada_data.get("home_team"),
                "away_team": bovada_data.get("away_team"),
                "commence_time": bovada_data.get("commence_time"),
                "last_update": bovada_data.get("last_update")
            },
            "odds": {}
        }
        
        # Process each market type
        for market_type, market_data in bovada_data.get("markets", {}).items():
            if market_type == "h2h":
                normalized["odds"]["moneyline"] = self._normalize_moneyline(market_data)
            elif market_type == "spreads":
                normalized["odds"]["spreads"] = self._normalize_spreads(market_data)
            elif market_type == "totals":
                normalized["odds"]["totals"] = self._normalize_totals(market_data)
                
        # Only return if we have at least one valid market
        if normalized["odds"]:
            return normalized
        return None

    def _normalize_moneyline(self, market_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize moneyline odds data.
        
        Args:
            market_data: Raw moneyline market data.
            
        Returns:
            List of normalized moneyline odds.
        """
        normalized = []
        
        for outcome in market_data:
            odds = outcome.get("price", 0)
            if odds == 0:
                continue
                
            normalized.append({
                "team": outcome.get("description", ""),
                "odds": odds,
                "market_type": "moneyline"
            })
            
        return normalized

    def _normalize_spreads(self, market_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize spread odds data.
        
        Args:
            market_data: Raw spread market data.
            
        Returns:
            List of normalized spread odds.
        """
        normalized = []
        
        for outcome in market_data:
            odds = outcome.get("price", 0)
            point = outcome.get("point", 0)
            
            if odds == 0:
                continue
                
            normalized.append({
                "team": outcome.get("description", ""),
                "spread": point,
                "odds": odds,
                "market_type": "spread"
            })
            
        return normalized

    def _normalize_totals(self, market_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize totals (over/under) odds data.
        
        Args:
            market_data: Raw totals market data.
            
        Returns:
            List of normalized totals odds.
        """
        normalized = []
        
        for outcome in market_data:
            odds = outcome.get("price", 0)
            point = outcome.get("point", 0)
            
            if odds == 0:
                continue
                
            normalized.append({
                "side": outcome.get("description", ""),  # "Over" or "Under"
                "total": point,
                "odds": odds,
                "market_type": "total"
            })
            
        return normalized

    def _passes_quality_filters(self, normalized_data: Dict[str, Any]) -> bool:
        """Apply quality filters to normalized data.
        
        Args:
            normalized_data: Normalized odds dictionary.
            
        Returns:
            True if data passes all quality filters.
        """
        # Check if we have valid game info
        game_info = normalized_data.get("game_info", {})
        if not all([game_info.get("home_team"), game_info.get("away_team")]):
            return False
            
        # Check odds quality for each market
        for market_type, odds_list in normalized_data.get("odds", {}).items():
            if not odds_list:
                continue
                
            for odds_data in odds_list:
                odds = odds_data.get("odds", 0)
                
                # Filter extreme odds
                if odds < self.min_odds_threshold or odds > self.max_odds_threshold:
                    return False
                    
                # Check for valid odds values
                if odds == 0:
                    return False
                    
        return True

    def get_market_summary(self, filtered_markets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate summary statistics for filtered markets.
        
        Args:
            filtered_markets: List of filtered market data.
            
        Returns:
            Dictionary with summary statistics.
        """
        summary = {
            "total_markets": len(filtered_markets),
            "market_types": {},
            "sports": {},
            "avg_odds_range": {}
        }
        
        for market in filtered_markets:
            # Count market types
            for market_type in market.get("odds", {}):
                summary["market_types"][market_type] = summary["market_types"].get(market_type, 0) + 1
                
            # Count sports
            sport = market.get("game_info", {}).get("sport", "Unknown")
            summary["sports"][sport] = summary["sports"].get(sport, 0) + 1
            
        return summary


