"""
Company enrichment: extract the company URL from the JD, then scrape
homepage + common sub-pages to build context beyond what the JD says.

All failures degrade gracefully — if scraping is blocked or the URL
can't be found, returns "" and the pipeline continues with JD-only.
"""

import asyncio
import contextlib
import io
import logging
import time
from urllib.parse import urlparse

import crawl4ai
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

# Suppress crawl4ai's verbose internal logging
logging.getLogger("crawl4ai").setLevel(logging.ERROR)

# Pages to try after the homepage. Tried in parallel; any that 404 or block are skipped.
_ENRICHMENT_PATHS = ["/about", "/values", "/culture", "/careers", "/engineering"]

# Cap per page so we don't bloat the research prompt
_CHARS_PER_PAGE = 3000

_URL_SYSTEM = "You are a URL extractor. Return only a base URL or the single word 'none'."
_URL_PROMPT = """Find the official website URL for the company in this job description.
First look for an explicit URL in the text. If none is present, use your knowledge
of the company to infer the correct website (e.g. 'Maruti Suzuki' → https://www.marutisuzuki.com).
Return ONLY the base URL (e.g. https://company.com) with no path, no trailing slash.
If you genuinely don't know the company, return the single word 'none'.

JOB DESCRIPTION:
{jd_text}"""


def extract_company_url(jd_text: str) -> str | None:
    """Ask haiku to pull the company base URL out of the JD text."""
    from llm import call_raw
    
    prompt = f"{_URL_SYSTEM}\n\n{_URL_PROMPT.format(jd_text=jd_text[:6000])}"
    
    for attempt in range(2):
        try:
            url = call_raw(prompt, model="haiku", timeout=30).strip().strip('"').strip("'")
            if url.lower() == "none" or not url.startswith("http"):
                return None
            # Normalise: keep scheme + netloc only
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            if attempt == 0:
                time.sleep(2)
            else:
                return None
    return None


async def _scrape_many_async(urls: list[str]) -> list[str]:
    """Scrape multiple URLs in parallel. Silently skips failures."""
    config = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED,
        wait_until="networkidle",
        page_timeout=15000,
        markdown_generator=crawl4ai.DefaultMarkdownGenerator(
            options={"ignore_links": True}
        ),
    )
    texts = []
    devnull = io.StringIO()
    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        async with AsyncWebCrawler(verbose=False) as crawler:
            tasks = [crawler.arun(url=url, config=config) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            continue
        if getattr(res, "success", False) and len(getattr(res, "markdown", "").strip()) >= 100:
            texts.append(getattr(res, "markdown", "").strip()[:_CHARS_PER_PAGE])
    return texts


def scrape_company_pages(base_url: str) -> str:
    """
    Scrape homepage + common sub-pages in parallel.
    Returns concatenated markdown text, capped per page.
    Returns "" if everything fails.
    """
    urls = [base_url] + [base_url + path for path in _ENRICHMENT_PATHS]
    try:
        texts = asyncio.run(_scrape_many_async(urls))
    except Exception:
        return ""
    return "\n\n---\n\n".join(texts)


def _ddg_find_url(jd_text: str) -> str | None:
    """DuckDuckGo fallback: extract company name then search for their website."""
    try:
        from duckduckgo_search import DDGS

        # Extract company name with haiku (much simpler task than finding a URL)
        name_prompt = (
            "Extract the company name from this job description. "
            "Return ONLY the company name, nothing else.\n\nJOB DESCRIPTION:\n"
            + jd_text[:3000]
        )
        from llm import call_raw
        
        try:
            result_text = call_raw(name_prompt, model="haiku", timeout=20)
            class MockResult:
                returncode = 0
                stdout = result_text
            result = MockResult()
        except Exception:
            class MockResult:
                returncode = 1
                stdout = ""
            result = MockResult()
        if result.returncode != 0:
            return None
        company_name = result.stdout.strip()
        if not company_name or len(company_name) > 80:
            return None

        with DDGS() as ddgs:
            hits = list(ddgs.text(f"{company_name} official website", max_results=3))
        for hit in hits:
            href = hit.get("href", "")
            if href.startswith("http") and "duckduckgo" not in href:
                parsed = urlparse(href)
                return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return None


def enrich_company(jd_text: str) -> str:
    """
    Full enrichment pipeline: extract URL → scrape pages → return context text.
    Falls back to DuckDuckGo search if haiku can't find/infer the URL.
    Returns "" at any failure so the caller can degrade gracefully.
    """
    url = extract_company_url(jd_text)
    if not url:
        url = _ddg_find_url(jd_text)
    if not url:
        return ""
    context = scrape_company_pages(url)
    return context
