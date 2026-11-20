# FUZJA — rejestr bazy operacyjnej

## Kanoniczna lokalizacja
- Dostawca: Cloudflare D1
- Nazwa: `fuzja-core`
- ID bazy: `72b81b7d-e92e-4050-b484-57ccc5cfa8d5`
- Konto Cloudflare: `7212c69dc8da20b144a6200a911c3795`
- Rejestr projektów: tabela `fuzja_registry`

## Zasady dostępu
- Dostęp wyłącznie po stronie serwera przez binding D1 albo autoryzowane API.
- Nie umieszczaj kluczy ani tokenów w kodzie przeglądarki.
- MotherDuck `my_db.sports` pozostaje kopią archiwalną i rollbackiem.
- Przed przełączeniem projektu wykonaj test odczytu liczników: predictions=56, settlements=47, matches=9, coupon_legs=56, sources=9.
