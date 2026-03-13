# PRD: AIda — Klimatkalkyl och beslutsstöd för ombyggnationer

**Phase**: AI för klimatet, Case 1b
**Status**: Draft
**Created**: 2026-03-13
**Author**: Henric + Demi
**Scale**: Large

## Problem Statement

Byggnationer är kommunens största klimatutsläppspost. Vid ombyggnationer (fönsterbyte, tilläggsisolering, ändrad rumsfunktion) fattar förvaltare och byggledare snabba beslut utan klimatdata. Nybyggnationer har konsulter och certifieringar. Ombyggnationer har ingenting.

En Excel-modell ("Gör fler klimatsmarta val") finns men tar för lång tid att använda i praktiken. Resultatet: klimatperspektivet faller bort ur beslutet, eller kommer in för sent för att påverka.

## Users & Context

**Primära användare**: Förvaltare och byggledare på fastighetsavdelningen, Karlstads kommun. De planerar och beställer ombyggnationer (ej nybyggnation). Små projekt, snabba beslut, begränsad budget.

**Sekundära användare**: Miljöstrateger (Ida Vestlund, Sara Hammarström) som stöttar och kvalitetsgranskar.

**Kontext**: Användaren har ett ombyggnadsprojekt (ex. renovering av skolkök, fönsterbyte i kontorsbyggnad) och vill förstå klimatpåverkan av olika alternativ innan beställning.

## Success Criteria

- [ ] SC-1: En användare utan klimatexpertis kan gå från projektbeskrivning till jämförbar klimat/ekonomi-data på under 15 minuter
- [ ] SC-2: Varje byggnadskomponent presenteras med minst baslinje (nyproduktion) och ett klimatoptimerat alternativ
- [ ] SC-3: Klimatdata baseras på verifierbara källor (EPD, Boverket, NollCO2) med referens
- [ ] SC-4: Användaren kan välja alternativ per komponent och se aggregerad klimat- och kostnadseffekt
- [ ] SC-5: Systemet producerar ett exporterbart beslutsunderlag

## Behavioral Acceptance Criteria

_EDD-compatible format._

| ID | Type | Criterion |
|----|------|-----------|
| AC-1 | MUST | Given a natural-language project description, the system extracts building type, area (BTA), and list of renovation components |
| AC-2 | MUST | For each identified component, the system calculates a baseline CO2e value (kg) representing all-new materials without climate ambitions |
| AC-3 | MUST | For each component, the system presents at least one alternative to the baseline with lower CO2e, including source reference |
| AC-4 | MUST | Each alternative includes both CO2e (kg) and estimated cost (SEK) |
| AC-5 | MUST | The user can select a preferred alternative per component and the system shows aggregated totals (total CO2e, total cost, total savings vs baseline) |
| AC-6 | MUST | The system generates a structured summary report that can be exported (PDF or markdown) |
| AC-7 | SHOULD | Reuse alternatives include real availability from marketplace sources (Sola/CCBuild or equivalent) |
| AC-8 | SHOULD | Baseline calculations follow NollCO2-metoden principles (worst-case new production as reference) |
| AC-9 | SHOULD | The system explains its reasoning and data sources for each calculation |
| AC-10 | SHOULD | The UI shows progress through analysis steps so the user knows what's happening |
| AC-11 | SHOULD | The documentation output is formatted for use in procurement documents or tjänsteskrivelser |

## User Journeys

### Journey 1: Standard renovation analysis (happy path)

1. User opens AIda web interface
2. User describes project in free text: "Vi ska renovera skolkök i Kronoparksskolan, ca 200 kvm. Byta golv, byta ut diskmaskin och kylanläggning, renovera innerväggar."
3. System confirms understanding, shows extracted components (golv, diskmaskin, kylanläggning, innerväggar) and asks user to verify
4. System calculates baseline per component (all new, no climate optimization) and displays in results panel
5. System searches for reuse alternatives and climate-optimized new materials, presents options per component in a comparison table
6. Each row shows: component name, alternative name, CO2e (kg), cost (SEK), savings vs baseline (%), source
7. User selects preferred alternative per component via radio buttons
8. Aggregated totals update live: total CO2e, total cost, total savings
9. User clicks "Generera rapport"
10. System produces a structured PDF with project summary, component breakdown, selected alternatives, total impact, and source references

### Journey 2: Incomplete or vague project description

1. User writes: "Vi ska fixa lite i en skola"
2. System asks clarifying questions: building type confirmed (skola), asks for approximate area, what kind of renovation (what's being changed)
3. User provides more detail: "Fönsterbyte, kanske 50 fönster, byggnaden är ca 3000 kvm"
4. System proceeds with the identified scope

### Journey 3: No reuse available

1. User describes a niche component where no reuse options exist
2. System shows baseline + climate-optimized new purchase (no reuse row)
3. System notes: "Inga återbruksalternativ hittades för [component]. Visar nyproducerade alternativ med lägre klimatavtryck."

## User Stories

- Som förvaltare vill jag snabbt förstå klimatpåverkan av mitt ombyggnadsprojekt, så att jag kan ta hänsyn till klimat i beställningen
- Som byggledare vill jag jämföra alternativ per komponent (kostnad vs klimat), så att jag kan motivera mina val
- Som miljöstrateg vill jag att underlaget har källhänvisningar, så att jag kan kvalitetsgranska beräkningarna
- Som beställare vill jag kunna exportera ett beslutsunderlag, så att jag kan bifoga det i tjänsteskrivelsen

## Domain Constraints

- **Platform**: Webb-baserad (tillgänglig via webbläsare, ingen installation)
- **Språk**: Svenska UI och output. Engelska EPD-data OK.
- **Datakällor**: Boverkets klimatdatabas (öppen), EPD-databaser (environdec.com, eco-platform.org), NollCO2-metoden (PDF). Sola/CCBuild för återbruk (Palats API finns).
- **Beräkningsmetod**: NollCO2-metoden som referensram för baseline. Enheter: kg CO2e, SEK.
- **Privacy**: Inga personuppgifter i projektbeskrivningar. Inga externa API:er som lagrar data.
- **Cost**: Claude API-kostnader. Inga dyra tredjepartstjänster.
- **Precision**: Systemet ger uppskattningar baserade på generiska/typiska värden, inte exakta projekteringsberäkningar. Tydlig disclaimertext.

## Scope

### v1 (Must Have)
- [ ] Conversational intake som extraherar projektparametrar och komponenter
- [ ] Baslinjeberäkning per komponent (kg CO2e) baserad på generiska EPD-data
- [ ] Minst ett alternativ per komponent (climate-optimized new materials)
- [ ] Kostnadsbedömning per alternativ (SEK)
- [ ] Comparison table med radio-select per komponent
- [ ] Aggregerad sammanställning (total CO2e, kostnad, besparing)
- [ ] Exporterbar rapport (PDF eller markdown)

### v1.1 (Should Have)
- [ ] Live-sökning i återbruksmarknadsplatser (Sola/CCBuild)
- [ ] Progress tracker som visar vilka analyssteg som körs
- [ ] Förklaringar och källhänvisningar inline i comparison table
- [ ] Dokumentationsagent som formaterar text för upphandling/tjänsteskrivelser
- [ ] Spara och ladda projekt

### Future
- Integration med kommunens system (Azure OpenAI, Copilot Studio)
- NollCO2-certifiering-kompabilitet
- Multi-user med projekthistorik
- Koppling till Svalna CIS för uppföljning
- "Gör fler klimatsmarta val"-modellen integrerad
- Byggvarubedömningen/eBVD-integration

## Dependencies

- Claude API (Anthropic) för agentflödet
- Boverkets klimatdatabas (öppen data, kan behöva scraping eller lokal kopia)
- NollCO2-metoden (PDF som referensdokument)
- Generiska EPD-data / klimatdata per materialtyp

## Risks & Open Questions

- **EPD-datakvalitet**: Generiska värden kan skilja sig mycket från specifika produkter. Mitigering: tydlig disclaimer + källhänvisning.
- **Kostnadsbedömningar**: Marknadspriser varierar. Mitigering: ange intervall, inte exakta priser. Datera uppskattningen.
- **NollCO2-tolkningar**: Metoden har nyanser. Mitigering: förenkla till baslinjeprincip (allt nytt, ingen ambition) och vara transparent om förenklingar.
- **Palats API-tillgänglighet**: API:et är dokumenterat men okänt om det är stabilt/öppet. v1 klarar sig utan (deferred to v1.1).
- **Användaracceptans**: Förvaltare kan vara skeptiska till AI-genererade klimatsiffror. Mitigering: alltid visa källa, möjliggör manuell override.
