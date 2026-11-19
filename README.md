# NoteSport — Tipsport extraliga

Statická stránka pro GitHub Pages. Při otevření čte soubory `data/*.json`; návštěvníkům se zobrazuje jen ověřený obsah, který automatizace úspěšně uložila. Data doplňují GitHub Actions:

- průběžné výsledky a tabulka na serveru nejméně každých 5 minut (otevřená stránka zkouší načíst skóre a tabulku každých 20 sekund);
- oficiální kompletní rozpis každých 15 minut, včetně dosud zveřejněných zápasů play-off;
- novinky z RSS Hokej.cz každých 30 minut, s archivem posledních 500 článků;
- tabulka se přebírá z oficiálního aktuálního pořadí Českého hokeje, takže se po výsledcích mění automaticky.

## Jednorázové zveřejnění zdarma

1. Nahrajte obsah této složky do nového GitHub repozitáře na výchozí větvi `main`.
2. V `Settings → Pages` nastavte publikování přes `GitHub Actions`.
3. V `Settings → Actions → General` ponechte povolené workflow a zápis tokenu `GITHUB_TOKEN` do repozitáře. Workflow nepoužívají jiné tajné klíče.
4. V `Actions` spusťte `Aktualizace NoteSport` ručně. Ruční spuštění načte tabulku, rozpis i noviny; workflow pro Pages se spustí automaticky při nahrání souborů.

GitHub Pages a GitHub Actions pak poběží nezávisle na tomto počítači a na tom, zda je web otevřený. GitHub plánuje workflow nejvýše jednou za 5 minut, může je navíc spustit se zpožděním. Otevřená stránka proto zkouší živý scoreboard načíst každých 20 sekund, ale mimo stránku jsou automatizované běhy nejrychleji po pěti minutách. Přesný průběžný čas a statistiky jsou jen tehdy, pokud je datový zdroj poskytne; chybějící údaje jsou označené, nevymýšlíme je.

## Data a bezpečnost

- Tabulka a kompletní rozpis se čtou z webu Českého hokeje. Skript ponechá předchozí datové soubory, pokud stránku nelze přečíst, nezíská všech 14 týmů nebo méně než 364 utkání základní části. Další utkání (například play-off) se zahrnou, jakmile jsou na oficiálních stránkách vypsaná.
- Průběžná skóre se doplňují z veřejného scoreboardu eSports.cz; program, perioda a konečné výsledky jsou z oficiálního rozpisu Českého hokeje. Scoreboard je omezen na nedávné dny, proto se starší skóre uchovává v archivu `games.json`.
- Noviny se načítají z RSS Hokej.cz. Články se slučují do archivu; nevymýšlíme texty ani výsledky.
- Zdrojová stránka uvádí odkazy na původní články a oficiální data.

## Ruční spuštění

```bash
python -m pip install -r requirements.txt
python scripts/update_data.py --mode schedule
python scripts/update_data.py --mode live
python scripts/update_data.py --mode news
```

