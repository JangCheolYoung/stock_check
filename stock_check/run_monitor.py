#!/usr/bin/env python3
import argparse


def main():
    parser = argparse.ArgumentParser(description="Run stock monitor crawler")
    parser.add_argument("--site", choices=["cultizm", "hyundai"], required=True)
    args = parser.parse_args()

    if args.site == "cultizm":
        from stock_check.crawlers.cultizm import CultizmCrawler

        crawler = CultizmCrawler()
    else:
        from stock_check.crawlers.hyundai import HyundaiCrawler

        crawler = HyundaiCrawler()

    crawler.run()


if __name__ == "__main__":
    main()
