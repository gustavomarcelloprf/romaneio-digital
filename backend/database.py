import os
import sqlite3
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "romaneio.db")

# -------- Conexão --------
def get_conn() -> sqlite3.Connection:
    """Retorna uma conexão com o banco de dados."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# -------- Schema do Banco de Dados --------
def init_db() -> None:
    """Inicializa o schema do banco de dados, criando as tabelas necessárias."""
    conn = get_conn()
    cur = conn.cursor()
    
    # A loja segue como identidade do tenant, mas as credenciais de login
    # agora vivem na tabela usuarios. senha_hash ficou nullable (legado).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lojas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo      TEXT UNIQUE,
            nome        TEXT NOT NULL,
            senha_hash  TEXT,
            status      TEXT NOT NULL DEFAULT 'pendente',
            endereco    TEXT,
            pix_chave   TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lojas_codigo ON lojas(codigo)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja          TEXT NOT NULL,
            nome          TEXT NOT NULL,
            login         TEXT NOT NULL,
            -- NULL enquanto o convite não for aceito: sem senha não há login.
            senha_hash    TEXT,
            -- 'gerente' já é aceito pelo schema, mas ainda não há UI para criá-lo:
            -- a criação de usuário continua nascendo 'operador'.
            papel         TEXT NOT NULL DEFAULT 'operador' CHECK(papel IN ('admin','operador','gerente')),
            taxa_comissao REAL NOT NULL DEFAULT 0,
            ativo         INTEGER NOT NULL DEFAULT 1,
            -- Convite de primeiro acesso: o admin cria o operador sem senha e
            -- repassa o link /convite/<token>; o operador define a própria senha.
            -- convite_expira é UTC "YYYY-MM-DD HH:MM:SS" (mesmo formato de datetime('now')).
            convite_token  TEXT UNIQUE,
            convite_expira TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE,
            UNIQUE(loja, login)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_loja ON usuarios(loja)")
    # Bancos criados antes do convite: acrescenta as colunas (o ADD COLUMN do
    # SQLite não aceita UNIQUE, daí o índice único à parte). Tornar senha_hash
    # nullable num banco antigo exige recriá-lo.
    colunas = {r["name"] for r in cur.execute("PRAGMA table_info(usuarios)")}
    if "convite_token" not in colunas:
        cur.execute("ALTER TABLE usuarios ADD COLUMN convite_token TEXT")
    if "convite_expira" not in colunas:
        cur.execute("ALTER TABLE usuarios ADD COLUMN convite_expira TEXT")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_convite ON usuarios(convite_token)")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nome       TEXT NOT NULL,
            telefone   TEXT,
            email      TEXT,
            loja       TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE SET NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_clientes_loja ON clientes(loja)")

    # O vendedor de um pedido é o usuário logado (tabela usuarios);
    # a antiga tabela vendedores foi aposentada.
    cur.execute("DROP TABLE IF EXISTS vendedores")

    # comissao_taxa/comissao_valor são congeladas no momento do pedido:
    # mudar a taxa do usuário depois não altera pedidos passados.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pedidos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id   INTEGER,
            usuario_id   INTEGER,
            loja         TEXT,
            data_iso     TEXT,
            tecido       TEXT,
            quantidade   REAL,
            preco_unitario REAL,
            total        REAL,
            desconto     REAL DEFAULT 0,
            comissao_taxa  REAL NOT NULL DEFAULT 0,
            comissao_valor REAL NOT NULL DEFAULT 0,
            -- pago=1: à vista (default). pago=0: fiado, entra no saldo devedor
            -- do cliente até um pagamento (tabela pagamentos) cobrir o total.
            pago         INTEGER NOT NULL DEFAULT 1,
            observacoes  TEXT,
            status       TEXT NOT NULL DEFAULT 'pedido' CHECK(status IN ('orcamento','pedido')),
            created_at   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE SET NULL,
            FOREIGN KEY (loja)       REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE SET NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_loja ON pedidos(loja)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS itens_pedido (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id   INTEGER NOT NULL,
            descricao   TEXT NOT NULL,
            cor         TEXT,
            peso_kg     REAL DEFAULT 0,
            qtd         INTEGER DEFAULT 1,
            preco_unit  REAL DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE
        )
    """)
    
    # --- NOVA ESTRUTURA DE ESTOQUE ---
    # 1. Remove a tabela de estoque antiga, se existir.
    cur.execute("DROP TABLE IF EXISTS estoque")
    
    # 2. Cria a tabela para os tipos de tecido.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS estoque_tecidos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_tecido   TEXT NOT NULL,
            loja          TEXT NOT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE,
            UNIQUE(nome_tecido, loja)
        )
    """)
    
    # 3. Cria a tabela para as cores e quantidades de cada tecido.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS estoque_cores (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tecido_id     INTEGER NOT NULL,
            nome_cor      TEXT NOT NULL,
            peso_kg       REAL NOT NULL DEFAULT 0,
            qtd_pecas     INTEGER NOT NULL DEFAULT 0,
            estoque_minimo REAL NOT NULL DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (tecido_id) REFERENCES estoque_tecidos(id) ON DELETE CASCADE,
            UNIQUE(tecido_id, nome_cor)
        )
    """)

    # Despesas da loja, importadas de planilha (categoria é texto livre).
    # data é "YYYY-MM-DD", comparável por string com o filtro de período.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS despesas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            loja        TEXT NOT NULL,
            data        TEXT NOT NULL,
            descricao   TEXT,
            categoria   TEXT,
            valor       REAL NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_despesas_loja_data ON despesas(loja, data)")

    # 4. Entrada de mercadoria (recebimento avulso): cabeçalho do lote.
    #    O efeito no estoque é somar o peso em estoque_cores.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entradas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja          TEXT NOT NULL,
            fornecedor    TEXT,
            data          TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entradas_loja ON entradas(loja)")

    # 5. Rolos: rastreabilidade opcional, só gravada quando a linha da entrada
    #    traz o id do rolo. O mesmo id não entra duas vezes na mesma loja
    #    (evita creditar em dobro ao reimportar a mesma planilha).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rolos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja          TEXT NOT NULL,
            entrada_id    INTEGER NOT NULL,
            tecido        TEXT NOT NULL,
            cor           TEXT NOT NULL,
            roll_ext_id   TEXT,
            peso          REAL NOT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE,
            FOREIGN KEY (entrada_id) REFERENCES entradas(id) ON DELETE CASCADE,
            UNIQUE(loja, roll_ext_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rolos_entrada ON rolos(entrada_id)")

    # 6. Encomenda: o que está PREVISTO chegar. Nunca mexe no estoque — só o
    #    recebimento (que credita como uma entrada) faz isso.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS encomendas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            loja          TEXT NOT NULL,
            fornecedor    TEXT,
            data_prevista TEXT,
            status        TEXT NOT NULL DEFAULT 'aberta',
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_encomendas_loja ON encomendas(loja)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS encomenda_itens (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            encomenda_id     INTEGER NOT NULL,
            tecido           TEXT NOT NULL,
            cor              TEXT NOT NULL,
            peso_previsto    REAL NOT NULL DEFAULT 0,
            rolos_previstos  INTEGER,
            FOREIGN KEY (encomenda_id) REFERENCES encomendas(id) ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_encomenda_itens_encomenda ON encomenda_itens(encomenda_id)")

    # Abatimentos do saldo devedor de um cliente (vendas fiado, pago=0).
    # O saldo em si não é armazenado: é sempre recalculado (SUM pedidos
    # fiado - SUM pagamentos) para nunca dessincronizar.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pagamentos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            loja        TEXT NOT NULL,
            cliente_id  INTEGER NOT NULL,
            valor       REAL NOT NULL,
            data        TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (loja) REFERENCES lojas(codigo) ON UPDATE CASCADE ON DELETE CASCADE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pagamentos_cliente ON pagamentos(loja, cliente_id)")

    # Colunas novas em tabelas que já existem: CREATE TABLE IF NOT EXISTS não
    # altera um banco antigo, então elas entram via ALTER quando faltarem.
    # Orçamento ('orcamento') não baixa estoque nem conta em relatório até
    # virar 'pedido'.
    _garantir_coluna(cur, "pedidos", "status",
                     "TEXT NOT NULL DEFAULT 'pedido' CHECK(status IN ('orcamento','pedido'))")
    # Alerta de estoque: 0 = sem mínimo definido (nunca alerta).
    _garantir_coluna(cur, "estoque_cores", "estoque_minimo", "REAL NOT NULL DEFAULT 0")

    conn.commit()
    conn.close()

def _garantir_coluna(cur: sqlite3.Cursor, tabela: str, coluna: str, ddl: str) -> None:
    colunas = [r[1] for r in cur.execute(f"PRAGMA table_info({tabela})").fetchall()]
    if coluna not in colunas:
        cur.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {ddl}")

# -------- Funções de Acesso a Dados (sem alterações, por enquanto) --------
def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Converte uma linha do banco de dados em um dicionário."""
    return dict(row) if row is not None else None

def get_loja_by_codigo(codigo: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, codigo, nome, endereco, pix_chave, senha_hash, status FROM lojas WHERE codigo=?", (codigo,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)

def get_usuario(loja: str, login: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, loja, nome, login, senha_hash, papel, taxa_comissao, ativo FROM usuarios WHERE loja=? AND login=?",
        (loja, login),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)

def get_usuario_by_id(usuario_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, loja, nome, login, papel, taxa_comissao, ativo FROM usuarios WHERE id=?",
        (usuario_id,),
    )
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)

def salvar_pix_by_codigo(codigo: str, pix_chave: Optional[str]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE lojas SET pix_chave = ? WHERE codigo = ?", (pix_chave, codigo))
    conn.commit()
    conn.close()

def get_pedido(pedido_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            p.id, p.data_iso, p.tecido, p.quantidade, p.preco_unitario, p.total, p.desconto,
            p.loja AS loja_codigo, p.cliente_id, p.usuario_id,
            p.comissao_taxa, p.comissao_valor, p.status,
            COALESCE(c.nome, '') AS cliente_nome,
            COALESCE(c.telefone, '') AS cliente_telefone,
            COALESCE(u.nome, '') AS vendedor_nome,
            COALESCE(l.nome, p.loja) AS loja_nome,
            COALESCE(l.pix_chave, '') AS loja_pix_chave
        FROM pedidos p
        LEFT JOIN clientes c ON c.id = p.cliente_id
        LEFT JOIN usuarios u ON u.id = p.usuario_id
        LEFT JOIN lojas l ON l.codigo = p.loja
        WHERE p.id=?
    """, (pedido_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)

def get_itens_pedido(pedido_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT descricao, cor, peso_kg FROM itens_pedido WHERE pedido_id = ? ORDER BY id ASC", (pedido_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

if __name__ == '__main__':
    print("Inicializando banco de dados...")
    init_db()
    print("Banco de dados inicializado em:", DB_PATH)

