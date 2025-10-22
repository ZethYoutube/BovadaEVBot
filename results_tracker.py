"""
Results tracking for BovadaEVBot.

This module records placed bets, outcomes, and performance metrics over time.
It can optionally connect to API-Sports to auto-mark wins/losses if an API key
is provided.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


class ResultsTracker:
    """Track bet results and summarize performance."""

    def __init__(self, file_path: str = "results.json", api_sports_key: Optional[str] = None) -> None:
        self.file_path = file_path
        self.api_sports_key = api_sports_key
        self._bets: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            self._save()
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._bets = list(data.get("bets", []))
        except Exception:
            self._save()

    def _save(self) -> None:
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump({"bets": self._bets}, f, indent=2)

    def record_bet(self, bet: Dict[str, Any]) -> None:
        """Record a bet with pending status.

        Expected keys: game, market, outcome, odds, stake, status
        """
        bet.setdefault("status", "pending")
        self._bets.append(bet)
        self._save()

    def mark_settlement(self, bet_index: int, result: str, profit: float) -> None:
        """Mark a bet as won/lost with profit amount (loss negative)."""
        if 0 <= bet_index < len(self._bets):
            self._bets[bet_index]["status"] = result
            self._bets[bet_index]["profit"] = profit
            self._save()

    def try_auto_update_results(self) -> None:
        """Attempt to auto update results using API-Sports if configured.

        Note: This is a placeholder that demonstrates structure. The actual
        endpoint and mapping between bets and API-Sports events must be
        implemented according to your subscribed sport and API plan.
        """
        if not self.api_sports_key:
            return
        # Placeholder: no-op. Implement lookup by event id and update statuses.
        return

    def summarize(self) -> Optional[Dict[str, Any]]:
        total_bets = len(self._bets)
        wins = sum(1 for b in self._bets if b.get("status") == "won")
        losses = sum(1 for b in self._bets if b.get("status") == "lost")
        net_profit = sum(float(b.get("profit", 0.0)) for b in self._bets)
        win_rate = (wins / total_bets) if total_bets else 0.0
        return {
            "total_bets": total_bets,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "net_profit": net_profit,
        }


