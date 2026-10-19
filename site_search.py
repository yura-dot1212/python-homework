"""
Фінальний проєкт: пошук введеного тексту на сторінках, посилання на які
зберігаються в БД. Чим частіше текст трапляється на сторінці, тим вище
сайт у списку результатів.

Залежності:  pip install requests beautifulsoup4
Архітектура: Database (SQLite) + SiteParser (requests/bs4) + UserInterface (tkinter)
"""

import re
import sqlite3
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# ----------------------------------------------------------------------------
# 1. Об'єкт, що працює з базою даних
# ----------------------------------------------------------------------------
class Database:
    def __init__(self, path="sites.db"):
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS sites ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL UNIQUE)"
        )
        self.connection.commit()

    @staticmethod
    def normalize(url):
        """Додає https://, якщо схему не вказано. Повертає None для некоректних URL."""
        url = url.strip()
        if not url:
            return None
        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = "https://" + url
        parsed = urlparse(url)
        return url if parsed.netloc and "." in parsed.netloc else None

    def add_site(self, url):
        """Повертає True, якщо сайт додано (нового запису раніше не було)."""
        url = self.normalize(url)
        if url is None:
            return False
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO sites (url) VALUES (?)", (url,)
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def add_sites(self, urls):
        return sum(1 for u in urls if self.add_site(u))

    def delete_site(self, url):
        self.connection.execute("DELETE FROM sites WHERE url = ?", (url,))
        self.connection.commit()

    def get_sites(self):
        rows = self.connection.execute("SELECT url FROM sites ORDER BY id").fetchall()
        return [row[0] for row in rows]

    def clear(self):
        self.connection.execute("DELETE FROM sites")
        self.connection.commit()

    def close(self):
        self.connection.close()


# ----------------------------------------------------------------------------
# 2. Об'єкт, що парсить сайти
# ----------------------------------------------------------------------------
class SiteParser:
    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StudentSearchBot/1.0)"}

    def __init__(self, timeout=10, workers=8):
        self.timeout = timeout
        self.workers = workers

    def get_text(self, url):
        """Завантажує сторінку й повертає видимий текст (без script/style)."""
        response = requests.get(url, headers=self.HEADERS, timeout=self.timeout)
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ")

    @staticmethod
    def count_occurrences(text, query):
        """Кількість входжень запиту (без урахування регістру)."""
        if not query:
            return 0
        return len(re.findall(re.escape(query), text, re.IGNORECASE))

    def check_site(self, url, query):
        """Повертає (url, кількість, помилка)."""
        try:
            return url, self.count_occurrences(self.get_text(url), query), None
        except Exception as error:  # мережеві та інші помилки не мають зупиняти пошук
            return url, 0, str(error)

    def search(self, urls, query):
        """Паралельно перевіряє всі сайти. Результат відсортований за спаданням."""
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(lambda u: self.check_site(u, query), urls))
        return sorted(results, key=lambda item: item[1], reverse=True)


# ----------------------------------------------------------------------------
# 3. Об'єкт інтерфейсу користувача
# ----------------------------------------------------------------------------
class UserInterface:
    def __init__(self, database, parser):
        self.db = database
        self.parser = parser
        self.results_queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title("Пошук по сайтах")
        self.root.geometry("820x560")
        self._build_widgets()
        self.refresh_sites()

    # --- побудова вікна ------------------------------------------------------
    def _build_widgets(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.search_tab = ttk.Frame(notebook)
        self.sites_tab = ttk.Frame(notebook)
        notebook.add(self.search_tab, text="Пошук")
        notebook.add(self.sites_tab, text="Список сайтів")

        # вкладка «Пошук»
        top = ttk.Frame(self.search_tab)
        top.pack(fill="x", pady=8)
        self.query_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda e: self.start_search())
        self.search_button = ttk.Button(top, text="Знайти", command=self.start_search)
        self.search_button.pack(side="left")

        columns = ("rank", "url", "count")
        self.results = ttk.Treeview(
            self.search_tab, columns=columns, show="headings", height=15
        )
        self.results.heading("rank", text="№")
        self.results.heading("url", text="Сайт")
        self.results.heading("count", text="Збігів")
        self.results.column("rank", width=40, anchor="center", stretch=False)
        self.results.column("count", width=90, anchor="center", stretch=False)
        self.results.pack(fill="both", expand=True)
        self.results.tag_configure("error", foreground="#b00020")

        self.status_var = tk.StringVar(value="Введіть запит і натисніть «Знайти».")
        ttk.Label(self.search_tab, textvariable=self.status_var).pack(
            anchor="w", pady=(6, 0)
        )

        # вкладка «Список сайтів»
        add_row = ttk.Frame(self.sites_tab)
        add_row.pack(fill="x", pady=8)
        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(add_row, textvariable=self.url_var)
        url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        url_entry.bind("<Return>", lambda e: self.add_site())
        ttk.Button(add_row, text="Додати", command=self.add_site).pack(side="left")

        self.sites_list = tk.Listbox(self.sites_tab, selectmode="extended")
        self.sites_list.pack(fill="both", expand=True)

        buttons = ttk.Frame(self.sites_tab)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Імпорт з файлу…", command=self.import_file).pack(
            side="left"
        )
        ttk.Button(buttons, text="Додати приклади", command=self.add_examples).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Видалити вибране", command=self.delete_selected).pack(
            side="left", padx=8
        )
        ttk.Button(buttons, text="Очистити БД", command=self.clear_db).pack(side="right")

    # --- робота зі списком сайтів ---------------------------------------------
    def refresh_sites(self):
        self.sites_list.delete(0, "end")
        for url in self.db.get_sites():
            self.sites_list.insert("end", url)

    def add_site(self):
        raw = self.url_var.get()
        if self.db.add_site(raw):
            self.url_var.set("")
            self.refresh_sites()
        else:
            messagebox.showwarning(
                "Увага", "Некоректне посилання або воно вже є в базі."
            )

    EXAMPLE_SITES = [
        "python.org",
        "en.wikipedia.org/wiki/Python_(programming_language)",
        "uk.wikipedia.org/wiki/Python",
        "github.com",
        "stackoverflow.com",
        "habr.com",
        "realpython.com",
        "w3schools.com/python",
    ]

    def add_examples(self):
        added = self.db.add_sites(self.EXAMPLE_SITES)
        self.refresh_sites()
        messagebox.showinfo("Приклади", f"Додано нових сайтів: {added}")

    def import_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Текстові файли", "*.txt"), ("Усі файли", "*.*")]
        )
        if not path:
            return
        with open(path, encoding="utf-8") as file:
            added = self.db.add_sites(file.read().splitlines())
        self.refresh_sites()
        messagebox.showinfo("Імпорт", f"Додано нових сайтів: {added}")

    def delete_selected(self):
        for index in reversed(self.sites_list.curselection()):
            self.db.delete_site(self.sites_list.get(index))
        self.refresh_sites()

    def clear_db(self):
        if messagebox.askyesno("Підтвердження", "Видалити всі сайти з бази даних?"):
            self.db.clear()
            self.refresh_sites()
            self.results.delete(*self.results.get_children())
            self.status_var.set("Базу даних очищено.")

    # --- пошук ----------------------------------------------------------------
    def start_search(self):
        query = self.query_var.get().strip()
        sites = self.db.get_sites()  # читаємо БД в основному потоці
        if not query:
            messagebox.showwarning("Увага", "Введіть текст для пошуку.")
            return
        if not sites:
            messagebox.showwarning("Увага", "База даних порожня. Додайте сайти.")
            return

        self.search_button.config(state="disabled")
        self.results.delete(*self.results.get_children())
        self.status_var.set(f"Пошук по {len(sites)} сайтах…")

        def worker():
            self.results_queue.put(self.parser.search(sites, query))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(200, self._poll_results)

    def _poll_results(self):
        try:
            results = self.results_queue.get_nowait()
        except queue.Empty:
            self.root.after(200, self._poll_results)
            return
        self.show_results(results)

    def show_results(self, results):
        found = 0
        for rank, (url, count, error) in enumerate(results, start=1):
            if error:
                self.results.insert(
                    "", "end", values=(rank, f"{url}  (помилка: {error[:60]})", "—"),
                    tags=("error",),
                )
            else:
                found += count > 0
                self.results.insert("", "end", values=(rank, url, count))
        self.status_var.set(f"Готово. Сайтів зі збігами: {found} з {len(results)}.")
        self.search_button.config(state="normal")

    def mainloop(self):
        self.root.mainloop()


# ----------------------------------------------------------------------------
# Точка входу
# ----------------------------------------------------------------------------
def run():
    database = Database()
    parser = SiteParser()
    ui = UserInterface(database, parser)
    try:
        ui.mainloop()
    finally:
        database.close()


if __name__ == "__main__":
    run()
