"""
EEAT Checker — всі функції перевірок
"""
import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
    _TRAFILATURA = True
except ImportError:
    _TRAFILATURA = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

OK   = "✅"
FAIL = "❌"
WARN = "⚠️ Перевірте вручну"


# ─── УТИЛІТИ ──────────────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 15) -> object:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.content, "lxml")
    except Exception:
        return None


def same_domain(url1: str, url2: str) -> bool:
    return urlparse(url1).netloc == urlparse(url2).netloc


def get_internal_links(soup: BeautifulSoup, base_url: str) -> dict:
    """Повертає {ключ: абсолютний_url} де ключ = текст посилання або шлях."""
    result = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.lower().startswith("javascript"):
            continue
        abs_href = urljoin(base_url, href)
        if same_domain(base_url, abs_href):
            text = a.get_text(strip=True).lower()
            if text:
                result[text] = abs_href
            path = urlparse(abs_href).path.lower()
            result[path] = abs_href
    return result


def find_page_url(links: dict, keywords: list) -> object:
    for kw in keywords:
        for key, url in links.items():
            if kw in key:
                return url
    return None


def get_schemas(soup: BeautifulSoup) -> list:
    schemas = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or script.get_text()
            data = json.loads(raw)
            if isinstance(data, list):
                schemas.extend(data)
            elif isinstance(data, dict):
                if "@graph" in data:
                    schemas.extend(data["@graph"])
                else:
                    schemas.append(data)
        except Exception:
            pass
    return schemas


def has_schema_type(schemas: list, *types: str) -> bool:
    for s in schemas:
        t = s.get("@type", "")
        t_list = t if isinstance(t, list) else [t]
        for desired in types:
            if desired in t_list:
                return True
    return False


def txt(soup_or_str) -> str:
    if isinstance(soup_or_str, str):
        return soup_or_str.lower()
    return (soup_or_str.get_text() if soup_or_str else "").lower()


def has_text(soup_or_str, *keywords: str) -> bool:
    t = txt(soup_or_str)
    return any(kw.lower() in t for kw in keywords)


def find_class_or_id(soup: BeautifulSoup, pattern: str) -> bool:
    rx = re.compile(pattern, re.I)
    return bool(soup.find(class_=rx) or soup.find(id=rx))


# ─── TRAFILATURA: НАДІЙНИЙ ЕКСТРАКТОР КОНТЕНТУ СТАТТІ ─────────────────────────

def _article_extract(soup: BeautifulSoup, base_url: str = "") -> tuple:
    """
    Повертає (article_text: str, content_links: list[str]).
    Використовує trafilatura для виокремлення тільки тіла статті —
    без хедера, футера, навігації, реклами.
    При відсутності trafilatura — fallback на BeautifulSoup.
    """
    if not soup:
        return "", []

    if _TRAFILATURA:
        try:
            html_str = str(soup)
            # XML-формат дозволяє витягнути і текст, і посилання всередині статті
            xml = trafilatura.extract(
                html_str,
                include_links=True,
                include_tables=True,
                output_format="xml",
                no_fallback=False,
                url=base_url or None,
            )
            if xml:
                xs = BeautifulSoup(xml, "lxml-xml")
                text = xs.get_text(separator=" ")
                links = [
                    r.get("target", "")
                    for r in xs.find_all("ref")
                    if r.get("target", "").startswith("http")
                ]
                return text, links

            # Якщо XML не вийшов — plain text
            text = trafilatura.extract(html_str) or ""
            return text, []
        except Exception:
            pass

    # Fallback: пробуємо знайти тіло статті через BeautifulSoup
    container = (
        soup.find("article")
        or soup.find(class_=re.compile(
            r"content|post|article-body|entry-content|news-detail|news-content", re.I
        ))
        or soup.find("main")
        or soup
    )
    text = container.get_text(separator=" ")
    links = [
        a["href"] for a in container.find_all("a", href=True)
        if a["href"].startswith("http")
    ]
    return text, links


# ─── ОКРЕМІ ПЕРЕВІРКИ ─────────────────────────────────────────────────────────

def chk_https(url: str) -> str:
    return OK if url.startswith("https://") else FAIL


def chk_email(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if soup.find("a", href=re.compile(r"^mailto:", re.I)):
        return OK
    if re.search(r"[\w.+-]+@[\w-]+\.\w{2,}", soup.get_text()):
        return OK
    return FAIL


def chk_phone(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if soup.find("a", href=re.compile(r"^tel:", re.I)):
        return OK
    if re.search(r"[\+\(]?\d[\d\s\-\(\)]{7,}\d", soup.get_text()):
        return OK
    return FAIL


def chk_address(soup: BeautifulSoup, schemas: list) -> str:
    if not soup:
        return FAIL
    for s in schemas:
        if "address" in s:
            return OK
    if has_text(soup, "вул.", "вулиця", "проспект", "пров.", "street", "адреса офісу"):
        return OK
    return FAIL


def chk_social_links(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    social = [
        "facebook.com", "instagram.com", "linkedin.com", "twitter.com",
        "youtube.com", "tiktok.com", "t.me", "telegram.", "x.com",
    ]
    for a in soup.find_all("a", href=True):
        if any(s in a["href"].lower() for s in social):
            return OK
    return FAIL


def chk_author(soup: BeautifulSoup, schemas: list) -> str:
    if not soup:
        return FAIL

    # 1. Schema.org — найнадійніший сигнал
    for s in schemas:
        if s.get("@type") in ("Article", "BlogPosting", "NewsArticle") and "author" in s:
            return OK

    # 2. rel="author" або meta name="author" з непустим змістом
    if soup.find(attrs={"rel": "author"}):
        return OK
    meta = soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)})
    if meta and len(meta.get("content", "").strip()) > 2:
        return OK

    # 3. Trafilatura витягує тільки тіло статті — шукаємо явні маркери авторства в ньому
    article_text, _ = _article_extract(soup)
    if article_text:
        if re.search(r'\b(автор|author)\s*:', article_text, re.I):
            return OK
        # "Написав/написала Ім'я" — конкретне ім'я після слова
        if re.search(r'\b(написав|написала|написали)\s+[А-ЯІЇЄҐA-Z]', article_text):
            return OK
        # "By FirstName LastName" — стандартний англійський byline
        if re.search(r'\bBy\s+[A-Z][a-z]+\s+[A-Z][a-z]+', article_text):
            return OK

    return FAIL


def chk_date_published(soup: BeautifulSoup, schemas: list) -> str:
    if not soup:
        return FAIL
    for s in schemas:
        if "datePublished" in s:
            return OK
    if soup.find("meta", property="article:published_time"):
        return OK
    if soup.find("time", attrs={"datetime": True}):
        return OK
    if has_text(soup, "опубліковано", "published", "дата публікації"):
        return OK
    return FAIL


def chk_date_modified(soup: BeautifulSoup, schemas: list) -> str:
    if not soup:
        return FAIL
    for s in schemas:
        if "dateModified" in s:
            return OK
    if soup.find("meta", property="article:modified_time"):
        return OK
    if has_text(soup, "оновлено", "updated", "дата оновлення", "last updated"):
        return OK
    return FAIL


_SOCIAL_AND_TRACKING = {
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "linkedin.com", "tiktok.com", "t.me", "telegram.",
    "pinterest.com", "vk.com", "ok.ru",
    "googletagmanager.com", "google-analytics.com", "mc.yandex.ru",
    "pixel.facebook.com", "doubleclick.net", "googlesyndication.com",
}


def chk_external_links(soup: BeautifulSoup, base_url: str) -> str:
    if not soup:
        return FAIL
    base_domain = urlparse(base_url).netloc

    # Trafilatura повертає тільки посилання всередині тіла статті —
    # без соцмереж у футері, без рекламних блоків, без навігації
    _, content_links = _article_extract(soup, base_url)

    authoritative = [
        link for link in content_links
        if base_domain not in link
        and not any(s in link.lower() for s in _SOCIAL_AND_TRACKING)
    ]

    if len(authoritative) >= 3:
        return OK
    if len(authoritative) >= 1:
        return WARN
    return FAIL


def chk_toc(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for cls in ["toc", "table-of-contents", "article-toc", "post-toc", "contents"]:
        if find_class_or_id(soup, cls):
            return OK
    return FAIL


def chk_references(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "список літератури", "джерела", "references", "бібліографія", "bibliography"):
        return OK
    return FAIL


def chk_images(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    article = soup.find("article") or soup.find(
        class_=re.compile(r"content|post|article-body|entry-content", re.I)
    )
    container = article if article else soup
    imgs = [
        img for img in container.find_all("img", src=True)
        if not any(x in " ".join(img.get("class", [])).lower() for x in ["logo", "icon", "avatar"])
    ]
    return OK if imgs else FAIL


def chk_comments(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for indicator in ["comment", "disqus", "коментар", "respond", "discussion"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_tags(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for indicator in [r"\btag", r"теги", r"tag-cloud"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_reading_progress(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for indicator in ["reading-progress", "scroll-progress", "progress-bar", "read-progress"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_cookie(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for indicator in ["cookie", "gdpr", "consent"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_reviews(soup: BeautifulSoup, schemas: list) -> str:
    if not soup:
        return FAIL
    for s in schemas:
        if s.get("@type") in ("Review", "AggregateRating"):
            return OK
    for indicator in [r"\breview", "відгук", r"\brating", "testimonial"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_trustpilot(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for a in soup.find_all("a", href=True):
        if any(x in a["href"].lower() for x in ["trustpilot", "bbb.org", "clutch.co", "google.com/maps"]):
            return OK
    if has_text(soup, "trustpilot", "clutch", "BBB"):
        return OK
    return FAIL


def chk_licenses(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "ліцензія", "сертифікат", "license", "certificate", "акредитація"):
        return OK
    return FAIL


def chk_editor(soup: BeautifulSoup, schemas: list) -> str:
    if not soup:
        return FAIL
    for s in schemas:
        if "editor" in s or "reviewedBy" in s:
            return OK
    if has_text(soup, "редактор", "editor", "reviewed by", "перевірено редактором"):
        return OK
    for cls in ["editor", "reviewed-by", "fact-check", "reviewer"]:
        if find_class_or_id(soup, cls):
            return OK
    return FAIL


def chk_article_views(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for indicator in [r"\bviews", "view-count", "переглядів", "перегляди"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_article_rating_widget(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for indicator in [r"\brate\b", r"\bvote\b", "оцінити", "star-rating"]:
        if find_class_or_id(soup, indicator):
            return OK
    return FAIL


def chk_page_exists(links: dict, keywords: list) -> str:
    return OK if find_page_url(links, keywords) else FAIL


def chk_categories(soup: BeautifulSoup, links: dict) -> str:
    for kw in ["категорії", "розділи", "category", "categories"]:
        for key in links:
            if kw in key:
                return OK
    for cls in ["categories", "category", "sections"]:
        if soup and find_class_or_id(soup, cls):
            return OK
    return FAIL


def chk_disclaimer(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "відмова від відповідальності", "disclaimer", "не є медичною порадою"):
        return OK
    if find_class_or_id(soup, "disclaimer"):
        return OK
    return FAIL


def chk_callback(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "зворотній дзвінок", "передзвонити", "callback"):
        return OK
    return FAIL


def chk_online_chat(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    page_str = str(soup).lower()
    for indicator in ["jivosite", "jivochat", "intercom", "livechat", "tawk.to", "crisp.chat", "freshchat"]:
        if indicator in page_str:
            return OK
    return FAIL


def chk_search(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if soup.find("input", attrs={"type": "search"}):
        return OK
    if soup.find("form", attrs={"role": "search"}):
        return OK
    if find_class_or_id(soup, r"\bsearch\b"):
        return OK
    return FAIL


def chk_newsletter(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "розсилку", "newsletter", "підписатися", "subscribe"):
        return OK
    return FAIL


def chk_author_education(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "освіта", "education", "університет", "university", "диплом", "degree", "навчання"):
        return OK
    return FAIL


def chk_author_experience(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "досвід", "experience", "стаж", "years of experience", "працює"):
        return OK
    return FAIL


def chk_org_mission(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "місія", "mission", "цінності", "values", "наша мета"):
        return OK
    return FAIL


def chk_org_age(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "заснована", "founded", "since", "рік заснування"):
        return OK
    if re.search(r"(з|since|from|заснован[аоі]?)\s+\d{4}", soup.get_text(), re.I):
        return OK
    return FAIL


def chk_reg_docs(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    if has_text(soup, "свідоцтво", "реквізити", "ЄДРПОУ", "ІПН", "registration number"):
        return OK
    return FAIL


def chk_team_photos(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    for cls in ["team", "staff", "команда"]:
        section = soup.find(class_=re.compile(cls, re.I))
        if section and section.find("img"):
            return OK
    return FAIL


def chk_form(soup: BeautifulSoup) -> str:
    if not soup:
        return FAIL
    return OK if soup.find("form") else FAIL


# ─── EVIDENCE: що саме знайшов чекер ──────────────────────────────────────────

def get_evidence(factor: str, soup: BeautifulSoup, schemas: list, base_url: str = "") -> str:
    """
    Повертає короткий фрагмент — що саме знайдено для цього фактора.
    Використовується для колонки «Приклад» щоб верифікувати результати вручну.
    """
    if not soup:
        return ""
    try:
        if factor == "HTTPS":
            return base_url[:80]

        elif factor == "Електронна адреса":
            a = soup.find("a", href=re.compile(r"^mailto:", re.I))
            if a:
                return a["href"].replace("mailto:", "")[:80]
            m = re.search(r"[\w.+-]+@[\w-]+\.\w{2,}", soup.get_text())
            return m.group()[:80] if m else ""

        elif factor == "Номер телефону":
            a = soup.find("a", href=re.compile(r"^tel:", re.I))
            if a:
                return a["href"].replace("tel:", "")[:60]
            m = re.search(r"[\+\(]?\d[\d\s\-\(\)]{7,}\d", soup.get_text())
            return m.group().strip()[:60] if m else ""

        elif factor == "Фізична адреса":
            for s in schemas:
                addr = s.get("address", {})
                if addr:
                    parts = [addr.get("streetAddress", ""), addr.get("addressLocality", "")]
                    joined = ", ".join(p for p in parts if p)
                    if joined:
                        return joined[:100]
            m = re.search(r"(вул\.|вулиця|проспект|пров\.|street)[^\n]{5,80}", soup.get_text(), re.I)
            return m.group(0).strip()[:100] if m else ""

        elif factor == "Зазначено автора":
            for s in schemas:
                if s.get("@type") in ("Article", "BlogPosting", "NewsArticle") and "author" in s:
                    auth = s["author"]
                    if isinstance(auth, list):
                        auth = auth[0]
                    name = auth.get("name", "") if isinstance(auth, dict) else str(auth)
                    return f"JSON-LD author: {name}"[:100]
            text, _ = _article_extract(soup)
            m = re.search(r"(автор|author)\s*[:\-]\s*(.{3,60})", text, re.I)
            return m.group(0).strip()[:100] if m else ""

        elif factor == "Дата публікації":
            for s in schemas:
                if "datePublished" in s:
                    return f"JSON-LD datePublished: {s['datePublished']}"[:80]
            meta = soup.find("meta", property="article:published_time")
            if meta:
                return f"meta article:published_time: {meta.get('content','')}"[:80]
            t = soup.find("time", attrs={"datetime": True})
            if t:
                return f"<time datetime=\"{t['datetime']}\">"[:80]

        elif factor == "Дата оновлення контенту":
            for s in schemas:
                if "dateModified" in s:
                    return f"JSON-LD dateModified: {s['dateModified']}"[:80]
            meta = soup.find("meta", property="article:modified_time")
            if meta:
                return f"meta article:modified_time: {meta.get('content','')}"[:80]

        elif factor == "Посилання на авторитетні ресурси у статті":
            _, links = _article_extract(soup, base_url)
            bd = urlparse(base_url).netloc
            auth = [l for l in links if bd not in l and not any(s in l.lower() for s in _SOCIAL_AND_TRACKING)]
            return ", ".join(auth[:3])[:150] if auth else ""

        elif factor == "Посилання на соцмережі":
            social_domains = ["facebook.com", "instagram.com", "linkedin.com",
                              "twitter.com", "x.com", "youtube.com", "tiktok.com", "t.me"]
            found = []
            for a in soup.find_all("a", href=True):
                for sd in social_domains:
                    if sd in a["href"].lower() and sd not in found:
                        found.append(a["href"][:60])
                        break
            return "; ".join(found[:3])[:150] if found else ""

        elif factor in ("Organization", "BreadcrumbList", "Article / BlogPosting",
                        "FAQPage", "Product", "Person", "LocalBusiness",
                        "MedicalWebPage", "MedicalClinic", "MedicalCondition"):
            type_map = {
                "Organization":           ["Organization"],
                "BreadcrumbList":         ["BreadcrumbList"],
                "Article / BlogPosting":  ["Article", "BlogPosting", "NewsArticle"],
                "FAQPage":                ["FAQPage"],
                "Product":                ["Product"],
                "Person":                 ["Person"],
                "LocalBusiness":          ["LocalBusiness"],
                "MedicalWebPage":         ["MedicalWebPage"],
                "MedicalClinic":          ["MedicalClinic"],
                "MedicalCondition":       ["MedicalCondition"],
            }
            for s in schemas:
                t = s.get("@type", "")
                t_list = t if isinstance(t, list) else [t]
                for desired in type_map.get(factor, []):
                    if desired in t_list:
                        name = s.get("name", s.get("headline", s.get("@id", "")))
                        return f"JSON-LD: {desired}" + (f" «{str(name)[:40]}»" if name else "")

        elif factor == "Редактор / рецензент матеріалу":
            for s in schemas:
                if "editor" in s:
                    ed = s["editor"]
                    name = ed.get("name", str(ed)) if isinstance(ed, dict) else str(ed)
                    return f"JSON-LD editor: {name}"[:80]
                if "reviewedBy" in s:
                    rb = s["reviewedBy"]
                    name = rb.get("name", str(rb)) if isinstance(rb, dict) else str(rb)
                    return f"JSON-LD reviewedBy: {name}"[:80]
            m = re.search(r"(редактор|editor|reviewed by|перевірено)[^\n]{3,60}", soup.get_text(), re.I)
            return m.group(0).strip()[:100] if m else ""

        elif factor == "Зазначено освіту":
            m = re.search(r"(університет|university|освіта|education|диплом|degree)[^\n]{5,80}",
                          soup.get_text(), re.I)
            return m.group(0).strip()[:100] if m else ""

        elif factor == "Зазначено досвід роботи":
            m = re.search(r"(досвід|experience|стаж)[^\n\d]{0,5}\d+[^\n]{0,40}",
                          soup.get_text(), re.I)
            return m.group(0).strip()[:100] if m else ""

        elif factor == "Плашка про куки":
            for indicator in ["cookie", "gdpr", "consent"]:
                el = soup.find(class_=re.compile(indicator, re.I)) or soup.find(id=re.compile(indicator, re.I))
                if el:
                    return f"Елемент: class/id «{indicator}»"[:80]

        elif factor == "Онлайн-консультант":
            page_str = str(soup).lower()
            for service in ["jivosite", "jivochat", "intercom", "livechat", "tawk.to", "crisp.chat", "freshchat"]:
                if service in page_str:
                    return f"Скрипт: {service}"

        elif factor == "Пошук по сайту":
            if soup.find("input", attrs={"type": "search"}):
                return "<input type=\"search\">"
            if soup.find("form", attrs={"role": "search"}):
                return "<form role=\"search\">"

        elif factor in ("Сторінка існує", "Контактна сторінка"):
            return "(сторінка знайдена за посиланням)"

    except Exception:
        pass
    return ""
