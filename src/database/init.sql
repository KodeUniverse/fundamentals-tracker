-- Fundamentals Tracker schema.
--
-- Layered deliberately:
--   entities / concepts  dimensional reference data
--   facts                the raw XBRL layer, one row per fact per filing
--
-- Metric resolution (revenue -> gross margin -> YoY growth) happens above this
-- layer, in SQL or Python, so the stored data stays a faithful mirror of what
-- SEC EDGAR reported. Nothing here aggregates or picks a "preferred" value.

CREATE TABLE IF NOT EXISTS entities (
    cik         BIGINT PRIMARY KEY,
    entity_name TEXT NOT NULL,
    ticker      TEXT
);

COMMENT ON TABLE entities IS 'One row per SEC registrant in the tracked universe.';
COMMENT ON COLUMN entities.cik IS 'SEC Central Index Key. BIGINT: CIKs have exceeded int32.';
COMMENT ON COLUMN entities.ticker IS 'Display ticker; not unique (e.g. GOOGL and GOOG share a CIK).';

CREATE TABLE IF NOT EXISTS concepts (
    id          BIGSERIAL PRIMARY KEY,
    taxonomy    TEXT NOT NULL,
    name        TEXT NOT NULL,
    label       TEXT,
    description TEXT,
    CONSTRAINT concepts_taxonomy_name_key UNIQUE (taxonomy, name)
);

COMMENT ON TABLE concepts IS 'XBRL tags seen in companyfacts: us-gaap, dei, srt, ecd, ffd, invest, ifrs-full.';
COMMENT ON COLUMN concepts.name IS 'Local tag name without namespace prefix, e.g. RevenueFromContractWithCustomerExcludingAssessedTax.';

CREATE TABLE IF NOT EXISTS facts (
    id         BIGSERIAL PRIMARY KEY,
    cik        BIGINT NOT NULL REFERENCES entities (cik),
    concept_id BIGINT NOT NULL REFERENCES concepts (id),
    unit       TEXT NOT NULL,
    value      NUMERIC NOT NULL,
    start_date DATE,
    end_date   DATE NOT NULL,
    form_type  TEXT NOT NULL,
    filed_date DATE NOT NULL,
    accession  TEXT NOT NULL,
    filing_fy  INTEGER,
    filing_fp  TEXT,
    frame      TEXT,
    CONSTRAINT facts_natural_key
        UNIQUE NULLS NOT DISTINCT (cik, concept_id, unit, start_date, end_date, accession)
);

COMMENT ON TABLE facts IS 'Raw XBRL facts from data.sec.gov companyfacts, one row per fact per reporting filing.';
COMMENT ON COLUMN facts.start_date IS 'Period start. NULL for instant (balance-sheet) facts, ~41% of rows.';
COMMENT ON COLUMN facts.end_date IS 'Period end; also the instant date for balance-sheet facts.';
COMMENT ON COLUMN facts.filing_fy IS 'Fiscal year OF THE FILING, not of the fact. A FY2023 fact reported in the FY2025 10-K carries filing_fy = 2025.';
COMMENT ON COLUMN facts.filing_fp IS 'Fiscal period of the filing (FY, Q1..Q4), not of the fact.';
COMMENT ON COLUMN facts.accession IS 'EDGAR accession number of the filing that reported this fact.';
COMMENT ON COLUMN facts.unit IS 'Raw unit string from the API, e.g. USD, USD/shares, shares.';

-- One company's time series for a concept.
CREATE INDEX IF NOT EXISTS facts_cik_concept_end_idx ON facts (cik, concept_id, end_date);
-- Cross-company comparisons for a given period ("highest gross margin last year").
CREATE INDEX IF NOT EXISTS facts_concept_end_idx ON facts (concept_id, end_date);
-- Everything reported for one company at one period.
CREATE INDEX IF NOT EXISTS facts_cik_end_idx ON facts (cik, end_date);
