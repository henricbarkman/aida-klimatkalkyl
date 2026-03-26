# AIda: Klimatkalkyl och beslutsstöd för ombyggnationer

Delprojekt inom AI för klimatet.

## Hosting

**Live**: [aida-klimatkalkyl.vercel.app](https://aida-klimatkalkyl.vercel.app)

Deployas via Vercel, kopplad till GitHub-repot `henricbarkman/aida-klimatkalkyl`. Push till main triggar auto-deploy.

- **Runtime**: `@vercel/python` (Flask)
- **Entry point**: `api/index.py` (importerar `src/aida/web/app.py`)
- **Env vars i Vercel**: `ANTHROPIC_API_KEY`, `AIDA_SECRET_KEY`, plus Supabase-variabler (se nedan)
- **Begränsning**: Vercel serverless har 10s timeout (hobby) / 60s (pro). Längre LLM-anrop kan timeout:a.

## Auth och sparade analyser (Supabase)

Användare kan skapa konto och spara sina analyser. Auth hanteras av Supabase (frontend JS client + backend JWT-validering).

### Env vars

| Variabel | Beskrivning |
|----------|-------------|
| `SUPABASE_URL` | Supabase-projektets URL (ex: `https://xyz.supabase.co`) |
| `SUPABASE_ANON_KEY` | Supabase anon/public key |
| `SUPABASE_JWT_SECRET` | JWT secret för att validera tokens server-side |

Om `SUPABASE_JWT_SECRET` inte finns faller appen tillbaka till gammal lösenordsauth (`AIDA_PASSWORD`).

### Supabase-setup

1. Skapa ett projekt på [supabase.com](https://supabase.com) (free tier)
2. Aktivera Email/Password auth under Authentication > Providers
3. Kör följande SQL i SQL Editor:

```sql
CREATE TABLE analyses (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT 'Nytt projekt',
  status TEXT DEFAULT 'intake',
  project_data JSONB,
  baseline_data JSONB,
  alternatives_data JSONB,
  selections_data JSONB,
  report_markdown TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at
  BEFORE UPDATE ON analyses
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Row Level Security
ALTER TABLE analyses ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users see own analyses"
  ON analyses FOR ALL
  USING (auth.uid() = user_id);
```

4. Lägg till env vars i Vercel (Settings > Environment Variables)
5. Deploya

## User story

Som förvaltare eller byggledare vill jag snabbt kunna förstå och jämföra klimatpåverkan och ekonomi för olika åtgärdsförslag i ett ombyggnadsprojekt, så att jag kan planera med de mest klimatsmarta alternativen som ryms inom budget och tidsplan.

## Problem

Byggnationer är kommunens största klimatutsläppspost. Förvaltare/byggledare fattar många beslut själva (fönsterbyte, tilläggsisolering, ändrad rumsfunktion) men saknar verktyg för klimatberäkningar. Befintlig Excel-modell ("Gör fler klimatsmarta val") är för tidskrävande.

## Behovsägare

- Förvaltare och byggledare på fastighetsavdelningen
- Ida Vestlund, miljöstrateg (intervjuad)
- Gunnar Persson (skapat "Gör fler klimatsmarta val")
- Cornelia Lundgren, verksamhetsutvecklare
- Charlotte Wedberg och Sara Hammarström, miljöstrateger

## Agentflöde (6 steg)

### 1. Project Description Agent — Projektintake
- Första kontaktpunkt, förklarar processen
- Samlar in: byggnadstyp, storlek/BTA, huvudåtgärder
- Tools: Inga
- Output: Strukturerad projektbeskrivning + projektkategorier

### 2. Baseline Agent — Baslinje
- Skapar "nollscenario" — klimatpåverkan om allt köps nytt utan miljöambitioner
- Metod: NollCO2-metoden
- Tools: File search (NollCO2-pdf), webbsök (EPD:er), code interpreter
- Output: Baslinjeberäkning som jämförelsetal

### 3. Reuse Agent — Återbruk
- Identifierar återbrukspotential
- Söker marknadsplatser: Sola byggåterbruk, CCBuild (Palats API tillgängligt)
- Tools: Webbsök (marknadsplatser)
- Output: Återbruksscenario + lista på residuala behov

### 4. Virgin Materials Agent — Klimatoptimerade nyinköp
- Optimerar kvarvarande nyinköp
- Hittar material med lågt klimatavtryck (fossilfritt stål, biobaserat etc)
- Tools: Webbsök
- Output: Optimerat materialscenario

### 5. Economics & Ranking Agent — Ekonomi
- Beräknar kostnader per scenario
- Nyckeltal: kostnad per sparat kg CO2e
- Tools: Webbsök (marknadspriser), code interpreter
- Output: Rankade alternativ med klimat/ekonomi-avvägning

### 6. Documentation Agent — Beställningsunderlag
- Paketerar data till färdiga texter
- Målgrupper: entreprenörer, konsulter, byggledare, beslutsfattare
- Tools: Webbsök (mallar, formuleringar)
- Output: Textblock för upphandling och tjänsteskrivelser

## Guardrails

Mindre modell för att detektera hallucinationer, rensa PII, säkerställa säkert innehåll.

## Datakällor

- "Gör fler klimatsmarta val"-modellen (inspiration, bifogas ej)
- LCA/EPD-data: webbsök EPD:er, Boverkets databas. Framtida: Byggvarubedömningen, eBVD
- CO2-gränsvärden: EKP-mål (pdf), Svalna-data (xls)
- NollCO2-metoden (pdf)

## Prototyp

- Agentflöde byggt i OpenAI Agent Builder — fullt användbart men behöver iteration
- UI-prototyp baserad på Henrics privata projekt — låter användaren se och interagera med agentens uppgifter
- Nästa steg: iterera med referensgrupp, bygga i kommunens system

## Exempelfrågor

- Vilka är de fem största utsläppsposterna i mitt ombyggnadsprojekt?
- Hur mycket CO2e (och kostnad) genererar den här produkten?
- Vad händer med utsläpp och ekonomi om vi tilläggsisolerar en fasad?
- Hur stor klimatbesparing om befintliga ventilationskanaler i stål återbrukas?
- Vilket alternativ ger störst klimatnytta per investerad krona?
- Hur formulerar vi klimatkrav i upphandlingen?
