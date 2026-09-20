"""Radar de stock Pokémon 30 ans sur lagranderecre.fr.

  python radar.py                 boucle continue
  python radar.py --once          un seul passage (cron, CI), code de sortie 0
  python radar.py --test-notif    envoie une notification de test
  python radar.py --serve         boucle + interface web temps réel
  python radar.py --mock          rejoue les fixtures, aucun appel au site (à combiner)
  python radar.py --fallback      force le repli Playwright pour les fiches
  python radar.py --check-url U   diagnostic : affiche ce que le parseur lit sur une fiche
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from radarlib.config import ConfigError, load_config
from radarlib.core import Radar
from radarlib.fetch import Fetcher, FixtureFetcher
from radarlib.notify import Notifier
from radarlib.state import RadarState

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"


def setup_logging(log_file: Path, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s : %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_radar(args: argparse.Namespace) -> Radar:
    config = load_config(use_browser=args.fallback)
    setup_logging(config.log_file, args.verbose)
    log = logging.getLogger("radar")
    if args.mock:
        log.warning("MODE MOCK : fixtures rejouées, aucun appel au site, notifications journalisées seulement")
        config = replace(config, state_file=Path("state.mock.json"))  # ne pollue pas l'état réel
        fetcher = FixtureFetcher(FIXTURES_DIR)
    else:
        fetcher = Fetcher(min_gap_seconds=config.api_min_gap_seconds)
    browser = None
    if args.fallback and not args.mock:
        from radarlib.browser import BrowserFetcher

        browser = BrowserFetcher(debug_dir=Path("debug"))
    notifier = Notifier(config.ntfy_server, config.ntfy_topic, dry_run=args.mock)
    if not notifier.configured:
        log.warning("NTFY_TOPIC absent dans .env : le radar tourne mais n'enverra rien")
    state = RadarState.load(config.state_file)
    return Radar(config=config, fetcher=fetcher, notifier=notifier, state=state, browser=browser)


def cmd_check_url(url: str, use_browser: bool) -> int:
    from radarlib.parse import parse_product_page

    if use_browser:
        from radarlib.browser import BrowserFetcher

        info = BrowserFetcher(debug_dir=Path("debug")).get_product(url)
    else:
        info = parse_product_page(Fetcher().get_html(url))
    status = "DISPONIBLE_WEB" if info.buyable else "INDISPONIBLE (web)"
    print(f"{status}  {info.price_label}  {info.title}\n  sku={info.sku} threshold={info.web_threshold} bouton_desactive={info.buy_button_disabled}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="un seul passage puis sortie")
    parser.add_argument("--test-notif", action="store_true", help="envoie une notification de test")
    parser.add_argument("--serve", action="store_true", help="boucle + interface web")
    parser.add_argument("--mock", action="store_true", help="rejoue les fixtures, aucun appel au site")
    parser.add_argument("--fallback", action="store_true", help="force le repli Playwright")
    parser.add_argument("--check-url", metavar="URL", help="diagnostic sur une fiche")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.check_url:
            return cmd_check_url(args.check_url, args.fallback)
        radar = build_radar(args)
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    if args.test_notif:
        ok = radar.notifier.notify_test()
        print("Notification de test envoyée." if ok else "Échec : vérifie NTFY_TOPIC dans .env et ta connexion.")
        return 0 if ok else 1

    if args.once:
        radar.run_cycle()
        print(radar.summary())
        return 0

    if args.serve:
        from radarlib.server import serve

        serve(radar)
        return 0

    try:
        radar.run_forever()
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
