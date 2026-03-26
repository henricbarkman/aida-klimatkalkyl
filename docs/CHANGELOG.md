# AIda Changelog

## 2026-03-19: Klimatdata-arkitektur v1

Stort arbete med att gå från hårdkodade klimatvärden till verifierade EPD-data.

### Vad vi byggde

**Boverket API-integration** (`boverket_client.py`)
- Koppling mot `api.boverket.se/klimatdatabas` (öppet, ingen nyckel)
- Hämtar ~200 generiska byggprodukter med GWP A1-A3 Typical-värden
- 661 poster (inkl. synonymer) pre-populerade i SQLite-cache
- Används för baslinjen (NollCO2-principen)

**Environdec EPD-integration** (`environdec_client.py`)
- Koppling mot `data.environdec.com` (soda4LCA, öppet, ingen nyckel)
- Indexerar 14 263 EPD:er (532 svenska, 116 svenska tillverkare)
- Förkategoriserade 222 EPD:er med hämtade GWP-värden i 13 komponentkategorier (`epd_alternatives.json`)
- Används av alternatives-agenten som underlag för LLM:en att resonera kring

**Redesignad alternatives-agent** (`alternatives.py`)
- Tidigare: programmatisk sökning som bara hittade 2/13 kategorier (golv, belysning)
- Nu: LLM:en får alla relevanta EPD:er per kategori i prompten och väljer själv
- Alla 13 kategorier täckta med verifierade EPD-data

**Klimatdata-validering och fix** (`climate_data.py`)
- Korrigerade 3 felaktiga värden (Forbo Marmoleum, Swedoor innerdörr, lertegel Monier)
- Tog bort 1 fabricerad källa (kylsystem CO2/propan)
- Sänkte confidence till "low" på alla ~15 poster med overifierade källor

**Enhetsomräkning** (`unit_conversion.py`)
- Konverterar kg CO2e/kg → funktionella enheter (m2, st, lm)
- Typiska densiteter som fallback när datakällan inte anger det
- Används av baslinjeberäkningen (Boverket-data ofta i kg)

**Vercel-deploy**
- SQLite-cache kopieras till `/tmp` (Vercel deploy-bundle är read-only)
- Boverket-data pre-populerad, ingen API-sync vid cold start
- Deploy-script: `bin/deploy.sh` (rsync till aida-klimatkalkyl repo)
- Total bundlestorlek: ~5.3 MB (under 15 MB limit)

### Designbeslut och varför

**Boverket Typical (inte Conservative) för baslinjen**
Per NollCO2 Manual 1.2, sektion 5.2/tabell 3. Typical-värden representerar standardmaterial, inte worst case.

**Förkategoriserade EPD:er istället för sökning i realtid**
Vi testade tre sökmetoder. Substring-sökning missade 11/13 kategorier (svenska termer, engelska EPD-titlar). Keyword-ranking missade fortfarande 4/13. Förkategorisering + LLM-resonemang täcker alla 13 och låter LLM:en göra det den är bra på.

**LLM:en är expert, inte sökmotor**
Användaren beskriver ett behov ("fönster"). LLM:en (alternatives-agenten) får alla relevanta EPD:er och resonerar kring vilka som passar bäst. Ingen programmatisk söklogik behövs.

**Environdec före EC3/OpenEPD**
Environdec (EPD International) har starkast svensk/europeisk täckning. Samma system som svenska byggproducenter registrerar sina EPD:er i. Gratis API utan nyckel.

### Kända begränsningar

- **Forbo och Swedoor saknas i Environdec** — deras EPD:er finns via EPD Norge eller egna hemsidor
- **222 EPD:er** täcker de vanligaste produkterna men inte allt — kan utökas
- **Återbruk** använder fortfarande lokal hårdkodad data, inte live-sökning mot marknadsplatser
- **Enhetsomräkning** bygger på typiska densiteter som fallback — inte alltid exakt
- **Baslinjevalet** (NollCO2 vs kommunens verklighet) är en öppen fråga som JJ utreder
