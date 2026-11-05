# Room — spørsmål fra møtet, undersøkt mens dere snakker

Lokal hackathon-prototype med simulert Atech-boks, møteinput, parallelle arbeidere,
SQLite-analyse, grafer og nedlastbare GitHub-issue-utkast.

## Start

```sh
cd room
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Åpne http://127.0.0.1:8765 i nettleseren. Bruk Chrome/Edge for valgfri
nettleserbasert talegjenkjenning. Tekst fungerer uten mikrofon og API-nøkkel.
Klikk «Prøv et ferdig eksempel», deretter den runde knappen på boksen.

## Hva fungerer nå?

- Syntetisk database: 180 kunder, tre produkter, hendelser og interne testkontoer.
- Kodearbeider leser ekte `app/product.py`; dataarbeider kjører ekte SQL parallelt.
- Regelstyrt demomodus med tydelig merking. Ingen LLM eller eksterne kontoer kreves.
- Manglende produkt/periode gir avklaring. Like aktive forespørsler dedupliseres.
- Graf med kilder, definisjon, SQL og parametere. Manuelt valgt visning.
- Opplesing via nettleseren, transkripsjon via tilgjengelig SpeechRecognition.
- Issue-utkast som Markdown. Ingen issues opprettes på GitHub.
- Lokal sesjonslagring; avbrutte jobber etter serverrestart markeres med feil.
- SQL kjører på read-only-tilkobling med authorizer, tidsgrense og radgrense.

## Avgrensning

Dette er en lokal enkeltromsprototype, uten innlogging. Bind bare til loopback.
En møteforespørsel støttes: nye eksterne kunder med minst én brukshendelse for
produkt X/Y/Z siste 7/30/90 dager. Demo-reglene kan feiltolke naturlig språk;
ikke bruk dette til reelle forretningsbeslutninger. Grafen viser syntetiske data.
Kodearbeideren leser en fast kildefil; den navigerer foreløpig ikke et vilkårlig repo.
Arbeiderne er deterministiske, ikke autonome LLM-agenter. Dette er merket i UI.

Mikrofon startes eksplisitt. Nettleserleverandøren kan behandle lyd eksternt.
Transkripsjon lagres lokalt i `data/session.json`. Slett filen med serveren stoppet
for å nullstille møtet. `data/demo.db` kan slettes for å lage nye demodata.
Ikke bruk sensitive møtedata i prototypen. Nettleserens taletjeneste kan stoppe
av seg selv; UI oppdaterer mikrofonstatus og starter aldri lytting automatisk.

## Valgfri modellbasert tolkning

Sett OPENAI_API_KEY i miljøet før oppstart. OPENAI_MODEL kan overstyre modellen
(standard gpt-4.1-mini). Da sendes hvert innsendte utsagn til OpenAI for å trekke
ut produkt og periode. SQL forblir parameterisert og forhåndsdefinert.
Ikke skriv API-nøkler i kildekode eller del dem i nettleseren.
Dette gir modellbasert tolkning, ikke full autonome agenter.

## Hardware senere

Ingen board/modul/port er antatt og ingen firmware er generert eller flashet.
Når hardware finnes: bekreft kort, moduler og porter; kjør `atech info`, lag
`project.yaml`, `atech validate`, bygg og sjekk hardware. Første ESP32-bygg krever
omtrent 2.4 GB til compiler. `atech describe` gir korrekte navn for adapteren:

```sh
python -m app.hardware_bridge --port /dev/cu.usbmodemYOURPORT \
  --led-action ACTUAL_FILL_ACTION --button-key ACTUAL_BUTTON_KEY
```

Adapteren bruker én SDK-forbindelse, bekreftede `board.call`-handlinger og knappevent
for å åpne resultat på dashboardet. Den er ikke testet på fysisk hardware.
Mikrofon og liten hardware-skjerm kobles til etter at modulene er kjent.

## Test

```sh
python -m unittest discover -s tests -v
```

## Neste steg

1. Koble faktisk repo og en godkjent read-only-datakilde med eksplisitte definisjoner.
2. Utvid evalueringssett for møteutsagn (nektelser, hypotetiske spørsmål, korrigeringer).
3. Strømmetranskripsjon med talerskille og kontrollert retention.
4. Modellstyrte kode-/dataagenter med avgrensede verktøy og sporbare bevis.
5. GitHub-integrasjon med eksplisitt gjennomgang før opprettelse.
6. Atech firmware etter bekreftet fysisk oppsett.
