# Underdogs FUZJA AI 0.4.0 (test)

Nowa aplikacja Android (minimum Android 6, zgodna z Android 10+) z interfejsem
hostowanym przez Supabase Edge Functions. Łączy się z Supabase Auth i wysyła
autoryzowane dane meczu do funkcji underdogs-analyze. Klucz OpenAI pozostaje
wyłącznie na serwerze. Nie zapisuje zakładów i nie loguje do konta bukmacherskiego.

## Budowanie

Repozytorium zawiera workflow GitHub Actions build-underdogs-apk.yml.
Debug APK jest podpisany kluczem testowym i nadaje się do instalacji ręcznej,
ale nie do publikacji w Google Play. Nowy applicationId pozwala zainstalować
go obok starszych wersji.

## Źródła danych

TheSportsDB Free, terminarz na dzień i dyscyplinę: na darmowym kluczu
liczba wyników w pojedynczej odpowiedzi jest ograniczona (obecnie 3).
Nie są pobierane kursy bukmacherskie; nie wolno ich zgadywać.

## Bezpieczeństwo

Nie wpisuj OPENAI_API_KEY do aplikacji ani do repozytorium.
W Supabase Edge Function underdogs-analyze wymagany jest zalogowany użytkownik.
Wersja testowa wymaga ręcznego zalogowania i kliknięcia Analizuj.
