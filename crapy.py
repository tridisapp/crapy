import threading
import json
import re
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


def extract_product_info(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # TITLE
    title = ""
    h1 = soup.select_one("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        ogt = soup.select_one("meta[property='og:title']")
        if ogt and ogt.get("content"):
            title = ogt["content"].strip()

    # IMAGE
    image = ""
    ogi = soup.select_one("meta[property='og:image']")
    if ogi and ogi.get("content"):
        image = ogi["content"].strip()

    # DESCRIPTION
    description = ""
    md = soup.select_one("meta[name='description']")
    if md and md.get("content"):
        description = md["content"].strip()

    if not description:
        for sel in [
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

    return {"url": url, "title": title, "description": description, "image": image}


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

        right = ttk.LabelFrame(main, text="Aperçu résultats (title / image / description)", padding=10)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        cols = ("url", "title", "image", "description")
        self.tree = ttk.Treeview(right, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c)
        self.tree.column("url", width=340, anchor="w")
        self.tree.column("title", width=240, anchor="w")
        self.tree.column("image", width=280, anchor="w")
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
            self.tree.insert("", "end", values=(r.get("url",""), r.get("title",""), r.get("image",""), short))

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
                    browser = p.chromium.launch(headless=headless)
                    context = browser.new_context(locale="fr-FR", viewport={"width": 1280, "height": 900})
                    page = context.new_page()
                    page.set_default_timeout(timeout_s * 1000)

                    page.goto(listing_url, wait_until="networkidle")
                    page.wait_for_timeout(wait_ms)

                    # Scroll pour lazy-load
                    for _ in range(5):
                        page.mouse.wheel(0, 2400)
                        page.wait_for_timeout(700)

                    html = page.content()

                    hrefs = extract_all_hrefs(html)
                    self.log_line(f"✓ href trouvés dans HTML: {len(hrefs)}")

                    abs_links = hrefs_to_absolute(page, hrefs)
                    self.log_line(f"✓ liens absolus construits: {len(abs_links)}")

                    links = filter_by_prefix(abs_links, prefix, only_same_domain=only_same, base_url=listing_url)
                    self.log_line(f"✓ liens après filtre prefix: {len(links)}")

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

        self.scrape_btn.config(state="disabled")
        self.get_urls_btn.config(state="disabled")
        self.status_var.set("Scraping fiches…")
        self.log_line(f"→ Scrape {len(urls)} fiche(s)")

        def worker():
            try:
                results = []
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=headless)
                    context = browser.new_context(locale="fr-FR", viewport={"width": 1280, "height": 900})
                    page = context.new_page()
                    page.set_default_timeout(timeout_s * 1000)

                    for i, u in enumerate(urls, start=1):
                        self.log_line(f"  [{i}/{len(urls)}] {u}")
                        r = page.goto(u, wait_until="domcontentloaded")
                        page.wait_for_timeout(wait_ms)
                        page.mouse.wheel(0, 1400)
                        page.wait_for_timeout(400)

                        st = r.status if r else None
                        if st and st >= 400:
                            self.log_line(f"    ⚠️ HTTP {st} (skip)")
                            continue

                        html = page.content()
                        results.append(extract_product_info(u, html))

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
