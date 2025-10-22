"""
Bankroll management for BovadaEVBot.

This module handles reading and writing bankroll data to `bankroll.json`,
recommending stake sizes (placeholder), and computing ROI metrics.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional


class BankrollManager:
    """Manage bankroll persistence and ROI calculations."""

    def __init__(self, starting_bankroll: Optional[float] = None, file_path: str = "bankroll.json") -> None:
        self.file_path = file_path
        self.starting_bankroll = starting_bankroll if starting_bankroll is not None else 0.0
        self.current_bankroll = self.starting_bankroll
        self.bets_placed = 0
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            self._save()
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.starting_bankroll = float(data.get("starting", self.starting_bankroll))
                self.current_bankroll = float(data.get("current", self.current_bankroll))
                self.bets_placed = int(data.get("bets_placed", 0))
        except Exception:
            # On error, keep in-memory defaults and rewrite file
            self._save()

    def _save(self) -> None:
        data = {
            "starting": self.starting_bankroll,
            "current": self.current_bankroll,
            "bets_placed": self.bets_placed,
        }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_summary(self) -> Dict[str, float]:
        roi = 0.0
        if self.starting_bankroll:
            roi = (self.current_bankroll - self.starting_bankroll) / self.starting_bankroll * 100.0
        return {
            "starting": self.starting_bankroll,
            "current": self.current_bankroll,
            "roi_pct": roi,
            "bets_placed": self.bets_placed,
        }

    def record_result(self, stake: float, net_return: float) -> None:
        """Update bankroll after a bet settles.

        Args:
            stake: Amount staked on the bet.
            net_return: Net return including stake (e.g., +10 profit -> +10; loss -> -stake)
        """
        self.current_bankroll += net_return
        self.bets_placed += 1
        self._save()

    def recommend_stake(self, ev: float, edge: float) -> float:
        """Simple placeholder staking: 1% of current bankroll if edge >= 2%."""
        if edge < 0.02 or self.current_bankroll <= 0:
            return 0.0
        return round(max(0.0, 0.01 * self.current_bankroll), 2)


