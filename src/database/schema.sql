-- database/schema.sql

-- ─────────────────────────────────────────
-- EXTENSION UUID
-- ─────────────────────────────────────────
create extension if not exists "uuid-ossp";


-- ─────────────────────────────────────────
-- COMPANIES (les clients de Kuria)
-- ─────────────────────────────────────────
create table companies (
    id              uuid primary key default uuid_generate_v4(),
    name            text not null,
    sector          text,
    size_employees  integer,
    size_revenue    numeric,

    -- Outils connectés (JSON simple)
    tools_connected jsonb default '{}',

    -- Score de clarté
    clarity_score   integer default 0,

    -- Config des agents (JSON)
    agent_configs   jsonb default '{}',

    created_at      timestamptz default now(),
    updated_at      timestamptz default now()
);


-- ─────────────────────────────────────────
-- DEALS
-- ─────────────────────────────────────────
create table deals (
    id                  text not null,
    company_id          uuid not null references companies(id),

    title               text not null,
    amount              numeric default 0,
    currency            text default 'EUR',

    stage               text not null,
    stage_order         integer default 0,
    probability         numeric default 0,
    probability_real    numeric,

    status              text default 'active',

    created_at          timestamptz,
    last_activity_at    timestamptz,
    closed_at           timestamptz,
    expected_close_date timestamptz,

    owner_id            text default '',
    owner_name          text default '',
    source              text default '',

    connector_source    text not null,
    raw_id              text not null,

    synced_at           timestamptz default now(),

    primary key (id),
    unique (company_id, raw_id, connector_source)
);


-- ─────────────────────────────────────────
-- CONTACTS
-- ─────────────────────────────────────────
create table contacts (
    id                  text not null,
    company_id          uuid not null references companies(id),

    email               text not null,
    first_name          text default '',
    last_name           text default '',

    company_name        text default '',
    company_size        integer,
    company_revenue     numeric,
    sector              text default '',

    source              text default '',
    source_detail       text default '',

    score               integer,
    score_label         text,
    score_reason        text default '',

    created_at          timestamptz,
    last_activity_at    timestamptz,

    connector_source    text not null,
    raw_id              text not null,

    synced_at           timestamptz default now(),

    primary key (id),
    unique (company_id, raw_id, connector_source)
);


-- ─────────────────────────────────────────
-- INVOICES
-- ─────────────────────────────────────────
create table invoices (
    id                  text not null,
    company_id          uuid not null references companies(id),

    amount              numeric not null,
    amount_paid         numeric default 0,
    currency            text default 'EUR',

    client_id           text default '',
    client_name         text default '',

    status              text default 'sent',

    issued_at           timestamptz,
    due_at              timestamptz,
    paid_at             timestamptz,
    payment_delay_days  integer,

    connector_source    text not null,
    raw_id              text not null,

    synced_at           timestamptz default now(),

    primary key (id),
    unique (company_id, raw_id, connector_source)
);


-- ─────────────────────────────────────────
-- TASKS
-- ─────────────────────────────────────────
create table tasks (
    id                  text not null,
    company_id          uuid not null references companies(id),

    title               text not null,
    description         text default '',

    assignee_id         text default '',
    assignee_name       text default '',

    status              text default 'todo',

    created_at          timestamptz,
    due_at              timestamptz,
    completed_at        timestamptz,
    cycle_time_days     numeric,

    connector_source    text not null,
    raw_id              text not null,

    synced_at           timestamptz default now(),

    primary key (id),
    unique (company_id, raw_id, connector_source)
);


-- ─────────────────────────────────────────
-- EXPENSES
-- ─────────────────────────────────────────
create table expenses (
    id                  text not null,
    company_id          uuid not null references companies(id),

    amount              numeric not null,
    currency            text default 'EUR',

    vendor              text default '',
    category            text default '',
    is_recurring        boolean default false,

    date                timestamptz,

    connector_source    text not null,
    raw_id              text not null,

    synced_at           timestamptz default now(),

    primary key (id),
    unique (company_id, raw_id, connector_source)
);


-- ─────────────────────────────────────────
-- EVENTS (router inter-agents)
-- ─────────────────────────────────────────
create table events (
    id          uuid primary key default uuid_generate_v4(),
    event_type  text not null,
    company_id  uuid not null references companies(id),
    payload     jsonb default '{}',
    processed   boolean default false,
    created_at  timestamptz default now()
);

create index events_unprocessed 
    on events(company_id, processed) 
    where processed = false;


-- ─────────────────────────────────────────
-- UPDATED_AT AUTOMATIQUE
-- ─────────────────────────────────────────
create or replace function update_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger companies_updated_at
    before update on companies
    for each row execute function update_updated_at();




-- À ajouter dans database/schema.sql

-- ─────────────────────────────────────────
-- ACTION LOGS (tout ce que Kuria a fait)
-- ─────────────────────────────────────────
create table action_logs (
    id              uuid primary key default uuid_generate_v4(),
    action_type     text not null,
    level           text not null,
    company_id      uuid not null references companies(id),
    agent           text not null,
    payload         jsonb default '{}',
    status          text not null,
    result          jsonb default '{}',
    error           text default '',
    attempts        integer default 1,
    executed_at     timestamptz default now()
);

create index action_logs_company
    on action_logs(company_id, executed_at desc);

create index action_logs_agent
    on action_logs(company_id, agent, executed_at desc);


-- ─────────────────────────────────────────
-- PENDING ACTIONS (niveau B et C en attente)
-- ─────────────────────────────────────────
create table pending_actions (
    id              uuid primary key default uuid_generate_v4(),
    action_type     text not null,
    level           text not null,
    company_id      uuid not null references companies(id),
    agent           text not null,
    payload         jsonb default '{}',
    description     text default '',
    preview         jsonb default '{}',
    status          text default 'pending',
    result          jsonb default '{}',
    created_at      timestamptz default now(),
    executed_at     timestamptz
);

create index pending_actions_company
    on pending_actions(company_id, status)
    where status = 'pending';


