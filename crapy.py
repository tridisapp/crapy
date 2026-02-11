import threading
import json
import re
import random
import time
from urllib.parse import urlparse

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# -------------------- Helpers --------------------

def same_domain(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def extract_all_hrefs(html: str) -> list[str]:
    # Extrait tous les href="..." (relatifs ou absolus)
    return re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)


def hrefs_to_absolute(page, hrefs: list[str]) -> list[str]:
    abs_links = []
    for h in hrefs:
        if not h:
            continue
        h = h.strip()
        if h.startswith(("javascript:", "mailto:", "tel:")):
            continue
        # Convertit relatif -> absolu côté navigateur
        absu = page.evaluate("(href) => new URL(href, window.location.href).href", h)
        abs_links.append(absu.split("#")[0])
    return abs_links


def filter_by_prefix(urls: list[str], prefix: str, only_same_domain: bool, base_url: str) -> list[str]:
    seen = set()
    out = []
    for u in urls:
        if only_same_domain and not same_domain(base_url, u):
            continue
        if prefix and not u.startswith(prefix):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def build_listing_pages(first_page_url: str, max_pages: int) -> list[str]:
    """Construit des URLs de pagination type page1.htm -> pageN.htm."""
    max_pages = max(1, max_pages)
    match = re.search(r"page(\d+)\.htm$", first_page_url)
    if not match:
        return [first_page_url]

    current = int(match.group(1))
    urls = []
    for i in range(current, current + max_pages):
        urls.append(re.sub(r"page\d+\.htm$", f"page{i}.htm", first_page_url))
    return urls


def looks_like_bot_challenge(html: str) -> bool:
    lowered = html.lower()
    patterns = [
        "captcha",
        "cloudflare",
        "vérification",
        "verify you are human",
        "are you human",
        "robot",
        "access denied",
    ]
    return any(p in lowered for p in patterns)


def human_pause(min_ms: int, max_ms: int):
    low = min(min_ms, max_ms)
    high = max(min_ms, max_ms)
    time.sleep(random.uniform(low, high) / 1000)


def imitate_entry_mouse_clicks(page, min_clicks: int = 1, max_clicks: int = 3):
    """Imite quelques mouvements/clics souris dans des zones non interactives."""
    candidates = page.evaluate(
        """
        () => {
            const width = Math.max(window.innerWidth || 0, 1);
            const height = Math.max(window.innerHeight || 0, 1);
            const points = [
                [Math.floor(width * 0.1), Math.floor(height * 0.2)],
                [Math.floor(width * 0.15), Math.floor(height * 0.82)],
                [Math.floor(width * 0.88), Math.floor(height * 0.18)],
                [Math.floor(width * 0.9), Math.floor(height * 0.84)],
                [Math.floor(width * 0.5), Math.floor(height * 0.94)],
            ];
            const nonInteractive = (x, y) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return false;
                return !el.closest('a, button, input, select, textarea, [role="button"], [onclick]');
            };

            const safePoints = points.filter(([x, y]) => nonInteractive(x, y));
            return safePoints.length ? safePoints : [[Math.floor(width * 0.08), Math.floor(height * 0.9)]];
        }
        """
    )

    click_count = random.randint(max(1, min_clicks), max(min_clicks, max_clicks))
    for _ in range(click_count):
        x, y = random.choice(candidates)
        page.mouse.move(x, y, steps=random.randint(12, 28))
        page.wait_for_timeout(random.randint(120, 260))
        page.mouse.click(x, y, delay=random.randint(40, 120))
        page.wait_for_timeout(random.randint(180, 420))


def build_browser_context(browser):
    context = browser.new_context(
        locale="fr-FR",
        timezone_id="Europe/Paris",
        viewport={"width": random.choice([1280, 1366, 1440]), "height": random.choice([820, 900, 960])},
        user_agent=random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ]),
        extra_http_headers={
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        """
    )
    return context


def goto_with_retry(page, url: str, wait_until: str, wait_ms: int, retries: int, logger) -> tuple:
    """Retourne (response, html) en réessayant si challenge anti-bot détecté."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = page.goto(url, wait_until=wait_until)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            if looks_like_bot_challenge(html):
                raise RuntimeError("challenge anti-bot détecté")
            return response, html
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            backoff_s = random.uniform(5.0, 9.0) * attempt
            logger(f"    ⚠️ tentative {attempt}/{retries} échouée ({e}), pause {backoff_s:.1f}s...")
            time.sleep(backoff_s)
            page.wait_for_timeout(random.randint(900, 1800))
    raise RuntimeError(f"Impossible d'ouvrir {url} après {retries} tentatives ({last_error}).")


def build_extraction_profile(html: str) -> dict:
    """Construit un profil de sélecteurs à partir d'une fiche exemple."""
    soup = BeautifulSoup(html, "lxml")

    profile = {
        "title": "h1",
        "description": "",
        "images": "img[src]",
    }

    if soup.select_one("h1.text-trabaldo"):
        profile["title"] = "h1.text-trabaldo"

    # Cible d'abord le pattern rencontré sur trabaldogino
    longest_mso = max(
        (
            (p.get_text(" ", strip=True), "p.MsoNormal")
            for p in soup.select("p.MsoNormal")
            if len(p.get_text(" ", strip=True)) > 120
        ),
        key=lambda item: len(item[0]),
        default=("", ""),
    )
    if longest_mso[1]:
        profile["description"] = longest_mso[1]
    else:
        for sel in [
            ".product-description",
            "#description",
            ".description",
            "[class*='description']",
            "[id*='description']",
            ".productDetail",
        ]:
            el = soup.select_one(sel)
            if el and len(el.get_text(" ", strip=True)) > 80:
                profile["description"] = sel
                break

    if soup.select("img[src*='/storage/']"):
        profile["images"] = "img[src*='/storage/']"

    return profile


def extract_product_info(url: str, html: str, profile: dict | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    profile = profile or {}

    # TITLE
    title = ""
    title_selectors = [
        profile.get("title"),
        "h1.text-trabaldo",
        "h1",
    ]
    h1 = None
    for sel in title_selectors:
        if not sel:
            continue
        h1 = soup.select_one(sel)
        if h1:
            break
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        ogt = soup.select_one("meta[property='og:title']")
        if ogt and ogt.get("content"):
            title = ogt["content"].strip()

    # IMAGES
    images = []
    image_selectors = [
        profile.get("images"),
        "img[src*='/storage/']",
        "img[src]",
    ]
    seen_images = set()
    for sel in image_selectors:
        if not sel:
            continue
        for img in soup.select(sel):
            src = (img.get("src") or "").strip()
            if not src or src in seen_images:
                continue
            seen_images.add(src)
            images.append(src)
        if images:
            break

    ogi = soup.select_one("meta[property='og:image']")
    if ogi and ogi.get("content"):
        og_img = ogi["content"].strip()
        if og_img and og_img not in seen_images:
            images.insert(0, og_img)
            seen_images.add(og_img)

    image = images[0] if images else ""

    # DESCRIPTION
    description = ""
    md = soup.select_one("meta[name='description']")
    if md and md.get("content"):
        description = md["content"].strip()

    desc_selector = profile.get("description")
    if desc_selector:
        el = soup.select_one(desc_selector)
        if el:
            description = el.get_text(" ", strip=True)

    if not description:
        for sel in [
            "p.MsoNormal",
            "#description", ".description", "[class*='description']",
            ".product-description", "[id*='description']",
            ".ficheProduit", ".productDetail", "[class*='detail']"
        ]:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(" ", strip=True)
                if len(txt) > 60:
                    description = txt
                    break

    if not description:
        paras = sorted((p.get_text(" ", strip=True) for p in soup.select("p")), key=len, reverse=True)
        for p in paras[:10]:
            if len(p) > 80:
                description = p
                break

    return {
        "url": url,
        "title": title,
        "description": description,
        "image": image,
        "images": ";".join(images),
    }


# -------------------- GUI --------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Scraper — 2 étapes (HTML hrefs → URLs → Fiches) [Playwright]")
        self.geometry("1320x860")

        self.product_urls = []
        self.results = []

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="x")

        ttk.Label(frm, text="URL catégorie (listing)").grid(row=0, column=0, sticky="w")
        self.listing_var = tk.StringVar(
            value="https://www.king-jouet.com/jeux-jouets/jeux-exterieur/jeux-outils-jardinage/page1.htm"
        )
        ttk.Entry(frm, textvariable=self.listing_var, width=120).grid(row=0, column=1, columnspan=7, sticky="we", padx=6)

        ttk.Label(frm, text="Préfixe URL produit (commence par)").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.prefix_var = tk.StringVar(value="https://www.king-jouet.com/jeu-jouet/")
        ttk.Entry(frm, textvariable=self.prefix_var, width=70).grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(frm, text="(mets un préfixe précis si tu veux une sous-catégorie)").grid(row=1, column=2, sticky="w", pady=(8, 0), columnspan=4)

        ttk.Label(frm, text="Timeout (s)").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.timeout_var = tk.StringVar(value="70")
        ttk.Entry(frm, textvariable=self.timeout_var, width=8).grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(frm, text="Wait (ms)").grid(row=2, column=2, sticky="e", pady=(8, 0))
        self.wait_var = tk.StringVar(value="2500")
        ttk.Entry(frm, textvariable=self.wait_var, width=10).grid(row=2, column=3, sticky="w", padx=6, pady=(8, 0))

        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Headless", variable=self.headless_var).grid(row=2, column=4, sticky="w", pady=(8, 0))

        self.same_domain_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Même domaine", variable=self.same_domain_var).grid(row=2, column=5, sticky="w", pady=(8, 0))

        ttk.Label(frm, text="Limite scraping").grid(row=2, column=6, sticky="e", pady=(8, 0))
        self.limit_var = tk.StringVar(value="20")
        ttk.Entry(frm, textvariable=self.limit_var, width=8).grid(row=2, column=7, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(frm, text="Nb pages listing").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.pages_var = tk.StringVar(value="1")
        ttk.Entry(frm, textvariable=self.pages_var, width=8).grid(row=3, column=1, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(frm, text="Pause aléatoire (ms)").grid(row=3, column=2, sticky="e", pady=(8, 0))
        self.delay_min_var = tk.StringVar(value="900")
        self.delay_max_var = tk.StringVar(value="1900")
        ttk.Entry(frm, textvariable=self.delay_min_var, width=8).grid(row=3, column=3, sticky="w", padx=(6, 2), pady=(8, 0))
        ttk.Label(frm, text="à").grid(row=3, column=4, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.delay_max_var, width=8).grid(row=3, column=5, sticky="w", padx=(2, 6), pady=(8, 0))

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")

        self.get_urls_btn = ttk.Button(btns, text="1) Récupérer URLs produits", command=self.get_urls)
        self.get_urls_btn.pack(side="left")

        self.scrape_btn = ttk.Button(btns, text="2) Scraper fiches sélectionnées", command=self.scrape_selected, state="disabled")
        self.scrape_btn.pack(side="left", padx=8)

        ttk.Button(btns, text="Exporter CSV", command=self.export_csv).pack(side="left", padx=8)
        ttk.Button(btns, text="Exporter JSON", command=self.export_json).pack(side="left")

        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(btns, textvariable=self.status_var).pack(side="right")

        main = ttk.Frame(self, padding=(10, 0, 10, 10))
        main.pack(fill="both", expand=True)

        left = ttk.LabelFrame(main, text="URLs récupérées (sélection multiple)", padding=10)
        left.pack(side="left", fill="both", expand=True)

        self.url_list = tk.Listbox(left, selectmode="extended", width=80, height=24)
        self.url_list.pack(side="left", fill="both", expand=True)
        self.url_list.bind("<<ListboxSelect>>", lambda e: self._update_scrape_button())

        lscroll = ttk.Scrollbar(left, orient="vertical", command=self.url_list.yview)
        self.url_list.configure(yscrollcommand=lscroll.set)
        lscroll.pack(side="right", fill="y")

        right = ttk.LabelFrame(main, text="Aperçu résultats (title / image(s) / description)", padding=10)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        cols = ("url", "title", "image", "images", "description")
        self.tree = ttk.Treeview(right, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c)
        self.tree.column("url", width=340, anchor="w")
        self.tree.column("title", width=240, anchor="w")
        self.tree.column("image", width=280, anchor="w")
        self.tree.column("images", width=340, anchor="w")
        self.tree.column("description", width=420, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        rscroll = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=rscroll.set)
        rscroll.pack(side="right", fill="y")

        logfrm = ttk.LabelFrame(self, text="Logs", padding=10)
        logfrm.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log = tk.Text(logfrm, height=10)
        self.log.pack(fill="both", expand=True)

    def log_line(self, msg: str):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def _update_scrape_button(self):
        self.scrape_btn.config(state="normal" if self.url_list.curselection() else "disabled")

    def fill_url_list(self, urls):
        self.url_list.delete(0, "end")
        for u in urls:
            self.url_list.insert("end", u)
        self._update_scrape_button()

    def fill_results(self, rows):
        for it in self.tree.get_children():
            self.tree.delete(it)
        for r in rows[:200]:
            desc = r.get("description", "") or ""
            short = (desc[:160] + "…") if len(desc) > 160 else desc
            self.tree.insert("", "end", values=(
                r.get("url", ""),
                r.get("title", ""),
                r.get("image", ""),
                r.get("images", ""),
                short,
            ))

    def get_urls(self):
        listing_url = self.listing_var.get().strip()
        prefix = (self.prefix_var.get() or "").strip()
        only_same = bool(self.same_domain_var.get())

        try:
            timeout_s = int(self.timeout_var.get().strip())
        except ValueError:
            timeout_s = 70

        try:
            wait_ms = int(self.wait_var.get().strip())
        except ValueError:
            wait_ms = 2500

        headless = bool(self.headless_var.get())
        try:
            max_pages = int(self.pages_var.get().strip())
        except ValueError:
            max_pages = 1

        try:
            delay_min = int(self.delay_min_var.get().strip())
            delay_max = int(self.delay_max_var.get().strip())
        except ValueError:
            delay_min, delay_max = 900, 1900

        if not listing_url:
            messagebox.showwarning("Manquant", "Mets l’URL catégorie.")
            return
        if not prefix:
            messagebox.showwarning("Manquant", "Mets un préfixe URL produit.")
            return

        self.get_urls_btn.config(state="disabled")
        self.scrape_btn.config(state="disabled")
        self.status_var.set("Récupération URLs…")
        self.log_line(f"→ Listing: {listing_url}")
        self.log_line(f"→ Prefix: {prefix}")

        def worker():
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=headless,
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    context = build_browser_context(browser)
                    page = context.new_page()
                    page.set_default_timeout(timeout_s * 1000)

                    all_abs_links = []
                    listing_pages = build_listing_pages(listing_url, max_pages)
                    self.log_line(f"→ Pages listing à visiter: {len(listing_pages)}")
                    for idx, page_url in enumerate(listing_pages, start=1):
                        self.log_line(f"  [{idx}/{len(listing_pages)}] {page_url}")
                        _, html = goto_with_retry(
                            page,
                            page_url,
                            wait_until="domcontentloaded",
                            wait_ms=wait_ms,
                            retries=3,
                            logger=self.log_line,
                        )
                        imitate_entry_mouse_clicks(page)

                        for _ in range(4):
                            page.mouse.wheel(0, 2200)
                            page.wait_for_timeout(550)

                        html = page.content()

                        hrefs = extract_all_hrefs(html)
                        abs_links = hrefs_to_absolute(page, hrefs)
                        self.log_line(f"    ✓ href: {len(hrefs)} | absolus: {len(abs_links)}")
                        all_abs_links.extend(abs_links)
                        human_pause(delay_min, delay_max)

                    links = filter_by_prefix(all_abs_links, prefix, only_same_domain=only_same, base_url=listing_url)
                    self.log_line(f"✓ liens produits uniques après filtre: {len(links)}")

                    context.close()
                    browser.close()

                if not links:
                    raise RuntimeError(
                        "0 lien produit après filtre.\n"
                        "Teste un prefix plus large: https://www.king-jouet.com/jeu-jouet/\n"
                        "ou colle le début exact d’une URL produit."
                    )

                self.product_urls = links
                self.after(0, lambda: self.fill_url_list(links))
                self.after(0, lambda: self.status_var.set(f"URLs récupérées: {len(links)}"))
                self.after(0, lambda: self.log_line("✓ OK: URLs affichées dans la liste."))

            except Exception as e:
                self.after(0, lambda: self.log_line(f"✗ Erreur URLs: {e}"))
                self.after(0, lambda: messagebox.showerror("Erreur", str(e)))
                self.after(0, lambda: self.status_var.set("Erreur"))
            finally:
                self.after(0, lambda: self.get_urls_btn.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def scrape_selected(self):
        sel_idx = list(self.url_list.curselection())
        if not sel_idx:
            messagebox.showinfo("Sélection", "Sélectionne une ou plusieurs URLs.")
            return

        urls = [self.url_list.get(i) for i in sel_idx]

        try:
            limit = int(self.limit_var.get().strip())
        except ValueError:
            limit = 20
        urls = urls[:max(1, limit)]

        try:
            timeout_s = int(self.timeout_var.get().strip())
        except ValueError:
            timeout_s = 70

        try:
            wait_ms = int(self.wait_var.get().strip())
        except ValueError:
            wait_ms = 2500

        headless = bool(self.headless_var.get())
        try:
            delay_min = int(self.delay_min_var.get().strip())
            delay_max = int(self.delay_max_var.get().strip())
        except ValueError:
            delay_min, delay_max = 900, 1900

        self.scrape_btn.config(state="disabled")
        self.get_urls_btn.config(state="disabled")
        self.status_var.set("Scraping fiches…")
        self.log_line(f"→ Scrape {len(urls)} fiche(s)")

        def worker():
            try:
                results = []
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=headless,
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                    context = build_browser_context(browser)
                    page = context.new_page()
                    page.set_default_timeout(timeout_s * 1000)

                    extraction_profile = None
                    for i, u in enumerate(urls, start=1):
                        self.log_line(f"  [{i}/{len(urls)}] {u}")
                        try:
                            r, html = goto_with_retry(
                                page,
                                u,
                                wait_until="domcontentloaded",
                                wait_ms=wait_ms,
                                retries=2,
                                logger=self.log_line,
                            )
                            imitate_entry_mouse_clicks(page)
                        except Exception as e:
                            self.log_line(f"    ⚠️ {e} (skip)")
                            continue

                        page.mouse.wheel(0, 1400)
                        page.wait_for_timeout(400)

                        st = r.status if r else None
                        if st and st >= 400:
                            self.log_line(f"    ⚠️ HTTP {st} (skip)")
                            continue

                        if extraction_profile is None:
                            extraction_profile = build_extraction_profile(html)
                            self.log_line(
                                "    ✓ Profil extraction: "
                                f"title={extraction_profile.get('title')} | "
                                f"description={extraction_profile.get('description') or 'auto'} | "
                                f"images={extraction_profile.get('images')}"
                            )

                        results.append(extract_product_info(u, html, extraction_profile))
                        human_pause(delay_min, delay_max)

                    context.close()
                    browser.close()

                self.results = results
                self.after(0, lambda: self.fill_results(results))
                self.after(0, lambda: self.status_var.set(f"Terminé: {len(results)} fiche(s)"))
                self.after(0, lambda: self.log_line("✓ Terminé."))

            except Exception as e:
                self.after(0, lambda: self.log_line(f"✗ Erreur scrape: {e}"))
                self.after(0, lambda: messagebox.showerror("Erreur", str(e)))
                self.after(0, lambda: self.status_var.set("Erreur"))
            finally:
                self.after(0, lambda: self.get_urls_btn.config(state="normal"))
                self.after(0, lambda: self._update_scrape_button())

        threading.Thread(target=worker, daemon=True).start()

    def export_csv(self):
        if not self.results:
            messagebox.showinfo("Rien à exporter", "Aucun résultat.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        pd.DataFrame(self.results).to_csv(path, index=False, encoding="utf-8")
        self.log_line(f"→ Export CSV: {path}")

    def export_json(self):
        if not self.results:
            messagebox.showinfo("Rien à exporter", "Aucun résultat.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        self.log_line(f"→ Export JSON: {path}")


if __name__ == "__main__":
    App().mainloop()
