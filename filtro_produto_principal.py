"""
filtro_produto_principal.py
===========================
Algoritmo de filtragem para distinguir produto principal de acessórios/relacionados.

Integração com TechOfertas:
    - Substitui (ou complementa) nome_compativel_com_busca() em app.py
    - Chamada DEPOIS do filtro de tokens existente (nome_compativel_com_busca filtra
      produtos sem relação alguma; is_produto_principal filtra acessórios relacionados)

Uso:
    from filtro_produto_principal import is_produto_principal
    if not is_produto_principal(nome, produto):
        continue

Filosofia de design:
    - Em caso de dúvida → MANTER (falso negativo é preferível ao falso positivo)
    - Sem bibliotecas externas (só re, unicodedata da stdlib)
    - Defensivo: retorna True se não consegue classificar com confiança
"""

import re
import unicodedata


# ---------------------------------------------------------------------------
# Constantes de manutenção
# ---------------------------------------------------------------------------

# Palavras que, quando presentes no TÍTULO, sinalizam que é um acessório.
# Manutenção: adicione aqui quando surgirem falsos negativos recorrentes.
ACESSORIOS_PALAVRAS = {
    # Capas, proteção
    "capa", "case", "capinha", "capas", "estoijo", "estojo",
    "bolsa", "mochila", "pasta", "sleeve",
    # Peliculas, vidros
    "pelicula", "peliculas", "vidro", "mica", "protecao", "protetor", "protetora",
    "temperado", "temperada",
    # Carregadores e cabos (quando não são o foco da query)
    "carregador", "cabo", "adaptador", "hub", "dock",
    "fonte", "tomada", "bivolt",
    "usbc", "usb c",
    # Suportes / apoios
    "suporte", "stand", "base", "apoio", "fixacao", "fixador",
    "wall mount", "bracket",
    # Canetas / stylus
    "caneta", "pencil", "stylus", "lapis",
    # Teclados e mouses — quando NÃO são o produto buscado
    "teclado", "mouse", "trackpad", "mousepad",
    # Fones e áudio — quando NÃO são o produto buscado
    "fone", "fones", "earphone", "earbud", "headset", "headphone",
    "microfone", "microfones",
    # Armazenamento externo / adaptadores — quando NÃO são o produto buscado
    "enclosure", "gaveta", "case m2", "case nvme", "case sata",
    # Periféricos de tela — quando NÃO é o produto buscado
    "cabo hdmi", "cabo displayport", "cabo dp", "cabo vga",
    "conversor", "splitter",
    # Controladores / controles — quando NÃO são o produto buscado
    "controle", "joystick", "gamepad", "volante",
    # Jogos (físicos/digitais) — quando não se busca jogo
    "jogo", "game", "jogos",
    # Baterias / power banks
    "bateria", "powerbank", "power bank", "banco de energia",
    # Outros
    "skin", "adesivo", "sticker", "grip", "anel", "pop socket",
    "webcam",  # periférico
    "impressora", "scanner",  # periféricos
    "cooler", "ventoinha", "cooler para",
    "pasta termica", "pasta térmica",
    "thermal paste", "thermal pad",
    "kit limpeza", "spray", "flanela",
}

# Palavras que, quando presentes na QUERY, indicam que o próprio acessório
# é o produto buscado → NÃO filtrar.
# Manutenção: espelhe ACESSORIOS_PALAVRAS conforme necessário.
QUERY_ACESSORIO_INTENCIONAL = {
    "capa", "case", "capinha", "pelicula", "peliculas", "protetor", "protetora",
    "carregador", "cabo", "adaptador", "hub", "dock", "fonte",
    "suporte", "stand", "base",
    "caneta", "pencil", "stylus",
    "teclado", "mouse", "mousepad",
    "fone", "fones", "headset", "headphone", "earphone", "earbud", "microfone",
    "enclosure", "gaveta",
    "controle", "joystick", "gamepad",
    "jogo", "game", "jogos",
    "bateria", "powerbank", "power bank",
    "cooler", "ventoinha", "pasta",   # "pasta" cobre "pasta termica"
    "webcam", "impressora",
    "kit", "acessorio", "acessórios",
    "skin", "adesivo",
}

# Prefixos de título que quase sempre indicam acessório.
# Manutenção: adicione padrões observados nos scrapers.
PREFIXOS_ACESSORIO = [
    r"^capa\b",
    r"^capinha\b",
    r"^case\b",
    r"^pelicula\b",
    r"^kit\s+\d",          # "Kit 2 películas", "Kit 3 capas"
    r"^suporte\b",
    r"^stand\b",
    r"^cabo\b",
    r"^carregador\b",
    r"^fonte\b",
    r"^adaptador\b",
    r"^bateria\b",
    r"^controle\b",
    r"^jogo\b",
    r"^game\b",
    r"^fone\b",
    r"^headset\b",
    r"^headphone\b",
    r"^mouse\b",
    r"^teclado\b",
    r"^webcam\b",
    r"^caneta\b",
    r"^cooler\b",
    r"^pasta\s+termica",
]

# Palavras-chave que, se presentes na query, indicam que é um produto principal
# com especificidade suficiente (console, tablet, smartphone...).
# Usadas para REFORÇAR o filtro de acessórios quando a query é clara.
PRODUTO_PRINCIPAL_INDICADORES = {
    # Consoles
    "console", "playstation", "xbox", "nintendo", "switch", "ps5", "ps4",
    # Tablets
    "ipad", "tablet",
    # Smartphones
    "iphone", "smartphone", "celular",
    # Computadores
    "notebook", "laptop", "desktop", "pc", "computador",
    # Componentes PC (quando a query é específica)
    "rtx", "gtx", "rx", "placa de video", "placa de vídeo", "gpu",
    "processador", "cpu", "ryzen", "core i",
    "ssd", "hd", "memoria ram", "memória ram",
    "fonte atx",
    "monitor",
    # Áudio (quando é o produto principal)
    "caixa de som", "soundbar",
    # Eletrodomésticos
    "geladeira", "tv", "televisao", "televisão",
}


# ---------------------------------------------------------------------------
# Utilitários de normalização (compatíveis com padronizar_texto do app.py)
# ---------------------------------------------------------------------------

def _normalizar(texto: str) -> str:
    """Converte para ASCII lower, remove acentos e pontuação."""
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    texto = re.sub(r"(\d+)\s?(gb|tb|mb|hz|w\b)", r"\1\2", texto)  # "16 GB" → "16gb"
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _tokens(texto: str) -> list[str]:
    return _normalizar(texto).split()


def _construir_patterns(palavras: set) -> list[tuple[str, re.Pattern]]:
    """Pré-compila padrões regex para um set de palavras."""
    resultado = []
    for p in sorted(palavras, key=len, reverse=True):  # mais longas primeiro
        p_norm = _normalizar(p)
        pat = re.compile(r"\b" + re.escape(p_norm) + r"\b")
        resultado.append((p, pat))
    return resultado


def _contem_algum(texto_normalizado: str, palavras_ou_patterns) -> str | None:
    """
    Retorna a primeira palavra do conjunto encontrada no texto normalizado.
    Aceita set de strings (normaliza on-demand) ou lista de (str, Pattern)
    pré-compilada (mais rápido para sets estáticos).
    """
    if isinstance(palavras_ou_patterns, list):
        for p, pat in palavras_ou_patterns:
            if pat.search(texto_normalizado):
                return p
        return None
    # Fallback para sets dinâmicos (qualificadores funcionais inline)
    for p in palavras_ou_patterns:
        p_norm = _normalizar(p)
        if re.search(r"\b" + re.escape(p_norm) + r"\b", texto_normalizado):
            return p
    return None


# Pré-compila os patterns das constantes estáticas usadas na hot-path
_PATTERNS_ACESSORIOS = _construir_patterns(ACESSORIOS_PALAVRAS)
_PATTERNS_QUERY_ACESSORIO = _construir_patterns(QUERY_ACESSORIO_INTENCIONAL)
_PATTERNS_PRODUTO_PRINCIPAL = _construir_patterns(PRODUTO_PRINCIPAL_INDICADORES)


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def is_produto_principal(titulo: str, query: str) -> bool:
    """
    Retorna True se `titulo` parece ser o produto principal da busca `query`.
    Retorna False se parece ser acessório, variante irrelevante ou item relacionado.

    Em caso de dúvida → retorna True (defensivo: melhor mostrar um acessório
    a mais do que esconder um produto válido).

    Parâmetros:
        titulo: título do produto retornado pela loja (string)
        query:  o que o usuário digitou na busca (string)

    Retorna:
        bool

    Fluxo de decisão:
        PASSO 1 — Query pede acessório intencional?
            1a. Query só tem a palavra de acessório → manter tudo
            1b. Query tem produto-pai (capa iPad, controle PS5) → manter tudo
            1c. Query tem qualificador que NÃO é produto-pai nem especificação
                do próprio acessório ("teclado mecânico", "headset gamer"):
                → é periférico-produto: manter só se título começa com a
                  palavra-produto, descartar caso contrário
            1d. Query tem especificação do próprio acessório ("cabo USB-C",
                "carregador 20W") → manter tudo (o acessório É o produto)

        PASSO 2 — Título começa com prefixo de acessório?
            (regex no início do título normalizado) → descartar

        PASSO 3 — Título tem palavra de acessório nos primeiros 1/3 tokens?
            → descartar

        PASSO 4 — Query indica produto principal claro (iPad, PS5, RTX...)?
            + Título tem palavra de acessório na parte principal?
            → descartar

        Padrão → manter
    """
    # Guarda de segurança
    if not titulo or not query:
        return True

    titulo_norm = _normalizar(titulo)
    query_norm = _normalizar(query)
    tokens_query = _tokens(query_norm)

    # ------------------------------------------------------------------
    # PASSO 1: A query pede um acessório intencionalmente?
    #
    # Casos:
    #   (a) "capa", "controle", "headset" → query só tem palavra de acessório
    #   (b) "capa iPad", "controle PS5", "jogo Switch" → acessório + produto pai
    #   (c) "cabo USB-C", "carregador 20W" → acessório + especificação técnica
    #       do próprio acessório → manter tudo
    #   (d) "teclado mecânico", "headset gamer" → acessório + qualificador
    #       funcional → periférico-produto: filtrar acessórios do periférico
    # ------------------------------------------------------------------
    query_pede_acessorio = _contem_algum(query_norm, _PATTERNS_QUERY_ACESSORIO)

    if query_pede_acessorio:
        palavra_acess_norm = _normalizar(query_pede_acessorio)
        tokens_qualificadores = [
            t for t in tokens_query
            if t not in palavra_acess_norm.split()
            and len(t) >= 2
        ]

        # Caso (a): query só tem a palavra de acessório
        if not tokens_qualificadores:
            return True

        qualificadores_str = " ".join(tokens_qualificadores)

        # Caso (b): qualificador é um produto pai
        if _contem_algum(qualificadores_str, _PATTERNS_PRODUTO_PRINCIPAL):
            return True

        # Distingue (c) de (d):
        # Qualificadores FUNCIONAIS → periférico-produto (d)
        # Qualificadores de ESPECIFICAÇÃO → acessório puro (c)
        QUALIFICADORES_FUNCIONAIS = {
            "mecanico", "mecanica", "gamer", "gaming", "wireless",
            "bluetooth", "rgb", "optico", "otico", "membrana", "hibrido",
            "ativo", "ativa", "passivo", "passiva",
            "over", "ear", "true", "tws",
            "tkl", "compact", "fullsize", "ergonomico", "ergonomica",
        }
        qualificadores_funcionais_norm = {_normalizar(q) for q in QUALIFICADORES_FUNCIONAIS}
        e_periferico_produto = any(
            t in qualificadores_funcionais_norm for t in tokens_qualificadores
        )

        # Caso (c): especificação do próprio acessório → manter
        if not e_periferico_produto:
            return True

        # Caso (d): periférico-produto qualificado ("teclado mecânico", "headset gamer")
        # O produto principal DEVE começar com a palavra-produto.
        # Acessórios do periférico (keycaps, suporte, cabo coiled) não começam.
        titulo_tokens = _tokens(titulo_norm)
        palavra_produto_tokens = palavra_acess_norm.split()
        titulo_comeca_com_produto = (
            titulo_tokens[:len(palavra_produto_tokens)] == palavra_produto_tokens
        )
        return titulo_comeca_com_produto  # True = produto, False = acessório do produto

    # ------------------------------------------------------------------
    # PASSO 2: Título começa com prefixo claro de acessório?
    # Ex.: "Capa para iPad...", "Suporte Mesa...", "Carregador USB-C..."
    # ------------------------------------------------------------------
    for padrao in PREFIXOS_ACESSORIO:
        if re.search(padrao, titulo_norm):
            return False

    # ------------------------------------------------------------------
    # PASSO 3: Título tem palavra de acessório nos primeiros 1/3 tokens?
    # Cobre: "Película de Vidro iPad Air 5" (prefixo não captura exato)
    # ------------------------------------------------------------------
    tokens_titulo = _tokens(titulo_norm)
    n_checar = max(3, len(tokens_titulo) // 3)
    prefixo_titulo = " ".join(tokens_titulo[:n_checar])
    if _contem_algum(prefixo_titulo, _PATTERNS_ACESSORIOS):
        return False

    # ------------------------------------------------------------------
    # PASSO 3b: Console como sufixo/plataforma — padrão de jogo ou acessório
    # "Resident Evil 4 - PS5", "Spider-Man PS5 Versão Europeia", "God of War Xbox"
    # O identificador do console aparece depois de " - " no título ORIGINAL,
    # indicando que é produto PARA a plataforma, não a plataforma em si.
    # Nota: _normalizar remove traços; por isso usamos `titulo` (original) aqui.
    # Exceção: título também contém palavras de hardware (console, slim, tb...)
    # ------------------------------------------------------------------
    # Padrão "para console [query]" / "para o console [query]" → acessório
    for qt in tokens_query:
        if re.search(r'\bpara\s+(?:o\s+)?console\s+' + re.escape(qt), titulo_norm):
            return False

    _CONSOLES_RAW = [
        (r'\bPS5\b', 'ps5'), (r'\bPS4\b', 'ps4'), (r'\bPS3\b', 'ps3'),
        (r'\bXbox\b', 'xbox'), (r'\bSwitch\b', 'switch'),
    ]
    _HW_WORDS = r'\b(console|slim|digital|825gb|1tb|2tb|cfr|cfi|heg|hdh)\b'
    for console_pat, console_key in _CONSOLES_RAW:
        if console_key in query_norm:
            # Padrão: "Algo - PS5" ou "Algo - PS5 Versão Europeia" no título original
            if re.search(r'\s-\s+' + console_pat, titulo, re.IGNORECASE):
                if not re.search(_HW_WORDS, titulo_norm):
                    return False

    # ------------------------------------------------------------------
    # PASSO 4: Query indica produto principal claro E título tem acessório?
    # Cobre keyword stuffing da Shopee e títulos mistos.
    # Só aplica quando a query sinaliza claramente o produto principal
    # (iPad, PS5, RTX 4070...) para não filtrar demais em queries genéricas.
    # ------------------------------------------------------------------
    if _contem_algum(query_norm, _PATTERNS_PRODUTO_PRINCIPAL):
        partes = re.split(r"[,()\[\]]", titulo_norm)
        parte_principal = partes[0].strip()
        palavra_acc = _contem_algum(parte_principal, _PATTERNS_ACESSORIOS)
        if palavra_acc:
            palavra_acc_norm = _normalizar(palavra_acc)
            # Excluir: "para [acessório]" indica descrição do produto, não tipo de acessório
            # Ex.: "Console para Jogos 4K Sony" — "para jogos" descreve o console
            if re.search(r"\bpara\s+" + re.escape(palavra_acc_norm), parte_principal):
                pass  # não filtrar — é descrição, não acessório
            # Excluir: "capa inclusa/incluída" e "com capa" indicam bundle, não capa como produto
            elif palavra_acc_norm == "capa" and re.search(
                r"\bcapa\s+inclu|\bcom\s+capa\b|\bacompanha\s+capa\b", parte_principal
            ):
                pass  # não filtrar — é nota de bundle
            else:
                return False

    # ------------------------------------------------------------------
    # Padrão: produto parece legítimo → manter
    # ------------------------------------------------------------------
    return True


# ---------------------------------------------------------------------------
# Suite de testes
# ---------------------------------------------------------------------------

SUITE = [
    # ===================================================================
    # GRUPO 1: "iPad" → deve manter tablets, remover acessórios
    # ===================================================================
    ("iPad", "Apple iPad 10ª Geração 64GB WiFi Cinza Espacial", True),
    ("iPad", "Apple iPad Pro 12.9 M2 256GB WiFi Space Gray", True),
    ("iPad", "iPad Air 5ª Geração 64GB WiFi Starlight", True),
    ("iPad", "Apple iPad Mini 6 256GB WiFi Pink", True),
    ("iPad", "Capa para iPad 10ª Geração Silicone Azul Marinho", False),
    ("iPad", "Capa Magnética iPad Pro 12.9 Smart Folio Apple", False),
    ("iPad", "Película de Vidro Temperado iPad Air 5 10.9", False),
    ("iPad", "Caneta Stylus iPad Pro Pencil 2ª Geração Apple", False),
    ("iPad", "Suporte Mesa iPad Pro Ajustável Alumínio Graystone", False),
    ("iPad", "Teclado Magic Keyboard iPad Pro 12.9 MJQK3BZ/A", False),
    ("iPad", "Carregador USB-C 20W Apple iPad Air iPhone", False),
    ("iPad", "Cabo USB-C para Lightning iPad 1m Apple", False),

    # ===================================================================
    # GRUPO 2: "PS5" → deve manter console, remover jogos/controles/headsets
    # ===================================================================
    ("PS5", "Console PlayStation 5 825GB Sony CFI-1215A", True),
    ("PS5", "Console PlayStation 5 Digital Edition 825GB", True),
    ("PS5", "Sony PlayStation 5 Slim 1TB CFI-2015A Branco", True),
    ("PS5", "Controle DualSense PS5 Branco CFI-ZCT1W", False),
    ("PS5", "Controle DualSense Edge PS5 CFIZCT1W Preto", False),
    ("PS5", "Jogo Marvel's Spider-Man 2 PS5 Mídia Física", False),
    ("PS5", "Jogo God of War Ragnarök PS5 Sony", False),
    ("PS5", "Headset Pulse 3D PS5 Sony CFI-ZWH1 Branco", False),
    ("PS5", "Base de Carregamento DualSense PS5 Sony", False),
    ("PS5", "Case PS5 Capa Protetora Faceplate Disco Preta", False),
    ("PS5", "SSD WD Black SN850X 1TB PS5 Compatible NVMe", True),   # SSD para PS5 é PRODUTO (componente)

    # ===================================================================
    # GRUPO 3: "iPhone 15" → celular principal, não acessórios
    # ===================================================================
    ("iPhone 15", "Apple iPhone 15 128GB Preto A3092 Lacrado NF", True),
    ("iPhone 15", "Apple iPhone 15 Pro 256GB Titanio Natural", True),
    ("iPhone 15", "Apple iPhone 15 Pro Max 512GB Titanio Preto", True),
    ("iPhone 15", "Capa iPhone 15 Pro Transparente Silicone Apple", False),
    ("iPhone 15", "Capinha iPhone 15 Anti-Impacto Kevlar Preta", False),
    ("iPhone 15", "Película iPhone 15 Pro Max Vidro Temperado 9H", False),
    ("iPhone 15", "Carregador iPhone 15 USB-C 20W Original Apple", False),
    ("iPhone 15", "Cabo USB-C iPhone 15 2m Trança MagSafe", False),
    ("iPhone 15", "Suporte Carro iPhone 15 MagSafe Magnético", False),

    # ===================================================================
    # GRUPO 4: "RTX 4070" → placa de vídeo, não suporte/pasta/cabos
    # ===================================================================
    ("RTX 4070", "ASUS Dual GeForce RTX 4070 12GB GDDR6X OC", True),
    ("RTX 4070", "Gigabyte Gaming OC RTX 4070 12GB GDDR6X 192bit", True),
    ("RTX 4070", "MSI Ventus 3X RTX 4070 Ti Super 16GB OC", True),
    ("RTX 4070", "Suporte GPU RTX 4070 Anti-Sagging Bracket Preto", False),
    ("RTX 4070", "Pasta Térmica RTX 4070 Thermal Grizzly Kryonaut 1g", False),
    ("RTX 4070", "Cabo PCIe 16 Pinos RTX 4070 Adaptador 2x 8 Pinos", False),

    # ===================================================================
    # GRUPO 5: "Nintendo Switch" → console, não jogos/cases/carregadores
    # ===================================================================
    ("Nintendo Switch", "Nintendo Switch OLED 64GB Branco HEG-001", True),
    ("Nintendo Switch", "Nintendo Switch Lite 32GB Amarelo HDH-001", True),
    ("Nintendo Switch", "Case Nintendo Switch OLED Bolsa Viagem Preta", False),
    ("Nintendo Switch", "Carregador Nintendo Switch USB-C 45W Fast", False),
    ("Nintendo Switch", "Jogo Mario Kart 8 Deluxe Nintendo Switch", False),
    ("Nintendo Switch", "Jogo The Legend of Zelda Tears Kingdom Switch", False),
    ("Nintendo Switch", "Protetor de Tela Nintendo Switch OLED Vidro", False),

    # ===================================================================
    # GRUPO 6: Query com acessório intencional → TUDO deve passar
    # ===================================================================
    ("controle PS5", "Controle DualSense PS5 Branco Sony CFI-ZCT1W", True),
    ("controle PS5", "Controle DualSense Edge PS5 Preto CFIZCT1W", True),
    ("capa iPad", "Capa iPad 10ª Geração Silicone Azul Royal", True),
    ("capa iPad", "Capa Smart Folio iPad Pro 12.9 M2 Preto Apple", True),
    ("carregador iPhone", "Carregador iPhone USB-C 20W Original Apple", True),
    ("carregador iPhone", "Carregador iPhone 15 Pro USB-C 30W Anker", True),
    ("jogo PS5", "Jogo Spider-Man 2 PS5 Mídia Física Sony", True),
    ("jogo PS5", "Jogo Hogwarts Legacy PS5 BR Dublado WB Games", True),
    ("cabo USB-C", "Cabo USB-C 2m 100W Carga Rápida PD Baseus", True),
    ("película iPhone", "Película iPhone 15 Pro Max Vidro Temperado 9H", True),

    # ===================================================================
    # GRUPO 7: Queries genéricas / componentes
    # ===================================================================
    ("SSD 1TB", "Samsung 870 EVO SSD 1TB SATA III 2.5 MZ-77E1T0B", True),
    ("SSD 1TB", "WD Blue SN580 SSD NVMe M.2 1TB PCIe Gen4", True),
    ("SSD 1TB", "Enclosure SSD NVMe USB-C 1TB Externo Orico", False),
    ("SSD 1TB", "Gaveta SSD 2.5 SATA USB 3.0 Case Externo", False),
    ("monitor 27", "Monitor LG 27GP850-B 27 IPS 165Hz QHD G-Sync", True),
    ("monitor 27", "Monitor Samsung Odyssey G5 27 165Hz VA Curvo", True),
    ("monitor 27", "Suporte Monitor 27 Articulado Parede VESA", False),
    ("monitor 27", "Cabo HDMI 2.1 8K 2m Para Monitor 27", False),
    ("teclado mecânico", "Teclado Mecânico Redragon Kumara K552 Red", True),
    ("teclado mecânico", "Teclado Mecânico HyperX Alloy Origins 60 Red", True),
    ("teclado mecânico", "Keycaps PBT Double Shot Teclado Mecânico ISO BR", False),
    ("teclado mecânico", "Cabo USB Coiled Aviator Teclado Mecânico 1.5m", False),
    ("headset gamer", "Headset Gamer HyperX Cloud II 7.1 KHX-HSCP-RD", True),
    ("headset gamer", "Headset Gamer Logitech G733 Lightspeed Sem Fio", True),
    ("headset gamer", "Suporte Headset Gamer Base Mesa RGB Alumínio", False),
    ("headset gamer", "Cabo USB Headset Gamer Reposição 2m Trançado", False),
]


def _executar_suite() -> dict:
    """Executa a suite e retorna métricas."""
    total = len(SUITE)
    acertos = 0
    falsos_positivos = []  # Previu False mas esperado True (produto válido filtrado)
    falsos_negativos = []  # Previu True mas esperado False (acessório não filtrado)

    for query, titulo, esperado in SUITE:
        resultado = is_produto_principal(titulo, query)
        if resultado == esperado:
            acertos += 1
        elif resultado is True and esperado is False:
            falsos_negativos.append((query, titulo))
        else:
            falsos_positivos.append((query, titulo))

    acuracia = acertos / total * 100

    # Métricas de precisão e recall para a classe "acessório" (False)
    acessorios_esperados = [(q, t) for q, t, e in SUITE if not e]
    acessorios_detectados = [(q, t) for q, t, e in SUITE if not e and not is_produto_principal(t, q)]
    principais_esperados = [(q, t) for q, t, e in SUITE if e]
    principais_mantidos = [(q, t) for q, t, e in SUITE if e and is_produto_principal(t, q)]

    precisao = len(acessorios_detectados) / (len(acessorios_detectados) + len(falsos_positivos)) * 100 if acessorios_detectados else 0
    recall = len(principais_mantidos) / len(principais_esperados) * 100 if principais_esperados else 0

    return {
        "total": total,
        "acertos": acertos,
        "acuracia": acuracia,
        "precisao": precisao,
        "recall": recall,
        "falsos_positivos": falsos_positivos,
        "falsos_negativos": falsos_negativos,
    }


def _imprimir_relatorio(r: dict):
    sep = "=" * 65
    print(sep)
    print("  RELATÓRIO DE TESTES — is_produto_principal")
    print(sep)
    print(f"  Total de casos:       {r['total']}")
    print(f"  Acertos:              {r['acertos']}")
    print(f"  Acurácia:             {r['acuracia']:.1f}%")
    print(f"  Precisão (filtro):    {r['precisao']:.1f}%  (meta ≥ 90%)")
    print(f"  Recall (manter):      {r['recall']:.1f}%  (meta ≥ 95%)")
    print(sep)

    if r["falsos_positivos"]:
        print(f"\n  FALSOS POSITIVOS ({len(r['falsos_positivos'])}) — produto válido REMOVIDO indevidamente:")
        for q, t in r["falsos_positivos"]:
            print(f"    query='{q}'")
            print(f"    titulo='{t}'")
            print()
    else:
        print("\n  Nenhum falso positivo.")

    if r["falsos_negativos"]:
        print(f"\n  FALSOS NEGATIVOS ({len(r['falsos_negativos'])}) — acessório NÃO filtrado:")
        for q, t in r["falsos_negativos"]:
            print(f"    query='{q}'")
            print(f"    titulo='{t}'")
            print()
    else:
        print("\n  Nenhum falso negativo.")

    print(sep)


if __name__ == "__main__":
    r = _executar_suite()
    _imprimir_relatorio(r)
