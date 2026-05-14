import requests
from bs4 import BeautifulSoup


def fetch_metadata(
    url: str,
    timeout: int = 5,
    max_bytes: int = 2_000_000,
    max_summary_len: int = 300,
) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ReadingList/0.1)"}
        r = requests.get(url, timeout=timeout, headers=headers, stream=True)
        r.raise_for_status()

        content = b""
        for chunk in r.iter_content(chunk_size=8192):
            content += chunk
            if len(content) > max_bytes:
                break

        soup = BeautifulSoup(content, "html.parser")
        title = _extract_title(soup) or url
        summary = _extract_summary(soup, max_summary_len) or ""
        return {"title": title.strip(), "summary": summary.strip()}
    except Exception as e:
        return {"title": url, "summary": "", "error": str(e)}


def _extract_title(soup):
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"]
    tw = soup.find("meta", attrs={"name": "twitter:title"})
    if tw and tw.get("content"):
        return tw["content"]
    if soup.title and soup.title.string:
        return soup.title.string
    return None


def _extract_summary(soup, max_len: int = 300):
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return _truncate(og["content"], max_len)
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        return _truncate(desc["content"], max_len)
    tw = soup.find("meta", attrs={"name": "twitter:description"})
    if tw and tw.get("content"):
        return _truncate(tw["content"], max_len)
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    for p in soup.find_all("p"):
        text = p.get_text().strip()
        if len(text) > 50:
            return _truncate(text, max_len)
    body = soup.find("body")
    if body:
        text = " ".join(body.get_text().split())
        if text:
            return _truncate(text, max_len)
    return None


def _truncate(s: str, max_len: int) -> str:
    s = " ".join(s.split())
    if len(s) <= max_len:
        return s
    return s[:max_len].rsplit(" ", 1)[0] + "…"
