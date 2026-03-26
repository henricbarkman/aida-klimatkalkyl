# AIda: Arkitektur och metod

## Vad AIda gör

AIda hjälper förvaltare och byggledare att jämföra klimatpåverkan vid ombyggnationer. En användare beskriver sitt projekt i fritext ("byta 500 m2 vinylgolv och 40 fönster"), och AIda:

1. Sätter en **baslinje** — klimatpåverkan om konventionella standardmaterial används
2. Tar fram **klimatsmartare alternativ** baserat på verifierade EPD:er
3. Presenterar **jämförelsen** — hur mycket bättre varje alternativ är mot baslinjen

Resultatet är ett beslutsunderlag, inte en certifiering.

## Metod: NollCO2-inspirerad baslinje

AIda använder en förenklad variant av NollCO2-metoden (Sweden Green Building Council) för att sätta baslinjen.

### Vad NollCO2 egentligen är

NollCO2 skapar en projektspecifik "standardtvilling" — en digital modell med samma geometri och funktioner som det aktuella projektet, men med konventionella standardmaterial. Det är inte "worst case", utan **standard case**: vad ett typiskt projekt av samma typ brukar innebära klimatmässigt.

Det skiljer NollCO2 från enklare metoder som Miljöbyggnad och BREEAM-SE, som använder fasta medianvärden per byggnadskategori. NollCO2 är mer precist men också mer komplext.

### Hur AIda tillämpar principen

AIda gör en förenkling av NollCO2: istället för en komplett geometrisk standardtvilling använder vi **Boverkets klimatdatabas med Typical-värden (A1-A3)** som baslinje per komponent. Inte Conservative-värdena (+25%), utan Typical, per NollCO2 Manual 1.2, sektion 5.2/tabell 3.

Notera: JJ utreder (Notion-kort "Hur ska vi sätta baslinje?") om NollCO2 är rätt val, och hur AIda:s baslinje förhåller sig till kommunens faktiska materialval. Öppen fråga.

### Jämförelsens två ben

| Ben | Datakälla | Roll |
|-----|-----------|------|
| **Baslinje** | Boverkets klimatdatabas (Typical A1-A3) | Vad standardmaterial kostar klimatmässigt. Referenspunkten. |
| **Alternativ** | Environdec EPD:er (produktspecifika) | Verkliga produkter med verifierad klimatpåverkan, som LLM:en väljer bland. |

Klimatbesparingen = baslinje minus alternativets påverkan.

**GWP-GHG A1-A3** är det primära måttet (produktion, exkl. biogent CO2), i linje med Boverkets klimatdeklarationskrav.

## Agentpipeline

Sex steg, varje hanterat av en specialiserad Claude-agent:

```
Intake → Baslinje → Alternativ → Sammanställning → Rapport
```

| Steg | Agent | Input | Output |
|------|-------|-------|--------|
| 1. Intake | `intake.py` | Fritext projektbeskrivning | Strukturerat `Project` (byggnadstyp, BTA, komponentlista) |
| 2. Baslinje | `baseline.py` | `Project` | `Baseline` (CO2e + kostnad per komponent via Boverket Typical) |
| 3. Alternativ | `alternatives.py` | `Project` + `Baseline` + EPD-data | `AlternativesResult` (klimatoptimerade alternativ med EPD-källor) |
| 4. Sammanställning | `aggregate.py` | Alla ovan | Aggregerad jämförelse, ranking |
| 5. Rapport | `report.py` | Alla ovan | Markdown-rapport med beslutsunderlag |

### Hur alternativ-agenten funkar

Användaren söker inte själv efter EPD:er. Det är LLM:en (Claude) som agerar expert:

1. **Intake-agenten** tolkar användarens beskrivning → komponentlista ("golv 200 m2")
2. **Baseline-agenten** slår upp varje komponent i Boverket → standardreferens
3. **Alternatives-agenten** får **förkategoriserade EPD:er** för komponentens kategori (t.ex. alla 20 golv-EPD:er med GWP-värden) direkt i prompten
4. LLM:en **resonerar som expert**: väljer de 2-4 mest relevanta, beräknar total CO2e, motiverar varför
5. Återbruksalternativ kompletteras från lokal data

LLM:en ser alltså riktiga produkter med verifierade GWP-värden och fattar beslut baserat på dem, istället för att fabricera siffror.

### Dataflöde

```
Användare: "Renovera skolkök, 200 m2 golv, 15 fönster, ny ventilation"
    │
    ▼
[Intake] → Project(components=[golv 200m2, fönster 15st, ventilation 50lm])
    │
    ▼
[Baslinje] → Boverket Typical: vinylgolv = 12.5 CO2e/m2
           → 12.5 × 200 = 2500 kg CO2e (standardreferens)
    │
    ▼
[Alternativ] → LLM:en får 20 golv-EPD:er med GWP-värden
             → Väljer: Golvabia Maxwear 12.9 CO2e/m2, Tarkett iQ 5.5 CO2e/m2...
             → Beräknar total besparing per alternativ
    │
    ▼
[Sammanställning] → Baslinje: 2500 kg, Bästa alternativ: 1100 kg, Besparing: 56%
    │
    ▼
[Rapport] → Markdown med tabeller, EPD-referenser, rekommendationer
```

## Klimatdata: två databaser med olika roller

### Boverkets klimatdatabas — baslinjen

- **Roll**: Sätter referenspunkten. "Vad kostar standardmaterial klimatmässigt?"
- **API**: `api.boverket.se/klimatdatabas` (öppet, ingen nyckel)
- **Innehåll**: ~200 generiska byggprodukter med GWP A1-A3 (Typical och Conservative)
- **Vi använder**: Typical-värden (inte Conservative +25%)
- **Cache**: Pre-populerad i `climate_cache.db` (661 poster inkl. synonymer), uppdateras var 30:e dag
- **Används av**: `baseline.py` via `ClimateProvider`

### Environdec EPD-databas — alternativ med verifierade värden

- **Roll**: Ger LLM:en verkliga produkter att välja bland. "Vilka golv har lägst klimatpåverkan?"
- **API**: `data.environdec.com` (soda4LCA, öppet, ingen nyckel)
- **Fullt index**: 14 263 EPD:er (532 svenska, 116 svenska tillverkare)
- **Förkategoriserat**: 222 EPD:er med hämtade GWP-värden, fördelade på 13 komponentkategorier
- **Sparas i**: `epd_alternatives.json` (bundlad med deploy, ~60 KB)
- **Används av**: `alternatives.py` — matas direkt i LLM-prompten per kategori

### Varför förkategoriserade EPD:er?

Vi testade tre approaches:

1. **Programmatisk sökning** (substring-match mot 14k EPD-titlar) — missade de flesta kategorier pga språkbarriär (svenska komponentnamn, engelska EPD-titlar)
2. **Keyword-baserad sökning med ranking** — bättre, men fortfarande beroende av manuellt mappade termer
3. **Förkategoriserat + LLM resonerar** (nuvarande) — alla 13 kategorier täckta, LLM:en fattar bättre beslut med fullständig data

Approach 3 vann för att den låter LLM:en göra det den är bra på (resonera, jämföra, välja) istället för att försöka lösa sökning programmatiskt.

### Lokal fallback — `climate_data.py`

- **Roll**: Fallback för baslinjen om Boverket-API:t är nere, och återbruksdata för alternativ
- **Innehåll**: ~30 material med confidence-märkning (high/medium/low)
- **Begränsning**: Statisk, kräver kodändringar vid uppdatering

### Confidence-nivåer

| Nivå | Betydelse | Källa |
|------|-----------|-------|
| `high` | Verifierbar EPD eller officiell databas | Boverket API, Environdec EPD med registreringsnummer |
| `medium` | Lokalt verifierad data | `climate_data.py` med korrekt källreferens |
| `low` | Estimat eller overifierad källa | Generisk EPD utan nummer, LLM-estimat |

### Enhetsomräkning

Boverket och Environdec rapporterar ofta i **kg CO2e/kg**. AIda behöver **kg CO2e per funktionell enhet** (m2, st, lm).

```
CO2e/m2 = CO2e/kg × densitet (kg/m3) × tjocklek (m)
CO2e/st = CO2e/kg × typisk vikt (kg)
CO2e/lm = CO2e/kg × vikt per meter (kg/m)
```

Densitet hämtas i första hand från datakällan (Boverket har `Conversions`-fält). I andra hand används typiska densiteter per komponentkategori (`unit_conversion.py`).

Enhetsomräkning sker i baslinjen (Boverket → funktionell enhet). För alternativ hanterar LLM:en omräkningen baserat på EPD-data den får.

## Filstruktur

```
src/aida/
├── agents/
│   ├── intake.py              # Steg 1: Fritext → strukturerat projekt
│   ├── baseline.py            # Steg 2: Baslinjeberäkning (Boverket Typical)
│   ├── alternatives.py        # Steg 3: LLM väljer alternativ från EPD-data
│   ├── aggregate.py           # Steg 4: Sammanställning
│   └── report.py              # Steg 5: Markdown-rapport
├── data/
│   ├── climate_provider.py    # Layered lookup: Boverket → Environdec → lokal
│   ├── boverket_client.py     # Boverkets klimatdatabas API-klient
│   ├── environdec_client.py   # Environdec soda4LCA API-klient + indexhantering
│   ├── climate_cache.py       # SQLite cache (/tmp-fallback på Vercel)
│   ├── climate_cache.db       # Pre-populerad med 661 Boverket-poster
│   ├── epd_alternatives.json  # 222 förkategoriserade EPD:er med GWP (13 kategorier)
│   ├── environdec_index.json  # Fullt EPD-index (14k poster, metadata)
│   ├── climate_data.py        # Statisk fallback + återbruksdata (~30 material)
│   └── unit_conversion.py     # kg → m2/st/lm konvertering + typiska densiteter
├── web/
│   └── app.py                 # Flask-app (UI + API-endpoints)
├── models.py                  # Dataklasser (Project, Baseline, etc.)
├── api_client.py              # Claude API-wrapper
└── cli.py                     # CLI-verktyg
```

## Deploy

- **Repo**: `henricbarkman/aida-klimatkalkyl` (syncat från generalassistant)
- **Deploy-script**: `bin/deploy.sh` (rsync + push, Vercel bygger automatiskt)
- **Runtime**: `@vercel/python` (Flask serverless)
- **Lambda-storlek**: ~5.3 MB (under 15 MB limit)
- **URL**: https://aida-klimatkalkyl.vercel.app

| Vercel-begränsning | Lösning |
|---------------------|---------|
| Read-only filsystem | SQLite-cachen kopieras till `/tmp` vid cold start |
| Stateless mellan anrop | Boverket-data pre-populerad i `climate_cache.db` |
| 10-60s timeout | Agenterna kör Claude API-anrop, inte tunga synk-jobb |
| Inget persistent filsystem | EPD-data bundlad som JSON i deploy |

## Öppna frågor

- **Baslinje vs kommunens verklighet**: NollCO2-baslinjen representerar standardmaterial, men Karlstads kommun kanske redan gör bättre val. Hur ska vi förhålla oss till det? (JJ + Henric + Ida Lund bollar)
- **Återbruksintegration**: Återbruksdata kommer idag från lokal `climate_data.py`. Framtid: live-sökning mot CCBuild/Palats/Sola.
- **Forbo/Swedoor saknas i Environdec**: Dessa tillverkare har EPD:er via EPD Norge eller egna hemsidor, inte via Environdec. Framtida komplettering.
- **LLM-fallback för baslinjen**: Ännu ej implementerad. Tänkt som sista utväg för ovanliga produkter som saknas i Boverket.
- **Fler EPD:er**: Nuvarande 222 förkategoriserade EPD:er täcker de vanligaste produkterna. Kan utökas genom att hämta fler per kategori.

## Kvalitetsprinciper

1. **Varje siffra ska ha en källa.** Ingen "ungefär 15 kg CO2e" utan referens.
2. **Estimat ska märkas.** `confidence: "low"` + ärlig källbeskrivning.
3. **Fel data är värre än ingen data.** Bättre att returnera `None` än ett fabricerat värde.
4. **Baslinjen ska vara standardreferens, inte worst case.** Boverket Typical, inte fabricerade maxvärden.
5. **LLM:en är experten, inte sökmotorn.** Ge den data och låt den resonera, istället för att programmera söklogik.
6. **Svenska/nordiska produkter först.** Geo-preferens speglar användarkontexten.
