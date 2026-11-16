-- Tabela de destino do exemplo (nomes genéricos).
-- A ordem das colunas segue o INSERT ... SELECT de carga_idempotente.py:
-- intervalo, hora, <demais colunas do CSV>, dt_referencia, uf.
-- As métricas ficam como texto na carga; a tipagem forte pode viver numa view
-- de consumo, o que mantém a carga tolerante a variações do arquivo de origem.

CREATE TABLE dw.dbo.historico_skill (
    intervalo            DATETIME     NOT NULL,
    hora                 TIME(0)      NOT NULL,
    chamadas_ofertadas   VARCHAR(50)  NULL,
    chamadas_atendidas   VARCHAR(50)  NULL,
    chamadas_abandonadas VARCHAR(50)  NULL,
    nivel_servico        VARCHAR(50)  NULL,
    tma_segundos         VARCHAR(50)  NULL,
    agentes_logados      VARCHAR(50)  NULL,
    skill                VARCHAR(50)  NULL,
    operacao             VARCHAR(50)  NULL,
    dt_referencia        DATE         NOT NULL,
    uf                   CHAR(2)      NOT NULL
);

-- A partição lógica da carga é (uf, dt_referencia): o DELETE usa exatamente isso.
CREATE INDEX ix_historico_skill_particao
    ON dw.dbo.historico_skill (uf, dt_referencia);
