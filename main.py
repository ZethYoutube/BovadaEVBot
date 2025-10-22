"""
Main controller for the BovadaEVBot project.

Responsibilities:
- Load environment variables from .env
- Initialize EV engine and Bovada filter
- Schedule a daily job to compute and send top EV bets
- Initialize Telegram bot with basic commands: /bankroll, /stats, /settings
- Coordinate bankroll and results tracking

Config (env, with defaults):
- MIN_EDGE (float): minimum EV threshold for auto-picks. Default 0.1 (10%).
- TOP_BETS (int): number of bets to send daily. Default 3.
- SPORTS (csv): sports to scan, e.g.,
  "basketball_nba,americanfootball_nfl,baseball_mlb,soccer_epl,soccer_uefa_champs_league".

Fallback behavior:
- If fewer than TOP_BETS have EV >= MIN_EDGE, remaining slots are filled with next best EVs
  and tagged as fallback in the Telegram message.
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
from telegram import error as tg_error

from bovada_filter import BovadaFilter
from ev_engine import EVEngine
from bankroll_manager import BankrollManager
from results_tracker import ResultsTracker


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def format_bet_lines(bets: List[Dict[str, Any]]) -> str:
    if not bets:
        return ""
    lines: List[str] = []
    for i, bet in enumerate(bets, start=1):
        prefix = "⚠️ " if bet.get("fallback") else ""
        suffix = " (Low EV fallback)" if bet.get("fallback") else ""
        line = (
            f"{prefix}{i}. {bet.get('game','')} | {bet.get('market','')} | {bet.get('outcome','')}\n"
            f"   Bovada: {bet.get('bovada_odds')}  Fair: {round(bet.get('fair_odds',0), 2)}  EV: {round(bet.get('edge_pct',0), 2)}%{suffix}"
        )
        lines.append(line)
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
            await update.message.reply_text("🔍 Fetching odds from all sports...")
            
            # Try multiple sports
            all_games = []
            sports_csv = os.getenv(
                "SPORTS",
                "basketball_nba,americanfootball_nfl,baseball_mlb,soccer_epl,soccer_uefa_champs_league",
            )
            sports = [s.strip() for s in sports_csv.split(",") if s.strip()]

            books_csv = os.getenv("BOOKS", "bovada,bodog")
            books = [b.strip().lower() for b in books_csv.split(",") if b.strip()]
            
            for sport in sports:
                try:
                    games = engine.fetch_odds(sport=sport)
                    if games:
                        all_games.extend(games)
                        logger.info(f"Fetched {len(games)} games from {sport}")
                except Exception as e:
                    logger.warning(f"Failed to fetch {sport}: {e}")
                    continue
            
            logger.info(f"Total games fetched: {len(all_games)}")
            
            if not all_games:
                await update.message.reply_text("❌ No games found across all sports. Try again later.")
                return
                
            min_edge = float(os.getenv("MIN_EDGE", "0.1"))
            top_n = int(os.getenv("TOP_BETS", "3"))
            top_bets = engine.get_top_bets(all_games, n=top_n, min_edge=min_edge, bookmaker_aliases=books)

            # cache last selection for placement
            context.application.bot_data["last_top_bets"] = top_bets
            logger.info(f"Computed {len(top_bets)} candidate bets (min_edge={min_edge}, top_n={top_n}, books={books})")
            
            if not top_bets:
                await update.message.reply_text("❌ No EV opportunities found. Try again later.")
                return
                
            header = f"🧪 TEST EV OPPORTUNITIES (All Sports) — Top {top_n} (MIN_EDGE {min_edge*100:.1f}%)"
            message = header + "\n\n" + format_bet_lines(top_bets)
            await update.message.reply_text(message)
            
        except Exception as e:
            logger.error(f"Test EV command failed: {e}")
            await update.message.reply_text(f"❌ Test failed: {str(e)}")

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check bot status and basic info."""
        try:
            # Test API connection with all sports
            all_games = []
            sports_csv = os.getenv(
                "SPORTS",
                "basketball_nba,americanfootball_nfl,baseball_mlb,soccer_epl,soccer_uefa_champs_league",
            )
            sports = [s.strip() for s in sports_csv.split(",") if s.strip()]
            
            sport_counts = {}
            for sport in sports:
                try:
                    games = engine.fetch_odds(sport=sport)
                    if games:
                        all_games.extend(games)
                        sport_counts[sport] = len(games)
                except Exception as e:
                    sport_counts[sport] = 0
            
            status_msg = f"✅ Bot Status: Running\n"
            status_msg += f"📊 Total Games: {len(all_games)}\n"
            status_msg += f"💰 Bankroll: ${bankroll.current_bankroll:.2f}\n"
            status_msg += f"🎯 Starting Bankroll: ${bankroll.starting_bankroll:.2f}\n"
            status_msg += f"📈 ROI: {bankroll.get_summary()['roi_pct']:.2f}%\n\n"
            status_msg += f"🏆 Sports Coverage:\n"
            
            for sport, count in sport_counts.items():
                sport_name = sport.replace('_', ' ').title()
                status_msg += f"  {sport_name}: {count} games\n"
            
            await update.message.reply_text(status_msg)
        except Exception as e:
            await update.message.reply_text(f"❌ Status check failed: {str(e)}")

    async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Debug command to see what games and bookmakers are available."""
        try:
            await update.message.reply_text("🔍 Fetching debug info...")
            
            # Just test NBA first (fastest)
            games = engine.fetch_odds(sport="basketball_nba")
            
            if not games:
                await update.message.reply_text("❌ No NBA games found.")
                return
            
            debug_msg = f"🔍 DEBUG INFO (NBA Only):\n"
            debug_msg += f"📊 NBA Games: {len(games)}\n\n"
            
            # Show first game details
            if games:
                game = games[0]
                debug_msg += f"📋 Sample Game:\n"
                debug_msg += f"  Home: {game.get('home_team', 'Unknown')}\n"
                debug_msg += f"  Away: {game.get('away_team', 'Unknown')}\n"
                debug_msg += f"  Bookmakers: {len(game.get('bookmakers', []))}\n\n"
                
                # Show all bookmakers
                debug_msg += f"📚 Available Bookmakers:\n"
                bookmakers = []
                bovada_found = False
                
                for bookmaker in game.get('bookmakers', []):
                    bookmaker_name = bookmaker.get('title', '')
                    bookmakers.append(bookmaker_name)
                    if 'bovada' in bookmaker_name.lower():
                        bovada_found = True
                
                for bm in bookmakers[:8]:  # Show first 8
                    debug_msg += f"  • {bm}\n"
                
                if len(bookmakers) > 8:
                    debug_msg += f"  ... and {len(bookmakers) - 8} more\n"
                
                debug_msg += f"\n🎯 Bovada Found: {'✅' if bovada_found else '❌'}\n"
                
                if not bovada_found:
                    debug_msg += f"\n💡 Try searching for 'bodog' or other variations"
            
            await update.message.reply_text(debug_msg)
            
        except Exception as e:
            logger.error(f"Debug command failed: {e}")
            await update.message.reply_text(f"❌ Debug failed: {str(e)}")

    async def cmd_take_ev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Record/"take" the current EV picks (uses last /testev selection)."""
        try:
            min_edge = float(os.getenv("MIN_EDGE", "0.1"))
            top_n = int(os.getenv("TOP_BETS", "3"))

            bets: List[Dict[str, Any]] = context.application.bot_data.get("last_top_bets") or []
            if not bets:
                # If no cached bets, compute fresh similar to /testev
                all_games: List[Dict[str, Any]] = []
                sports_csv = os.getenv(
                    "SPORTS",
                    "basketball_nba,americanfootball_nfl,baseball_mlb,soccer_epl,soccer_uefa_champs_league",
                )
                sports = [s.strip() for s in sports_csv.split(",") if s.strip()]
                for sport in sports:
                    try:
                        games = engine.fetch_odds(sport=sport)
                        if games:
                            all_games.extend(games)
                    except Exception:
                        continue
                bets = engine.get_top_bets(all_games, n=top_n, min_edge=min_edge)

            if not bets:
                await update.message.reply_text("❌ No EV picks to take right now.")
                return

            # Limit to top_n
            bets = bets[:top_n]

            confirmations: List[str] = []
            for i, bet in enumerate(bets, start=1):
                stake = bankroll.recommend_stake(bet.get("ev", 0.0), bet.get("ev", 0.0))
                record = {
                    "game": bet.get("game"),
                    "market": bet.get("market"),
                    "outcome": bet.get("outcome"),
                    "odds": bet.get("bovada_odds"),
                    "fair_odds": bet.get("fair_odds"),
                    "ev": bet.get("ev"),
                    "stake": stake,
                    "status": "pending",
                }
                results.record_bet(record)
                mark = "⚠️ " if bet.get("fallback") else ""
                confirmations.append(
                    f"{mark}{i}. {bet.get('game')} | {bet.get('market')} | {bet.get('outcome')} — Stake: {stake}"
                )

            logger.info(f"Recorded {len(bets)} bets via /takeev")
            await update.message.reply_text("📝 Bets recorded:\n\n" + "\n".join(confirmations))
        except Exception as e:
            logger.error(f"/takeev failed: {e}")
            await update.message.reply_text(f"❌ Take EV failed: {str(e)}")

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("BovadaEVBot is running. Use /bankroll, /stats, /settings, /testev, /status, /debug, /takeev")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bankroll", cmd_bankroll))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("testev", cmd_test_ev))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("takeev", cmd_take_ev))

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
            # Read config
            min_edge = float(os.getenv("MIN_EDGE", "0.1"))
            top_n = int(os.getenv("TOP_BETS", "3"))
            sports_csv = os.getenv(
                "SPORTS",
                "basketball_nba,americanfootball_nfl,baseball_mlb,soccer_epl,soccer_uefa_champs_league",
            )
            sports = [s.strip() for s in sports_csv.split(",") if s.strip()]

            # Fetch from all sports
            all_games = []
            for sport in sports:
                try:
                    games = engine.fetch_odds(sport=sport)
                    if games:
                        all_games.extend(games)
                        logger.info(f"Daily job: Fetched {len(games)} games from {sport}")
                except Exception as e:
                    logger.warning(f"Daily job: Failed to fetch {sport}: {e}")
                    continue
            
            # Filter to same calendar day in configured timezone (default: US Eastern)
            from datetime import datetime
            import pytz

            tz_name = os.getenv("LOCAL_TZ", "US/Eastern")
            tz = pytz.timezone(tz_name)
            today = datetime.now(tz).date()

            def is_today(game: Dict[str, Any]) -> bool:
                ts = game.get("commence_time")
                if not ts:
                    return False
                try:
                    # ISO format from API
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    return dt.astimezone(tz).date() == today
                except Exception:
                    return False

            todays_games = [g for g in all_games if is_today(g)]

            top_bets = engine.get_top_bets(todays_games, n=top_n, min_edge=min_edge, bookmaker_aliases=[b.strip().lower() for b in os.getenv("BOOKS", "bovada,bodog").split(",") if b.strip()])

            if not top_bets:
                msg = "No odds available from provider right now. Try again later."
            else:
                header = f"🏆 Top {top_n} Bovada Picks (MIN_EDGE {min_edge*100:.1f}%)"
                bets_txt = format_bet_lines(top_bets)
                # Append bankroll summary line
                summary = bankroll.get_summary()
                bank_txt = (
                    f"\n\nBankroll — Start: {summary['starting']:.2f}  Current: {summary['current']:.2f}  ROI: {summary['roi_pct']:.2f}%"
                )
                msg = header + "\n\n" + bets_txt + bank_txt

            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if chat_id:
                await app.bot.send_message(chat_id=chat_id, text=msg)
            logger.info(f"Daily report sent: {len(top_bets)} opportunities found")
        except Exception as e:
            logger.error(f"Daily job failed: {e}")

    app = build_application(telegram_token, engine, results, bankroll)

    # Schedule job daily
    schedule.every().day.at(os.getenv("DAILY_TIME", "05:00")).do(lambda: asyncio.run(daily_job(app)))

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
    
    # Preflight: ensure single polling instance (exit if another instance is polling)
    async def _preflight_check() -> bool:
        try:
            # A quick get_updates will raise Conflict if another instance is polling
            await app.bot.get_updates(limit=1, timeout=1)
            return True
        except tg_error.Conflict:
            logger.error("Another bot instance is already polling. Exiting to avoid conflicts.")
            return False
        except Exception:
            # Non-conflict errors should not prevent startup
            return True

    if not asyncio.run(_preflight_check()):
        return

    # Run Telegram bot with error handling
    try:
        app.run_polling(drop_pending_updates=True, close_loop=False)
    except tg_error.Conflict:
        logger.error("Polling conflict detected at runtime. Exiting instance.")
        return
    except Exception as e:
        logger.error(f"Bot error: {e}")
        # Brief backoff then try once more
        time.sleep(5)
        try:
            app.run_polling(drop_pending_updates=True, close_loop=False)
        except tg_error.Conflict:
            logger.error("Polling conflict persists. Exiting instance.")
            return


if __name__ == "__main__":
    main()


