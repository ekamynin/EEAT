"""
Crawler — знаходить і перевіряє всі EEAT-релевантні сторінки сайту.
Покриває всі типи сайтів з чекліста.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

from checkers import (
    fetch, get_schemas, same_domain,
    chk_author_education, chk_author_experience, chk_social_links,
    chk_licenses, chk_author, chk_date_published, chk_date_modified,
    chk_external_links, chk_editor, chk_toc, chk_references,
    chk_images, chk_disclaimer, chk_email, chk_phone, chk_address,
    chk_reviews, chk_trustpilot, chk_org_mission, chk_org_age,
    chk_reg_docs, chk_team_photos, chk_form, chk_social_links,
    chk_newsletter, chk_search, chk_cookie, chk_page_exists,
    get_internal_links, has_text,
    OK, FAIL, WARN,
)

# ─── ПАТТЕРНИ URL ДЛЯ ВИЗНАЧЕННЯ ТИПУ СТОРІНКИ ───────────────────────────────

PAGE_PATTERNS = {

    # Загальні сторінки (всі типи сайтів)
    "about": [
        r"/about", r"/about-us", r"/про-нас", r"/про-компан",
        r"/o-nas", r"/company", r"/компан", r"/history",
        r"/pro-nas", r"/about_us",
    ],
    "contact": [
        r"/contact", r"/contacts", r"/контакт", r"/зв.яжіться",
        r"/reach-us", r"/get-in-touch", r"/contact-us",
    ],
    "team": [
        r"/team", r"/команда", r"/editorial", r"/редакц",
        r"/staff", r"/our-team", r"/about/team",
    ],

    # Автори (блоги, послуги, медицина)
    "author": [
        r"/author/", r"/authors/", r"/writer/", r"/contributor/",
        r"/про-автора/", r"/редактор/", r"/editor/",
    ],

    # Лікарі / спеціалісти (медицина)
    "doctor": [
        r"/doctor/", r"/doctors/", r"/лікар/", r"/лікарі/",
        r"/physician/", r"/specialist/", r"/спеціаліст/",
        r"/expert/", r"/konsultant/", r"/medic/",
    ],

    # Статті / блог / новини
    "article": [
        r"/blog/", r"/news/", r"/article/", r"/articles/",
        r"/блог/", r"/новин", r"/стат", r"/post/", r"/posts/",
        r"/publication/", r"/матеріал/", r"/content/",
    ],

    # Товари (e-commerce)
    "product": [
        r"/product/", r"/products/", r"/товар/",
        r"/item/", r"/goods/", r"/p/[a-z0-9\-]+",
    ],

    # Препарати (медицина)
    "drug": [
        r"/drug/", r"/drugs/", r"/препарат/", r"/ліки/",
        r"/medication/", r"/medicine/", r"/liky/",
    ],

    # Категорії ліків (медицина)
    "drug_category": [
        r"/category/", r"/kategoriya/", r"/категор/ліки",
        r"/drugs/category", r"/medikaments/",
    ],

    # Важливі інформаційні сторінки
    "policy_privacy": [
        r"/privacy", r"/конфіденційність", r"/privacy-policy",
    ],
    "policy_terms": [
        r"/terms", r"/ugoda", r"/оферта", r"/публічна",
        r"/user-agreement",
    ],
    "policy_editorial": [
        r"/editorial", r"/редакційна", r"/editorial-policy",
        r"/about/editorial",
    ],
    "delivery": [
        r"/delivery", r"/shipping", r"/доставка", r"/доставк",
    ],
    "payment": [
        r"/payment", r"/оплата", r"/pay", r"/oplata",
    ],
    "return_guarantee": [
        r"/return", r"/повернення", r"/guarantee", r"/гарантія",
        r"/refund",
    ],
    "faq": [
        r"/faq", r"/frequently-asked", r"/питання",
        r"/zapytannya", r"/questions",
    ],
    "licenses": [
        r"/license", r"/ліцензі", r"/certificate", r"/сертифікат",
        r"/accreditation", r"/акредитац",
    ],
    "reviews_page": [
        r"/review", r"/відгук", r"/testimonial", r"/feedback",
    ],
}

# ─── МІТКИ ДЛЯ UI ─────────────────────────────────────────────────────────────

PAGE_TYPE_LABELS = {
    "about":            "🏢 Сторінка «Про компанію»",
    "contact":          "📞 Контактна сторінка",
    "team":             "👥 Команда / Редакція",
    "author":           "✍️ Сторінки авторів",
    "doctor":           "🩺 Сторінки лікарів",
    "article":          "📝 Статті / блог",
    "product":          "🛒 Картки товарів",
    "drug":             "💊 Картки препаратів",
    "drug_category":    "📂 Категорії препаратів",
    "policy_privacy":   "🔒 Політика конфіденційності",
    "policy_terms":     "📄 Угода користувача / Оферта",
    "policy_editorial": "📰 Редакційна політика",
    "delivery":         "🚚 Доставка",
    "payment":          "💳 Оплата",
    "return_guarantee": "↩️ Повернення / Гарантії",
    "faq":              "❓ FAQ",
    "licenses":         "🏅 Ліцензії та сертифікати",
    "reviews_page":     "⭐ Відгуки",
}

# ─── ПЕРЕВІРКИ ПО ТИПАХ СТОРІНОК ─────────────────────────────────────────────

def _has_photo(soup):
    if not soup: return FAIL
    for cls in ["photo", "avatar", "img-author", "profile-img", "team-photo",
                "doctor-photo", "person-photo", "author-photo"]:
        if soup.find(class_=re.compile(cls, re.I)): return OK
    return FAIL

def _has_bio(soup):
    if not soup: return FAIL
    if has_text(soup, "біографія", "biography", "про автора", "about the author",
                "про себе", "про нас", "about me"): return OK
    for cls in ["bio", "author-bio", "author-description", "about-author"]:
        if soup.find(class_=re.compile(cls, re.I)): return OK
    return FAIL

def _has_publication_list(soup):
    if not soup: return FAIL
    for cls in ["author-posts", "author-articles", "publications",
                "latest-posts", "written-by", "articles-by"]:
        if soup.find(class_=re.compile(cls, re.I)): return OK
    if has_text(soup, "публікацій", "статей автора", "articles by", "written by"): return OK
    return FAIL

def _has_specialty(soup):
    if not soup: return FAIL
    if has_text(soup, "спеціальність", "спеціалізація", "specialty",
                "кардіолог", "терапевт", "хірург", "педіатр", "невролог",
                "дерматолог", "онколог", "ортопед"): return OK
    return FAIL

def _has_price(soup):
    if not soup: return FAIL
    for cls in ["price", "ціна", "cost", "amount"]:
        if soup.find(class_=re.compile(cls, re.I)): return OK
    if soup.find(attrs={"itemprop": "price"}): return OK
    return FAIL

def _has_buy_button(soup):
    if not soup: return FAIL
    if has_text(soup, "купити", "buy", "додати в кошик", "add to cart",
                "замовити", "order now"): return OK
    for cls in ["buy", "add-to-cart", "btn-buy", "purchase"]:
        if soup.find(class_=re.compile(cls, re.I)): return OK
    return FAIL

def _has_product_status(soup):
    if not soup: return FAIL
    if has_text(soup, "в наявності", "немає в наявності", "in stock",
                "out of stock", "available", "наявність"): return OK
    return FAIL

def _has_instructions(soup):
    if not soup: return FAIL
    if has_text(soup, "інструкція", "instruction", "спосіб застосування",
                "dosage", "дозування", "показання"): return OK
    return FAIL

def _has_alternatives(soup):
    if not soup: return FAIL
    if has_text(soup, "аналоги", "analogy", "alternatives", "замінники",
                "схожі препарати"): return OK
    for cls in ["analogs", "alternatives", "similar"]:
        if soup.find(class_=re.compile(cls, re.I)): return OK
    return FAIL

def _has_content_block(soup):
    """Чи є змістовний текстовий блок (сторінка заповнена)."""
    if not soup: return FAIL
    text = soup.get_text(strip=True)
    return OK if len(text) > 300 else FAIL

def _has_map(soup):
    if not soup: return FAIL
    page_str = str(soup).lower()
    if any(x in page_str for x in ["google.com/maps", "maps.app", "openstreetmap", "iframe"]): return OK
    return FAIL

def _has_editorial_standards(soup):
    if not soup: return FAIL
    if has_text(soup, "стандарти", "standards", "принципи", "principles",
                "редакційні вимоги", "editorial guidelines",
                "перевірка фактів", "fact-check"): return OK
    return FAIL

def _has_correction_policy(soup):
    if not soup: return FAIL
    if has_text(soup, "виправлення", "correction", "помилк", "спростування",
                "corrections policy"): return OK
    return FAIL

PAGE_CHECKS = {

    "about": [
        ("Сторінка заповнена (є текст)",        lambda s, sc, u: _has_content_block(s)),
        ("Місія / цінності",                    lambda s, sc, u: chk_org_mission(s)),
        ("Вік / дата заснування",               lambda s, sc, u: chk_org_age(s)),
        ("Реквізити / свідоцтво",               lambda s, sc, u: chk_reg_docs(s)),
        ("Фотографії команди",                  lambda s, sc, u: chk_team_photos(s)),
        ("Посилання на соцмережі",              lambda s, sc, u: chk_social_links(s)),
        ("Нагороди та досягнення",              lambda s, sc, u:
            OK if s and has_text(s, "нагород", "award", "перемог", "визнан") else FAIL),
    ],

    "contact": [
        ("Електронна адреса",                   lambda s, sc, u: chk_email(s)),
        ("Номер телефону",                      lambda s, sc, u: chk_phone(s)),
        ("Фізична адреса",                      lambda s, sc, u: chk_address(s, sc)),
        ("Форма зворотного зв'язку",            lambda s, sc, u: chk_form(s)),
        ("Посилання на соцмережі",              lambda s, sc, u: chk_social_links(s)),
        ("Схема проїзду / карта",               lambda s, sc, u: _has_map(s)),
        ("Контакти керівника",                  lambda s, sc, u:
            OK if s and has_text(s, "директор", "ceo", "керівник", "founder") else FAIL),
    ],

    "team": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Фотографії членів команди",           lambda s, sc, u: chk_team_photos(s)),
        ("Посади зазначені",                    lambda s, sc, u:
            OK if s and has_text(s, "редактор", "editor", "автор", "директор",
                                 "менеджер", "manager", "засновник") else FAIL),
        ("Посилання на соцмережі",              lambda s, sc, u: chk_social_links(s)),
    ],

    "author": [
        ("Фото автора",                         lambda s, sc, u: _has_photo(s)),
        ("Біографія / опис",                    lambda s, sc, u: _has_bio(s)),
        ("Зазначено освіту",                    lambda s, sc, u: chk_author_education(s)),
        ("Зазначено досвід роботи",             lambda s, sc, u: chk_author_experience(s)),
        ("Посилання на соцмережі",              lambda s, sc, u: chk_social_links(s)),
        ("Список публікацій автора",            lambda s, sc, u: _has_publication_list(s)),
    ],

    "doctor": [
        ("Фото лікаря",                         lambda s, sc, u: _has_photo(s)),
        ("Спеціальність лікаря",                lambda s, sc, u: _has_specialty(s)),
        ("Медична освіта",                      lambda s, sc, u: chk_author_education(s)),
        ("Стаж роботи",                         lambda s, sc, u: chk_author_experience(s)),
        ("Ліцензії та сертифікати",             lambda s, sc, u: chk_licenses(s)),
        ("Посилання на соцмережі",              lambda s, sc, u: chk_social_links(s)),
    ],

    "article": [
        ("Зазначено автора",                    lambda s, sc, u: chk_author(s, sc)),
        ("Дата публікації",                     lambda s, sc, u: chk_date_published(s, sc)),
        ("Дата оновлення",                      lambda s, sc, u: chk_date_modified(s, sc)),
        ("Редактор / рецензент",                lambda s, sc, u: chk_editor(s, sc)),
        ("Зовнішні посилання (джерела)",        lambda s, sc, u: chk_external_links(s, u)),
        ("Зміст статті (ToC)",                  lambda s, sc, u: chk_toc(s)),
        ("Список літератури",                   lambda s, sc, u: chk_references(s)),
        ("Зображення у статті",                 lambda s, sc, u: chk_images(s)),
        ("Відмова від відповідальності",        lambda s, sc, u: chk_disclaimer(s)),
    ],

    "product": [
        ("Ціна товару",                         lambda s, sc, u: _has_price(s)),
        ("Кнопка «Купити» / «В кошик»",        lambda s, sc, u: _has_buy_button(s)),
        ("Статус наявності",                    lambda s, sc, u: _has_product_status(s)),
        ("Зображення товару",                   lambda s, sc, u: chk_images(s)),
        ("Відгуки / рейтинг",                   lambda s, sc, u: chk_reviews(s, sc)),
        ("Дата оновлення",                      lambda s, sc, u: chk_date_modified(s, sc)),
    ],

    "drug": [
        ("Зазначено автора / рецензента",       lambda s, sc, u: chk_author(s, sc)),
        ("Редактор / лікар-рецензент",          lambda s, sc, u: chk_editor(s, sc)),
        ("Дата публікації",                     lambda s, sc, u: chk_date_published(s, sc)),
        ("Дата оновлення",                      lambda s, sc, u: chk_date_modified(s, sc)),
        ("Інструкція / спосіб застосування",    lambda s, sc, u: _has_instructions(s)),
        ("Список літератури",                   lambda s, sc, u: chk_references(s)),
        ("Відмова від відповідальності",        lambda s, sc, u: chk_disclaimer(s)),
        ("Аналоги препарату",                   lambda s, sc, u: _has_alternatives(s)),
    ],

    "drug_category": [
        ("Зазначено автора / рецензента",       lambda s, sc, u: chk_author(s, sc)),
        ("Редактор / лікар-рецензент",          lambda s, sc, u: chk_editor(s, sc)),
        ("Дата оновлення",                      lambda s, sc, u: chk_date_modified(s, sc)),
        ("Список літератури",                   lambda s, sc, u: chk_references(s)),
        ("Відмова від відповідальності",        lambda s, sc, u: chk_disclaimer(s)),
    ],

    "policy_privacy": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Є опис що збирається",                lambda s, sc, u:
            OK if s and has_text(s, "збираємо", "collect", "персональні дані",
                                 "personal data", "cookies") else FAIL),
        ("Є контакт для запитів",               lambda s, sc, u: chk_email(s)),
    ],

    "policy_terms": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Є умови використання",                lambda s, sc, u:
            OK if s and has_text(s, "умови", "terms", "угода", "agreement",
                                 "зобов'язання", "obligations") else FAIL),
    ],

    "policy_editorial": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Стандарти редакції",                  lambda s, sc, u: _has_editorial_standards(s)),
        ("Політика виправлень",                 lambda s, sc, u: _has_correction_policy(s)),
        ("Хто пише контент",                    lambda s, sc, u:
            OK if s and has_text(s, "автори", "редактори", "фахівці",
                                 "experts", "journalists") else FAIL),
    ],

    "delivery": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Терміни доставки",                    lambda s, sc, u:
            OK if s and has_text(s, "днів", "days", "термін", "строк", "delivery time") else FAIL),
        ("Вартість доставки",                   lambda s, sc, u:
            OK if s and has_text(s, "безкоштовна", "free", "вартість", "грн", "price") else FAIL),
        ("Способи доставки",                    lambda s, sc, u:
            OK if s and has_text(s, "нова пошта", "укрпошта", "кур'єр",
                                 "courier", "pickup", "самовивіз") else FAIL),
    ],

    "payment": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Способи оплати описані",              lambda s, sc, u:
            OK if s and has_text(s, "visa", "mastercard", "liqpay", "privat24",
                                 "картка", "card", "готівка", "cash") else FAIL),
        ("Безпека платежів",                    lambda s, sc, u:
            OK if s and has_text(s, "безпечн", "secure", "ssl", "захист") else FAIL),
    ],

    "return_guarantee": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Умови повернення описані",            lambda s, sc, u:
            OK if s and has_text(s, "повернення", "return", "refund",
                                 "обмін", "exchange") else FAIL),
        ("Терміни вказані",                     lambda s, sc, u:
            OK if s and has_text(s, "днів", "days", "термін", "строк") else FAIL),
    ],

    "faq": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Є питання та відповіді",              lambda s, sc, u:
            OK if s and (
                s.find(class_=re.compile(r"faq|accordion|question|answer", re.I)) or
                has_text(s, "питання", "question", "відповідь", "answer")
            ) else FAIL),
    ],

    "licenses": [
        ("Сторінка заповнена",                  lambda s, sc, u: _has_content_block(s)),
        ("Зображення ліцензій / сертифікатів",  lambda s, sc, u:
            OK if s and s.find("img") and has_text(s, "ліцензія", "сертифікат",
                                                   "license", "certificate") else FAIL),
        ("Номер або дата ліцензії",             lambda s, sc, u:
            OK if s and re.search(r"\d{4,}", s.get_text()) else FAIL),
    ],

    "reviews_page": [
        ("Є відгуки на сторінці",               lambda s, sc, u: chk_reviews(s, sc)),
        ("Є оцінки / зірки",                    lambda s, sc, u:
            OK if s and s.find(class_=re.compile(r"star|rating|score", re.I)) else FAIL),
        ("Є відповіді на відгуки",              lambda s, sc, u:
            OK if s and has_text(s, "відповідь", "reply", "response", "відповіли") else FAIL),
    ],
}

# ─── ЯКІ ТИПИ ШУКАТИ ДЛЯ КОЖНОГО ТИПУ САЙТУ ─────────────────────────────────

SITE_TYPE_PAGES = {
    "E-commerce": [
        "about", "contact", "team",
        "author", "article",
        "product",
        "policy_privacy", "policy_terms",
        "delivery", "payment", "return_guarantee",
        "faq", "reviews_page",
    ],
    "Сайти послуг": [
        "about", "contact", "team",
        "author", "article",
        "policy_privacy", "policy_terms", "policy_editorial",
        "delivery", "payment", "return_guarantee",
        "faq", "licenses", "reviews_page",
    ],
    "Блоги / сайти новин": [
        "about", "contact", "team",
        "author", "article",
        "policy_privacy", "policy_editorial",
        "faq",
    ],
    "Аптеки / медицина": [
        "about", "contact", "team",
        "author", "doctor",
        "article", "drug", "drug_category",
        "policy_privacy", "policy_terms", "policy_editorial",
        "delivery", "payment", "return_guarantee",
        "faq", "licenses", "reviews_page",
    ],
}


# ─── ВИЗНАЧЕННЯ ТИПУ СТОРІНКИ ─────────────────────────────────────────────────

def detect_page_type(url: str, types_to_scan: list) -> object:
    url_lower = url.lower()
    for page_type in types_to_scan:
        for pattern in PAGE_PATTERNS.get(page_type, []):
            if re.search(pattern, url_lower):
                return page_type
    return None


# ─── ЗБІР URL ─────────────────────────────────────────────────────────────────

def collect_urls(
    base_url: str,
    main_soup: BeautifulSoup,
    types_to_scan: list,
    max_per_type: int = 30,
) -> dict:
    """
    BFS-краулінг 2 рівнів.
    Повертає {page_type: [url, ...]}
    """
    found       = {t: set() for t in types_to_scan}
    visited     = set()
    base_domain = urlparse(base_url).netloc

    # Збираємо всі посилання з головної сторінки
    queue = []
    for a in main_soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.lower().startswith("javascript"):
            continue
        abs_url = urljoin(base_url, href)
        if urlparse(abs_url).netloc == base_domain:
            queue.append(abs_url)

    # Перший рівень — посилання з головної
    second_level = []
    for url in queue:
        clean = url.split("?")[0].split("#")[0]
        if clean in visited:
            continue
        visited.add(clean)

        page_type = detect_page_type(clean, types_to_scan)
        if page_type and len(found[page_type]) < max_per_type:
            found[page_type].add(clean)
        else:
            # Зберігаємо для другого рівня якщо не підійшло
            second_level.append(clean)

    # Другий рівень — заходимо на сторінки де мало що знайшли
    types_need_more = [t for t in types_to_scan if len(found[t]) < 3]
    if types_need_more:
        pages_to_dive = second_level[:20]  # не більше 20 сторінок для 2-го рівня
        for page_url in pages_to_dive:
            page_soup = fetch(page_url)
            if not page_soup:
                continue
            for a in page_soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith("#") or href.lower().startswith("javascript"):
                    continue
                abs_url = urljoin(base_url, href)
                clean   = abs_url.split("?")[0].split("#")[0]
                if urlparse(abs_url).netloc != base_domain or clean in visited:
                    continue
                visited.add(clean)
                page_type = detect_page_type(clean, types_need_more)
                if page_type and len(found[page_type]) < max_per_type:
                    found[page_type].add(clean)

    return {t: list(urls) for t, urls in found.items() if urls}


# ─── АНАЛІЗ ОДНІЄЇ СТОРІНКИ ──────────────────────────────────────────────────

def fetch_and_analyze(url: str, page_type: str, base_url: str) -> dict:
    soup = fetch(url)
    if not soup:
        return {"url": url, "label": url, "error": True, "results": {}}

    schemas = get_schemas(soup)
    checks  = PAGE_CHECKS.get(page_type, [])

    results = {}
    for check_name, check_fn in checks:
        try:
            results[check_name] = check_fn(soup, schemas, base_url)
        except Exception:
            results[check_name] = WARN

    title = soup.find("h1")
    label = title.get_text(strip=True) if title else url.rstrip("/").split("/")[-1]

    return {"url": url, "label": label[:60], "error": False, "results": results}


# ─── ГОЛОВНА ФУНКЦІЯ ──────────────────────────────────────────────────────────

def analyze_all_pages(
    base_url: str,
    main_soup: BeautifulSoup,
    site_type: str,
    max_per_type: int = 25,
    progress_callback=None,
) -> dict:
    """
    Знаходить і аналізує всі EEAT-релевантні сторінки сайту.
    Повертає {page_type: [page_result, ...]}
    """
    types_to_scan = SITE_TYPE_PAGES.get(site_type, list(PAGE_PATTERNS.keys()))

    if progress_callback:
        progress_callback(5, "Збираємо посилання з сайту...")

    all_found = collect_urls(base_url, main_soup, types_to_scan, max_per_type)

    if not all_found:
        return {}

    output        = {}
    total_pages   = sum(len(v) for v in all_found.values())
    processed     = 0

    for page_type, urls in all_found.items():
        label = PAGE_TYPE_LABELS.get(page_type, page_type)
        if progress_callback:
            pct = int(10 + 80 * processed / max(total_pages, 1))
            progress_callback(pct, f"Аналізуємо {label} ({len(urls)} стор.)...")

        page_results = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(fetch_and_analyze, url, page_type, base_url): url
                for url in urls
            }
            for future in as_completed(futures):
                try:
                    page_results.append(future.result())
                except Exception:
                    pass
                processed += 1

        page_results.sort(key=lambda x: x.get("label", ""))
        output[page_type] = page_results

    if progress_callback:
        progress_callback(100, "Готово!")

    return output
