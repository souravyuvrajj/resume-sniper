import asyncio
from pathlib import Path

import crawl4ai
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode


async def _scrape_async(url: str) -> str:
    config = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED,       # always fresh
        wait_until="networkidle",            # wait for JS to finish
        page_timeout=30000,
        markdown_generator=crawl4ai.DefaultMarkdownGenerator(
            options={"ignore_links": True}   # cleaner text, no noise from hrefs
        ),
    )
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, config=config)

    if not result.success:
        raise RuntimeError(
            f"Crawl4AI failed for {url}: {result.error_message}\n"
            "Tip: save the JD to a .txt file and use --jd-file jd.txt"
        )

    text = result.markdown.strip()
    if len(text) < 100:
        raise RuntimeError(
            f"Crawl4AI returned only {len(text)} chars — page may require login or block bots.\n"
            "Tip: save the JD to a .txt file and use --jd-file jd.txt"
        )
    return text


def scrape_job(url: str) -> str:
    """Scrape a job posting URL using Crawl4AI (handles JS/SPA pages)."""
    return asyncio.run(_scrape_async(url))


def load_jd_file(path: str) -> str:
    """Load job description from a local text file."""
    return Path(path).read_text(encoding="utf-8")
