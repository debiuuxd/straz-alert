# Straż Alert

Mapa zdarzeń z całej Polski. Powiadomienia lokalne mają osobny, wąski zasięg.

## Działanie

- GitHub Actions pobiera RSS co 15 minut według harmonogramu GitHuba (godziny nie są gwarantowane).
- `feed_queue` zachowuje materiały pomiędzy uruchomieniami. Maksymalnie 8 prób AI na uruchomienie; pozostałe materiały czekają.
- Pierwszy poprawny odczyt każdego źródła tworzy cichy punkt startowy. Nie wysyła zaległych alarmów. Artykuły starsze niż 3 dni są pomijane, powiadomienia automatyczne wymagają publikacji w ciągu ostatnich 2 godzin i ustalonej lokalizacji.
- Nieznane współrzędne pozostają puste. Wpis pozostaje na liście. Punkty z geokodowania oznaczane są jako przybliżone i nie są losowo przesuwane.
- AI dostaje materiał źródłowy, datę publikacji i dzisiejszą datę w Polsce. Data zdarzenia, godzina, służby i status wymagają wskazania dowodu w materiale; brak danych jest jawny.
- Zapisane wcześniej daty, służby i współrzędne nie są automatycznie poprawiane — oznaczenie „dawny wpis” przypomina, że nie zostały zweryfikowane nową logiką.
- Łączenie artykułów jest ostrożne: ten sam identyfikator/link albo zgodna jawna data i godzina, lokalizacja, kategoria oraz mocno podobny tytuł. Sama bliskość punktów nie wystarcza. Część opisów bez daty/godziny może nadal wymagać ręcznej oceny.
- Nowe informacje mogą uzupełnić źródła i potwierdzony status. Zakończonej akcji nie otwiera ponownie artykuł o trwających działaniach. Nie ma dostępu do wewnętrznego systemu statusów służb.
- Rezerwacja `(incident_id, topic)` przed wysyłką ntfy zapobiega ponownej wysyłce po restarcie. Po niejednoznacznym błędzie sieci nie ma automatycznej powtórki: możliwe jest pominięcie alarmu zamiast jego podwojenia. Wynik pojawia się w stanie bota.
- Ręczne zgłoszenia wysyła tylko właściciel. Ntfy jest opcjonalne i realizowane przez bota, z tym samym lokalnym filtrem. Bez punktu na mapie bot najpierw próbuje odnaleźć miejscowość.
- `local-alert-area.json` jest wspólną konfiguracją bota i strony. Zachowuje poprzednie małe obszary wokół Strykowa/Głowna oraz nazwy miejscowości, a odrzuca odległe miejscowości o powtarzających się nazwach. Łódź i Zgierz są wykluczane także z ulicą w opisie. To nadal filtr przybliżony, nie granice administracyjne.
- Kalendarz pobiera cały wybrany dzień. „Wszystkie dni” oferuje kolejne strony starszych wpisów. Brak lokalizacji nie usuwa zdarzenia z listy.
- Radar RainViewer pozostaje na mapie i odświeża pomiar co 10 minut po włączeniu. IMGW i komunikaty modelu Open-Meteo mają oddzielne oznaczenia. Błąd pobierania nie jest komunikatem o braku zagrożeń.
- Ustawienia mapy zawierają podgląd stanu źródeł i kolejki. Awaria źródła nie wyłącza pozostałych źródeł, ale jest zapisana i dodaje ostrzeżenie do podsumowania GitHuba.

## Baza i dostęp

1. `database/reliability.sql` dodaje pola i tabele bez usuwania zdarzeń.
2. `database/owner-access.sql` ogranicza dodawanie i usuwanie do adresów zapisanych przez administratora w `app_editors`. E-maile właścicieli przechowuje się tylko w bazie, nie w repozytorium. Tabeli tej nie wolno udostępniać do publicznego zapisu.
3. Supabase Auth → URL Configuration → Site URL: `https://debiuuxd.github.io/straz-alert/`.
4. Właściciel otwiera ustawienia mapy, podaje dopuszczony adres i wybiera „Wyślij link do logowania”. Zwykłe oglądanie mapy nie wymaga logowania.
5. Domyślna poczta Supabase ma ograniczenia; dla jednego właściciela będącego członkiem projektu wystarcza do prób. Nie dodawać innych odbiorców bez sprawdzenia konfiguracji poczty. Nigdy nie umieszczać klucza `service_role` w stronie.

Sekrety GitHuba: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (ewentualnie dotychczasowy serwerowy `SUPABASE_KEY`), `GEMINI_API_KEY`. Klucz anonimowy nie jest zastępczym kluczem bota. Biblioteki przypięto w `requirements.lock.txt`.

## Poprawianie pinezek i dubli

Po zalogowaniu właściciel otwiera kartę zdarzenia. Na dole karty są opcje:

- **Przesuń pinezkę**: kliknij mapę lub przeciągnij niebieski punkt, popraw nazwę miejsca i naciśnij „Zapisz położenie”. „Anuluj” nie zapisuje zmian. Punkt jest oznaczony jako wskazany ręcznie; kolejne aktualizacje bota go zachowują.
- **Połącz duplikat**: wyszukaj drugi wpis po miejscu lub tytule (wyszukiwarka obejmuje wszystkie dni). Porównaj oba opisy i źródła, wybierz opis i położenie do zachowania, zaznacz potwierdzenie i wybierz „Połącz wpisy”. Źródła obu wpisów zostają razem. Drugi wpis i jego identyfikatory pozostają w bazie jako alias, dzięki czemu stary materiał nie tworzy nowej pinezki.
- **Cofnij ostatnią poprawkę**: przywraca poprzedni punkt albo rozdziela wpisy. Dostępne dopóki żaden z objętych wpisów nie został później zmieniony. Cofnięcie nie nadpisuje nowszych danych.

Migrację `database/owner-corrections.sql` stosuje się po `database/merged-incidents.sql`. Dodaje numer wersji wpisu, prywatny zapis poprawek i atomowe funkcje z kontrolą właściciela. Nie przyznaje przeglądarce bezpośredniego prawa UPDATE. Każdy zapis sprawdza wersję, żeby nie nadpisać równoległej aktualizacji. Bot odczytuje świeży wpis główny przed uzupełnieniem źródeł i ponawia po konflikcie wersji. Poprawki i cofnięcia nie uruchamiają ntfy; konfiguracja zasięgu pozostaje w `local-alert-area.json`.

`tests/owner_corrections.sql` to osobny test integracyjny administratora: sprawdza rzeczywiste role i funkcje w transakcji zakończonej ROLLBACK, bez pozostawiania próbnych zdarzeń. Nie jest automatycznie uruchamiany przez CI.

## Lokalizacja i warunki usługi

Bot zachowuje istniejące użycie Nominatim z trwałą pamięcią wyników, jednym zadaniem naraz i odstępem co najmniej 15,1 s pomiędzy nowymi zapytaniami. Nie ma automatycznego wyszukiwania adresów podczas pisania w formularzu. Alternatywny zgodny endpoint można ustawić przez `GEOCODER_URL` bez zmian kodu. Korzystanie musi pozostawać zgodne z [zasadami Nominatim](https://operations.osmfoundation.org/policies/nominatim/), w tym limitem dla cyklicznych zadań, oznaczeniem źródła i warunkami OSM. W razie większego ruchu należy zmienić dostawcę.

## Testy bez wysyłania powiadomień

```sh
pip install -r requirements.lock.txt
python -m unittest discover -s tests -v
node tests/test_frontend.cjs
```

Testy nie korzystają z sekretów ani nie uruchamiają bota. Wywołania ntfy w testach są zastąpione symulacją.
