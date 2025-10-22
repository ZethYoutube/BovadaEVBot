"""
Main controller for the BovadaEVBot project.

Responsibilities:
- Load environment variables from .env
- Initialize EV engine and Bovada filter
- Schedule a daily job to compute and send top EV bets
- Initialize Telegram bot with basic commands: /bankroll, /stats, /settings
- Coordinate bankroll and results tracking
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List

import schedule
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from bovada_filter import BovadaFilter
from ev_engine import EVEngine
from bankroll_manager import BankrollManager
from results_tracker import ResultsTracker


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def format_bets_message(bets: List[Dict[str, Any]]) -> str:
    if not bets:
        return "No qualifying EV bets found today."
    lines: List[str] = ["Top EV Bets:"]
    for i, bet in enumerate(bets, start=1):
        lines.append(
            f"{i}. {bet.get('game','')} | {bet.get('market','')} | {bet.get('outcome','')}\n"
            f"   Bovada: {bet.get('bovada_odds')}  Fair: {round(bet.get('fair_odds',0), 2)}  EV: {round(bet.get('edge_pct',0), 2)}%"
        )
    return "\n".join(lines)


def schedule_loop() -> None:
    while True:
        schedule.run_pending()
        time.sleep(30)


def build_application(token: str, engine: EVEngine, results: ResultsTracker, bankroll: BankrollManager) -> Application:
    app = ApplicationBuilder().token(token).build()

    async def cmd_bankroll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        summary = bankroll.get_summary()
        msg = (
            f"Bankroll:\n"
            f"- Starting: {summary['starting']:.2f}\n"
            f"- Current: {summary['current']:.2f}\n"
            f"- ROI: {summary['roi_pct']:.2f}%\n"
            f"- Bets Placed: {summary['bets_placed']}"
        )
        await update.message.reply_text(msg)

    async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        stats = results.summarize() or {}
        msg = (
            "Stats:\n"
            f"- Total Bets: {stats.get('total_bets', 0)}\n"
            f"- Wins: {stats.get('wins', 0)}  Losses: {stats.get('losses', 0)}\n"
            f"- Win Rate: {round(stats.get('win_rate', 0.0)*100, 2)}%\n"
            f"- Net Profit: {round(stats.get('net_profit', 0.0), 2)}"
        )
        await update.message.reply_text(msg)

    async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = {
            "ENV": os.getenv("ENV", "development"),
            "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        }
        await update.message.reply_text("Settings:\n" + json.dumps(settings, indent=2))

    async def cmd_test_ev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Test EV calculation with current odds."""
        try:
            games = engine.fetch_odds()
            top_bets = engine.get_top_bets(games, n=5, min_edge=0.01)  # Lower threshold for testing
            
            if not top_bets:
                await update.message.reply_text("No EV opportunities found. Try again later when games are available.")
                return
                
            message = "🧪 TEST EV OPPORTUNITIES:\n\n" + format_bets_message(top_bets)
            await update.message.reply_text(message)
            
        except Exception as e:
            await update.message.reply_text(f"Test failed: {str(e)}")

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("BovadaEVBot is running. Use /bankroll, /stats, /settings, /testev")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bankroll", cmd_bankroll))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("testev", cmd_test_ev))

    return app


def main() -> None:
    load_dotenv()

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN") or "7597027433:AAG0jmlwieLJ8T8gKiEUnA4EF6TsKeSyguA"
    if not telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required in environment")

    odds_api_key = os.getenv("THEODDS_API_KEY")
    if not odds_api_key:
        logger.warning("THEODDS_API_KEY missing; using default key")

    # Initialize core components
    engine = EVEngine(api_key=odds_api_key)
    bovada_filter = BovadaFilter()
    bankroll = BankrollManager(starting_bankroll=float(os.getenv("STARTING_BANKROLL", "20") or 20))
    results = ResultsTracker()

    # Daily job: fetch odds, compute top 3 bets, send to Telegram (if chat id configured)
    async def daily_job(app: Application) -> None:
        try:
            games = engine.fetch_odds()
            # Optional: use filter if needed in future; current EV uses raw games
            # filtered = bovada_filter.filter_markets(games)
            top_bets = engine.get_top_bets(games, n=3, min_edge=0.02)
            message = format_bets_message(top_bets)

            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if chat_id:
                await app.bot.send_message(chat_id=chat_id, text=message)
            logger.info(message)
        except Exception as e:
            logger.error(f"Daily job failed: {e}")

    app = build_application(telegram_token, engine, results, bankroll)

    # Schedule job daily
    schedule.every().day.at(os.getenv("DAILY_TIME", "09:00")).do(lambda: asyncio.run(daily_job(app)))

    # Start schedule loop in background
    threading.Thread(target=schedule_loop, daemon=True).start()

    # Run Telegram bot (blocking)
    port = int(os.getenv("PORT", 8000))
    
    # Start HTTP server for Render health checks
    import http.server
    import socketserver
    
    def start_http_server():
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
            print(f"HTTP server started on port {port}")
            httpd.serve_forever()
    
    # Start HTTP server in background
    threading.Thread(target=start_http_server, daemon=True).start()
    
    # Keep-alive ping every 10 minutes to prevent sleep
    def keep_alive():
        import requests
        while True:
            time.sleep(600)  # 10 minutes
            try:
                requests.get(f"http://localhost:{port}", timeout=5)
            except:
                pass
    
    threading.Thread(target=keep_alive, daemon=True).start()
    
    # Run Telegram bot
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()


