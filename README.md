# Tracker polityków

Sam śledzi, co Trump i jego administracja mówią o konkretnych spółkach, oraz jakie akcje kupują
i sprzedają najgłośniejsi giełdowi gracze w Kongresie. Maile przychodzą tylko wtedy, gdy Trump powie coś ważnego, a transakcje polityków trafiają do niedzielnego podsumowania; nic się nie powtarza codziennie, a Ty nie musisz nic robić.

Kogo śledzimy:

| Kto | Co |
|---|---|
| **Donald Trump** | wpisy na Truth Social, decyzje prezydenta (rozporządzenia, cła), przemówienia |
| **JD Vance** i ministrowie | przemówienia i oświadczenia publikowane przez Biały Dom |
| **Nancy Pelosi** | transakcje giełdowe (STOCK Act) |
| **Josh Gottheimer** | transakcje, dużo opcji |
| **Cleo Fields** | transakcje, jeden z najaktywniejszych w 2026 |
| **Byron Donalds** | transakcje, kandydat Trumpa na gubernatora Florydy |

Listę polityków zmieniasz w `config.yaml` (`trades.watchlist`).

Co przychodzi na maila:

| Mail | Kiedy | Co zawiera |
|---|---|---|
| 🔔 Alert | zwykle do ~10 min od pojawienia się wpisu w archiwum, tylko gdy dotyczy spółki lub branży | streszczenie po polsku, tickery, wpływ pozytywny lub negatywny, aktualny kurs |
| 📊 Podsumowanie | w każdą niedzielę | jak zmieniły się kursy od wzmianki Trumpa i od transakcji polityków (kto co kupił i sprzedał), w porównaniu z S&P 500 |

Niedzielny mail przychodzi zawsze, więc jest też sygnałem, że tracker działa. Gdyby coś się zepsuło
(np. padło źródło danych), GitHub sam wyśle Ci maila o nieudanym uruchomieniu.

> To zestawienie publicznych informacji, a nie porada inwestycyjna. Wzmianka Trumpa nie oznacza, że kurs
> wzrośnie: przypadek Della jest głośny, bo się udał. Niedzielne podsumowanie pokazuje uczciwie także
> wzmianki i transakcje, po których kurs spadł.

## Jak to działa

Wszystko działa za darmo na GitHub Actions, czyli na serwerach GitHuba. Twój komputer nie musi być włączony.

- **Trump**: archiwum Truth Social [trumpstruth.org](https://www.trumpstruth.org), sprawdzane co 10 minut.
- **Biały Dom**: decyzje prezydenta, przemówienia i oświadczenia z [whitehouse.gov](https://www.whitehouse.gov).
- **Ocena wypowiedzi**: model Nemotron na NVIDIA NIM (darmowe API z [build.nvidia.com](https://build.nvidia.com))
  czyta każdy nowy wpis, ocenia go w skali 1–5 i wskazuje spółki z tickerami (sprawdzanymi w Nasdaq).
  Mail przychodzi przy ocenie 4–5, czyli przy konkretnej decyzji, inwestycji, cle lub ataku na spółkę. Bez klucza tracker szuka
  tylko nazw ~500 spółek w tekście; działa wtedy dużo gorzej, bo nie wyłapie np. ceł na stal.
- **Transakcje**: oficjalne zgłoszenia z [disclosures-clerk.house.gov](https://disclosures-clerk.house.gov),
  sprawdzane raz dziennie.
- **Ceny**: Nasdaq (opóźnione), z Yahoo Finance jako zapasowym źródłem.

## Instalacja (jednorazowo, ok. 15 minut)

Potrzebujesz trzech rzeczy:

1. **Konto na GitHubie**: [github.com/signup](https://github.com/signup), za darmo.
2. **Hasło aplikacji do Gmaila**, żeby tracker mógł wysyłać maile z Twojej skrzynki.
   Wejdź na [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   (wymaga włączonej weryfikacji dwuetapowej), wpisz nazwę np. „Tracker” i skopiuj 16-znakowe hasło.
   Używasz iCloud? Wygeneruj „hasło dla aplikacji” na [account.apple.com](https://account.apple.com)
   i w `config.yaml` zmień `smtp_host` na `smtp.mail.me.com`.
3. **Klucz NVIDIA** (zaczyna się od `nvapi-`): masz już jeden w `~/.config/clinical-search/nvidia.env`
   (zmienna `LLM2_API_KEY`). Nowy wygenerujesz na [build.nvidia.com](https://build.nvidia.com)
   po kliknięciu „Get API Key”.

Potem w Terminalu, w folderze projektu:

```bash
brew install gh
```

```bash
gh auth login
```

Jeśli to Twój pierwszy raz z gitem, przedstaw się mu (wpisz swoje imię i e-mail):

```bash
git config --global user.name "Imię Nazwisko" && git config --global user.email "ty@example.com"
```

```bash
git init -b main && git add . && git commit -m "Tracker"
```

```bash
gh repo create trump-tracker --private --source . --push
```

Teraz ustaw sekrety. Każda komenda zapyta o wartość; wklejasz ją i wciskasz Enter (tekst się nie wyświetla):

```bash
gh secret set SMTP_USER
```
(Twój adres Gmail)

```bash
gh secret set SMTP_PASSWORD
```
(16-znakowe hasło aplikacji)

```bash
gh secret set EMAIL_TO
```
(adres, na który mają przychodzić maile; kilka adresów oddziel przecinkami)

Klucz NVIDIA przeniesiesz prosto z pliku, bez kopiowania:

```bash
gh secret set NVIDIA_API_KEY --body "$(grep '^LLM2_API_KEY=' ~/.config/clinical-search/nvidia.env | cut -d= -f2- | tr -d "\"'")"
```

Na koniec wyślij mail testowy:

```bash
gh workflow run tracker.yml -f mode=test-email
```

Po minucie powinien przyjść mail „✅ Tracker: test maila”. W ciągu godziny przyjdzie też
„✅ Tracker działa”, czyli analiza ostatnich wpisów, a następnego dnia transakcje polityków
z ostatnich 30 dni. Od tej chwili wszystko dzieje się samo.

Wolisz bez Terminala? Załóż prywatne repozytorium na github.com, wgraj pliki przez
[GitHub Desktop](https://desktop.github.com), a sekrety dodaj w repozytorium:
**Settings → Secrets and variables → Actions → New repository secret**. Mail testowy uruchomisz w
zakładce **Actions → Tracker → Run workflow**.

## Zmiana ustawień

Edytuj `config.yaml` (na github.com: otwórz plik i kliknij ołówek). Najważniejsze opcje:

- `trades.watchlist`: politycy, których transakcje śledzimy (wystarczy nazwisko).
- `analysis.model`: model z [build.nvidia.com/models](https://build.nvidia.com/models).
- `analysis.min_importance`: od 1 do 5. Wyższa wartość oznacza mniej maili, tylko najważniejsze sprawy.
- `sources`: możesz dopisać dowolny kanał RSS (`type: rss`).

## Koszty

- GitHub: 0 zł. Repo jest publiczne, a publiczne repo ma minuty GitHub Actions bez limitu. Sekrety (klucze, hasło, adres e-mail) pozostają ukryte.
- NVIDIA NIM: 0 zł w ramach darmowego dostępu dla deweloperów (limit ok. 40 zapytań na minutę, tracker robi kilka na godzinę).
- Maile: 0 zł.

## Ograniczenia

- **JD Vance**: jego wpisy są głównie na X, a dostęp do API X kosztuje ok. 200 USD miesięcznie.
  Śledzimy więc tylko jego oficjalne wystąpienia, które publikuje Biały Dom.
- **Senat** (np. Tuberville) nie jest jeszcze objęty: jego strona ze zgłoszeniami blokuje połączenia
  spoza USA, więc nie dało się tego przetestować.
- **Khanna i McCaul** składają papierowe skany, z których nie da się automatycznie odczytać transakcji.
  Gdy ich dopiszesz do listy, dostaniesz tylko informację o nowym zgłoszeniu z linkiem do PDF.
- Politycy mają do 45 dni na zgłoszenie transakcji, więc informacja przychodzi z opóźnieniem.
- Wpisy Trumpa pochodzą z niezależnego archiwum, bo Truth Social blokuje automatyczny dostęp.
- Tracker działa jako jedno długie uruchomienie (ok. 5,5 h), które przed końcem startuje następne. Godzinny harmonogram GitHuba jest tylko zapasem, gdyby ten łańcuch się przerwał.

## Uruchomienie lokalne (opcjonalne)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m tracker alerts --dry-run
```

Z `--dry-run` nic nie jest wysyłane ani zapisywane, a podgląd maila trafia do folderu `out/`.
Dostępne tryby: `alerts`, `trades`, `weekly`, `test-email`. Klucz NVIDIA podajesz w zmiennej
środowiskowej `NVIDIA_API_KEY`.
