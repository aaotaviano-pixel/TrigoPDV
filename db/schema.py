"""Schema SQLite versionado do PDV.

Os nomes principais seguem o enunciado em português para que consultas de
suporte e cópias de segurança sejam fáceis de auditar localmente.
"""

SCHEMA_VERSION = 9

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS produtos (
    gtin TEXT PRIMARY KEY,
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    marca TEXT,
    preco REAL NOT NULL DEFAULT 0.0 CHECK (preco >= 0),
    estoque REAL NOT NULL DEFAULT 0.0,
    data_validade TEXT,
    unidade TEXT NOT NULL DEFAULT 'UN' CHECK (unidade IN ('UN', 'KG')),
    estoque_controlado INTEGER NOT NULL DEFAULT 1 CHECK (estoque_controlado IN (0, 1)),
    item_balcao INTEGER NOT NULL DEFAULT 0 CHECK (item_balcao IN (0, 1)),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    origem TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'open_food_facts')),
    tipo_codigo TEXT NOT NULL DEFAULT 'GTIN' CHECK (tipo_codigo IN ('GTIN', 'PLU')),
    categoria TEXT NOT NULL DEFAULT 'Outros',
    subcategoria TEXT,
    detalhes_embalagem TEXT,
    validacao_codigo TEXT NOT NULL DEFAULT 'PENDENTE'
        CHECK (validacao_codigo IN ('PENDENTE', 'VALIDO_ESTRUTURAL', 'CONFIRMADO', 'INCOMPATIVEL', 'VALIDO_INTERNO')),
    fonte_validacao TEXT,
    validado_em TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cache_gtin (
    gtin TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('ENCONTRADO', 'NAO_ENCONTRADO', 'INDISPONIVEL')),
    fonte TEXT NOT NULL,
    nome TEXT,
    marca TEXT,
    categoria TEXT,
    detalhes_embalagem TEXT,
    consultado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expira_em TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 1 CHECK (tentativas > 0)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    login TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(login)) > 0),
    senha_hash TEXT NOT NULL,
    perfil TEXT NOT NULL CHECK (perfil IN ('admin', 'caixa')),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    deve_trocar_senha INTEGER NOT NULL DEFAULT 0 CHECK (deve_trocar_senha IN (0, 1)),
    codigo_recuperacao_hash TEXT,
    tentativas_login_falhas INTEGER NOT NULL DEFAULT 0,
    login_falhas_janela_inicio TEXT,
    login_bloqueado_ate TEXT,
    recuperacao_falhas INTEGER NOT NULL DEFAULT 0 CHECK (recuperacao_falhas >= 0),
    recuperacao_janela_inicio TEXT,
    recuperacao_bloqueado_ate TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installation_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    installation_id TEXT NOT NULL UNIQUE CHECK (length(installation_id) = 36),
    state TEXT NOT NULL CHECK (state IN ('UNINITIALIZED', 'READY')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provisioned_at TEXT
);

INSERT OR IGNORE INTO installation_state(singleton, installation_id, state, created_at, provisioned_at)
VALUES (
    1,
    lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' ||
    lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' ||
    lower(hex(randomblob(6))),
    'UNINITIALIZED',
    CURRENT_TIMESTAMP,
    NULL
);

CREATE TABLE IF NOT EXISTS caixas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    data_abertura TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    data_fechamento TEXT,
    fundo_inicial REAL NOT NULL CHECK (fundo_inicial >= 0),
    valor_informado REAL,
    valor_esperado REAL,
    quebra REAL,
    justificativa TEXT,
    status TEXT NOT NULL DEFAULT 'ABERTO' CHECK (status IN ('ABERTO', 'FECHADO'))
);

CREATE TABLE IF NOT EXISTS vendas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id INTEGER NOT NULL REFERENCES caixas(id),
    operador_id INTEGER REFERENCES usuarios(id),
    total REAL NOT NULL CHECK (total >= 0),
    forma_pagamento TEXT NOT NULL CHECK (forma_pagamento IN ('Dinheiro', 'PIX', 'Cartão')),
    valor_recebido REAL,
    troco REAL NOT NULL DEFAULT 0.0 CHECK (troco >= 0),
    data_venda TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'CONFIRMADA' CHECK (status IN ('CONFIRMADA', 'CANCELADA')),
    chave_idempotencia TEXT,
    total_manual REAL NOT NULL DEFAULT 0 CHECK (total_manual >= 0),
    autorizador_excecao_id INTEGER REFERENCES usuarios(id),
    motivo_excecao TEXT,
    fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS itens_venda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venda_id INTEGER NOT NULL REFERENCES vendas(id),
    gtin TEXT REFERENCES produtos(gtin),
    nome_produto TEXT NOT NULL,
    unidade TEXT NOT NULL DEFAULT 'UN' CHECK (unidade IN ('UN', 'KG')),
    quantidade REAL NOT NULL CHECK (quantidade > 0),
    preco_unitario REAL NOT NULL CHECK (preco_unitario >= 0),
    subtotal REAL NOT NULL CHECK (subtotal >= 0),
    tipo_lancamento TEXT NOT NULL DEFAULT 'CATALOGO'
        CHECK (tipo_lancamento IN ('CATALOGO', 'MANUAL')),
    codigo_informado TEXT,
    preco_original REAL CHECK (preco_original IS NULL OR preco_original >= 0)
);

CREATE TABLE IF NOT EXISTS movimentacoes_caixa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id INTEGER NOT NULL REFERENCES caixas(id),
    usuario_id INTEGER REFERENCES usuarios(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('SANGRIA', 'SUPRIMENTO')),
    valor REAL NOT NULL CHECK (valor > 0),
    observacao TEXT,
    data_movimentacao TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    chave_idempotencia TEXT,
    fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS cancelamentos_venda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venda_id INTEGER NOT NULL UNIQUE REFERENCES vendas(id),
    operador_id INTEGER NOT NULL REFERENCES usuarios(id),
    autorizador_id INTEGER NOT NULL REFERENCES usuarios(id),
    motivo TEXT NOT NULL CHECK (length(trim(motivo)) BETWEEN 8 AND 250),
    chave_idempotencia TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    data_cancelamento TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backups_caixa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caixa_id INTEGER NOT NULL UNIQUE REFERENCES caixas(id),
    solicitado_por INTEGER REFERENCES usuarios(id),
    status TEXT NOT NULL DEFAULT 'PENDENTE'
        CHECK (status IN ('PENDENTE', 'CONCLUIDO', 'FALHOU')),
    tentativas INTEGER NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
    ultimo_erro TEXT,
    arquivo TEXT,
    solicitado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    concluido_em TEXT
);

CREATE TABLE IF NOT EXISTS impressao_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venda_id INTEGER NOT NULL REFERENCES vendas(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('ORIGINAL', 'SEGUNDA_VIA')),
    solicitado_por INTEGER REFERENCES usuarios(id),
    chave_idempotencia TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDENTE'
        CHECK (status IN ('PENDENTE', 'IMPRESSO', 'FALHOU')),
    payload TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
    ultimo_erro TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    impresso_em TEXT
);

CREATE TABLE IF NOT EXISTS logs_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER REFERENCES usuarios(id),
    usuario_login TEXT,
    acao TEXT NOT NULL,
    entidade TEXT NOT NULL,
    entidade_id TEXT,
    detalhes TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_produtos_nome ON produtos(nome COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_produtos_validade ON produtos(data_validade);
CREATE INDEX IF NOT EXISTS idx_produtos_balcao ON produtos(item_balcao, ativo);
CREATE INDEX IF NOT EXISTS idx_caixas_usuario_status ON caixas(usuario_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_caixa_aberto_por_usuario
    ON caixas(usuario_id) WHERE status = 'ABERTO';
CREATE UNIQUE INDEX IF NOT EXISTS uq_caixa_aberto_global
    ON caixas(status) WHERE status = 'ABERTO';
CREATE INDEX IF NOT EXISTS idx_vendas_caixa ON vendas(caixa_id, data_venda);
CREATE INDEX IF NOT EXISTS idx_vendas_data ON vendas(data_venda);
CREATE UNIQUE INDEX IF NOT EXISTS uq_vendas_chave_idempotencia
    ON vendas(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_itens_venda_gtin ON itens_venda(gtin);
CREATE INDEX IF NOT EXISTS idx_movimentacoes_caixa ON movimentacoes_caixa(caixa_id, data_movimentacao);
CREATE UNIQUE INDEX IF NOT EXISTS uq_movimentacoes_chave_idempotencia
    ON movimentacoes_caixa(chave_idempotencia) WHERE chave_idempotencia IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cancelamentos_venda_data
    ON cancelamentos_venda(data_cancelamento);
CREATE INDEX IF NOT EXISTS idx_backups_caixa_status
    ON backups_caixa(status, solicitado_em);
CREATE INDEX IF NOT EXISTS idx_impressao_outbox_status
    ON impressao_outbox(status, criado_em);
CREATE UNIQUE INDEX IF NOT EXISTS uq_impressao_original_por_venda
    ON impressao_outbox(venda_id) WHERE tipo = 'ORIGINAL';
CREATE INDEX IF NOT EXISTS idx_auditoria_data ON logs_auditoria(criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_auditoria_entidade ON logs_auditoria(entidade, entidade_id);
CREATE INDEX IF NOT EXISTS idx_produtos_classificacao ON produtos(categoria, tipo_codigo, ativo);
"""


RATE_LIMIT_TRIGGER_STATEMENTS = (
    "CREATE TRIGGER IF NOT EXISTS trg_usuarios_rate_limit_insert "
    "BEFORE INSERT ON usuarios FOR EACH ROW "
    "WHEN NEW.tentativas_login_falhas < 0 OR NEW.recuperacao_falhas < 0 "
    "BEGIN SELECT RAISE(ABORT, 'contador de autenticacao invalido'); END",
    "CREATE TRIGGER IF NOT EXISTS trg_usuarios_rate_limit_update "
    "BEFORE UPDATE OF tentativas_login_falhas, recuperacao_falhas ON usuarios FOR EACH ROW "
    "WHEN NEW.tentativas_login_falhas < 0 OR NEW.recuperacao_falhas < 0 "
    "BEGIN SELECT RAISE(ABORT, 'contador de autenticacao invalido'); END",
)


def fresh_schema_statements() -> tuple[str, ...]:
    """Retorna o schema-base em comandos individuais, seguros em transação."""

    base = tuple(statement.strip() for statement in SCHEMA_SQL.split(";") if statement.strip())
    return base + RATE_LIMIT_TRIGGER_STATEMENTS
