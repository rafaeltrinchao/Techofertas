import json
import os
import re
import time
import unicodedata
import uuid
import urllib.parse
import cloudscraper as _cloudscraper
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from flask import Flask, render_template_string, jsonify, request, Response, stream_with_context, send_file
from concurrent.futures import ThreadPoolExecutor

try:
    from curl_cffi import requests as _cffi_requests
    _CB_SESSION = _cffi_requests.Session(impersonate='chrome124')
except ImportError:
    _cffi_requests = None
    _CB_SESSION = None

# --- filtro_produto_principal (inlined) ---
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
    # Identificação explícita de acessório no título
    "acessorio", "acessorios",
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
    r"^cover\b",
    r"^pelicula\b",
    r"^vidro\s+temperado",  # "Vidro Temperado iPhone..."
    r"^mica\b",
    r"^protetor\b",
    r"^protetora\b",
    r"^kit\b",              # "Kit Viagem", "Kit 2 películas", "Kit Limpeza"
    r"^suporte\b",
    r"^stand\b",
    r"^cabo\b",
    r"^carregador\b",
    r"^fonte\b",
    r"^adaptador\b",
    r"^hub\b",
    r"^dock\b",
    r"^enclosure\b",
    r"^gaveta\b",
    r"^bateria\b",
    r"^powerbank\b",
    r"^power\s+bank\b",
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
    r"^skin\b",
    r"^adesivo\b",
    r"^manga\b",            # "Manga para notebook" (sleeve)
    r"^sleeve\b",
]

# Palavras-chave que, se presentes na query, indicam que é um produto principal
# com especificidade suficiente (console, tablet, smartphone...).
# Usadas para REFORÇAR o filtro de acessórios quando a query é clara.
PRODUTO_PRINCIPAL_INDICADORES = {
    # Consoles
    "console", "playstation", "xbox", "nintendo", "switch", "ps5", "ps4",
    # Tablets
    "ipad", "tablet",
    # Smartphones / marcas específicas
    "iphone", "smartphone", "celular",
    "galaxy", "pixel", "oneplus", "xiaomi", "redmi",
    # Computadores e marcas específicas
    "notebook", "laptop", "desktop", "pc", "computador",
    "macbook", "surface",
    # Componentes PC (quando a query é específica)
    "rtx", "gtx", "rx", "placa de video", "placa de vídeo", "gpu",
    "processador", "cpu", "ryzen", "core i",
    "ssd", "hd", "memoria ram", "memória ram",
    "fonte atx",
    "monitor",
    # Áudio (quando é o produto principal)
    "caixa de som", "soundbar", "airpods",
    # Eletrodomésticos / smart home
    "geladeira", "tv", "televisao", "televisão",
    "roteador", "router",
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

        PASSO 1.5 — Título contém "para [token_query]" / "compatível com [token]"?
            → sinal universal de acessório em e-commerce BR; descartar
            Cobre MacBook, Galaxy, Surface e qualquer produto futuro sem
            necessidade de manutenção de listas.

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
    # PASSO 1.5: "Para [...] [token_query]" / "compatível com [token]" / "p/ [token]"
    # Sinal universal de acessório em e-commerce brasileiro.
    # Fonte: ML treina vendedores a usar "para X" / "compatível com X" para acessórios.
    # Funciona para qualquer produto sem manutenção de listas:
    #   "Sleeve para MacBook Air", "Hub para MacBook Pro",
    #   "Para Laptop PC MacBook PS4", "para iPhone 16, iPad e MacBook Air",
    #   "Capa para Galaxy S24", "compatível com MacBooks"
    # Nota: verifica token e plural simples (macbook → macbooks)
    # ------------------------------------------------------------------
    _m_para = re.search(r'\bpara\b', titulo_norm)
    for qt in tokens_query:
        if len(qt) < 3:
            continue  # ignora artigos, prep., números curtos ("de", "15", "m2")
        # Padrão de match: token ou plural (macbook → macbooks)
        _qt_pat = r'\b(?:' + re.escape(qt) + r'|' + re.escape(qt) + r's)\b'
        # "para [qualquer coisa] macbook" — token da query após qualquer "para"
        if _m_para and re.search(_qt_pat, titulo_norm[_m_para.start():]):
            return False
        # "p/ macbook", "p/iphone"
        if re.search(r'\bp\s*/\s*' + re.escape(qt) + r'\b', titulo_norm):
            return False
        # "compatível com macbook", "compatible macbook", "compatíveis macbooks"
        if re.search(r'\bcompat[a-z]*\b(?:\s+com)?\s+' + _qt_pat, titulo_norm):
            return False

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
            # Excluir: "para [atividade]" descreve o PROPÓSITO do produto principal,
            # não indica que é um acessório físico.
            # ✓ "Console para Jogos 4K Sony"  — "para jogos" = propósito do console
            # ✗ "Pano de Limpeza Capa Para Teclado" — "para teclado" = acessório do teclado
            _PALAVRAS_ATIVIDADE = {
                "jogo", "jogos", "game", "games", "uso", "trabalho",
                "escritorio", "estudio", "edicao", "gamers", "criadores",
                "streaming", "multimidia",
            }
            if (re.search(r"\bpara\s+" + re.escape(palavra_acc_norm), parte_principal)
                    and palavra_acc_norm in _PALAVRAS_ATIVIDADE):
                pass  # não filtrar — é propósito do produto
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
# Constante e função para filtro de produtos NOVOS vs. USADOS
# ---------------------------------------------------------------------------

# Palavras que, na QUERY, indicam que o usuário QUER produtos usados/recondicionados.
# Quando presentes, is_produto_novo() retorna True (não filtra nada).
QUERY_QUER_USADO = {
    "usado", "usada", "usados",
    "recondicionado", "recondicionada",
    "seminovo", "semi-novo", "remanufaturado",
    "vitrine", "refurbished",
}

_QUERY_QUER_USADO_NORM = {_normalizar(p) for p in QUERY_QUER_USADO}


def is_produto_novo(titulo: str, query: str) -> bool:
    """
    Retorna True se o produto parece ser NOVO (não usado/recondicionado).
    Retorna False se o título indica produto usado, recondicionado ou seminovo.

    Exceção: se a query explicitamente pede produto usado/recondicionado,
    retorna True (não filtra — usuário sabe o que quer).

    Sinais de produto USADO/RECONDICIONADO detectados:
        1. Título começa com "Usado:" (padrão Mercado Livre)
        2. Palavras explícitas: recondicionado, seminovo, vitrine, etc.
        3. "Muito Bom" como frase (condição ML para item usado)
        4. " - Bom" / " - Excelente" com traço no título original
           (padrão ML: "iPhone 14 128GB - Excelente", "iPhone 14 - Bom (Recondicionado)")
        5. "Bom" ou "Excelente" como último token (sem traço)
           (padrão ML alternativo: "Apple iPhone 14 128GB Excelente")
    """
    if not titulo or not query:
        return True

    titulo_norm = _normalizar(titulo)
    query_norm = _normalizar(query)

    # Usuário quer produto usado/recondicionado → não filtrar
    if any(t in _QUERY_QUER_USADO_NORM for t in query_norm.split()):
        return True

    # 1. "Usado" em qualquer posição do título (normalizado ou em parênteses no original)
    # Captura: "Usado: Apple iPhone...", "Apple iPhone 15 (Usado)", "Apple iPhone - Usado"
    if re.search(r'\busad[ao]s?\b', titulo_norm):
        return False

    # 2. Palavras explícitas de condição usada/recondicionada
    # Inclui "condicionado" (sellers às vezes omitem o "re-") e "(Recondicionado)" com parênteses.
    # Nota: "ar condicionado" é produto legítimo — detectado via lookbehind no título original.
    if re.search(
        r'\b(?:recondicionad[ao]s?|condicionad[ao]s?|seminov[ao]s?|semi[\s-]nov[ao]s?'
        r'|vitrine|remanufaturad[ao]s?|refurbished)\b',
        titulo_norm,
    ):
        # Falso positivo: "Ar Condicionado" é produto legítimo — não filtrar
        if re.search(r'\bar\s+condicionad', titulo_norm):
            pass  # manter — é aparelho de ar condicionado
        else:
            return False

    # 2b. Padrão explícito com parênteses no título ORIGINAL (antes da normalização)
    # Captura: "(Usado)", "(Recondicionado)", "(Condicionado)", "(Muito Bom)", "(Excelente)", "(Bom)"
    if re.search(
        r'\((?:Usado|Usada|Recondicionado|Condicionado|Muito\s+Bom|Excelente|Bom)\)',
        titulo, re.IGNORECASE,
    ):
        return False

    # 3. "Muito Bom" como frase — condição ML para item usado, grau "Muito Bom"
    # (distinto de "muito boa câmera", que usa gênero feminino)
    if re.search(r'\bmuito\s+bom\b', titulo_norm):
        return False

    # 4. " - Bom" / " - Excelente" com traço no título ORIGINAL
    # Padrão ML: "Apple iPhone 14 128GB - Bom" / "iPhone 14 - Excelente (Recond.)"
    # Usa `titulo` original (não normalizado) pois o normalizador remove traços.
    if re.search(r'\s-\s(?:Bom|Excelente)\b', titulo, re.IGNORECASE):
        return False

    # 5. "bom" ou "excelente" como ÚLTIMO token — sem traço (padrão alternativo ML)
    # "Apple iPhone 14 128GB Excelente" → claramente item usado sem "Usado:" prefix
    tokens_titulo = titulo_norm.split()
    if tokens_titulo and tokens_titulo[-1] in {'bom', 'excelente'}:
        return False

    return True
# --- end filtro_produto_principal ---

# Cache do buildId da Magalu (invalidado automaticamente em caso de 404)
_MAGALU_BUILD_ID = None

# PyInstaller: bundled assets live in sys._MEIPASS; user data lives next to the exe
import sys as _sys
_BUNDLE_DIR = Path(getattr(_sys, '_MEIPASS', Path(__file__).parent))
_DATA_DIR = Path(_sys.executable).parent if getattr(_sys, 'frozen', False) else Path(__file__).parent

app = Flask(__name__)

@app.route('/logo2.png')
def serve_logo():
    return send_file(_BUNDLE_DIR / 'logo2.png', mimetype='image/png')

# ---------------------------------------------------------------------------
# Watchlist — persistência local em JSON
# ---------------------------------------------------------------------------
WATCHLIST_PATH = _DATA_DIR / 'watchlist.json'

# ---------------------------------------------------------------------------
# Telegram — configuração e envio
# ---------------------------------------------------------------------------
TELEGRAM_CONFIG_PATH = _DATA_DIR / 'telegram_config.json'


def _tg_load():
    if TELEGRAM_CONFIG_PATH.exists():
        try:
            return json.loads(TELEGRAM_CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'token': '', 'chat_id': ''}


def _tg_save(cfg):
    TELEGRAM_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')


def _tg_send(token, chat_id, text):
    """Envia mensagem via Telegram Bot API. Retorna (ok, erro)."""
    if not token or not chat_id:
        return False, 'Token ou Chat ID não configurado.'
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get('ok', False), None
    except Exception as e:
        return False, str(e)


def _tg_notify_price(item, old_best, new_best, trend):
    """Envia alerta de preço via Telegram se configurado e habilitado para o item."""
    if not new_best or not isinstance(new_best.get('preco'), (int, float)):
        return
    cfg = _tg_load()
    if not cfg.get('token') or not cfg.get('chat_id'):
        return

    def fmt(v):
        return f"R$ {v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

    # Preço mais baixo — só se havia preço anterior para comparar
    if item.get('notificar_preco_baixo', True) and trend == 'down' and old_best:
        savings = old_best['preco'] - new_best['preco']
        if savings > 0.01:
            msg = (
                f"🔔 <b>TechOfertas — Queda de Preço!</b>\n\n"
                f"📦 {new_best.get('nome') or item.get('query', '')}\n"
                f"💰 <b>{fmt(new_best['preco'])}</b> ({fmt(savings)} mais barato)\n"
                f"🏪 {new_best.get('loja', '')}"
            )
            if new_best.get('link'):
                msg += f"\n🛒 <a href=\"{new_best['link']}\">Ver oferta</a>"
            _tg_send(cfg['token'], cfg['chat_id'], msg)

    # Valor alvo atingido — notifica apenas na primeira vez que cruza o limiar
    target = item.get('notificar_valor_alvo')
    if target and new_best['preco'] <= target:
        if not old_best or old_best['preco'] > target:
            msg = (
                f"🎯 <b>TechOfertas — Valor Alvo Atingido!</b>\n\n"
                f"📦 {new_best.get('nome') or item.get('query', '')}\n"
                f"💰 <b>{fmt(new_best['preco'])}</b> (alvo: {fmt(target)})\n"
                f"🏪 {new_best.get('loja', '')}"
            )
            if new_best.get('link'):
                msg += f"\n🛒 <a href=\"{new_best['link']}\">Ver oferta</a>"
            _tg_send(cfg['token'], cfg['chat_id'], msg)


def _wl_load():
    if WATCHLIST_PATH.exists():
        try:
            return json.loads(WATCHLIST_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'items': []}


def _wl_save(data):
    WATCHLIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def _wl_is_stale(item, minutos=15):
    ts = item.get('ultima_busca')
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() > minutos * 60
    except Exception:
        return True


def _parse_json_ld(html):
    """Extrai nome+preço de schema.org/Product via JSON-LD."""
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            items = data if isinstance(data, list) else [data]
            for it in items:
                if it.get('@type') != 'Product':
                    continue
                offers = it.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price_raw = offers.get('price') or offers.get('lowPrice')
                if price_raw is None:
                    continue
                try:
                    preco = float(str(price_raw).replace(',', '.'))
                except Exception:
                    continue
                if preco <= 0:
                    continue
                nome = (it.get('name') or '').strip()
                imagem = it.get('image', '')
                if isinstance(imagem, list):
                    imagem = imagem[0] if imagem else ''
                return {'nome': nome, 'preco': preco, 'imagem': str(imagem)}
        except Exception:
            pass
    return None


def _preco_kabum_link(url):
    html = http_get(url, referer='https://www.kabum.com.br/')
    o = _parse_json_ld(html)
    if o:
        o.update({'link': url, 'loja': 'KaBuM!'})
        return o
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        pp = data.get('props', {}).get('pageProps', {})
        product = pp.get('product') or pp.get('data', {}).get('product', {}) or {}
        nome = (product.get('name') or product.get('title') or '').strip()
        preco = kabum_extrair_preco(product)
        if not preco:
            return None
        imagem = str(product.get('image') or product.get('thumbnail') or '')
        return {'nome': nome, 'preco': preco, 'link': url, 'imagem': imagem, 'loja': 'KaBuM!'}
    except Exception:
        return None


def _preco_pichau_link(url):
    scraper = _nova_sessao_scraper()
    html = _scraper_get(scraper, url, 'https://www.pichau.com.br/')
    o = _parse_json_ld(html)
    if o:
        o.update({'link': url, 'loja': 'Pichau'})
        return o
    nm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    nome = unescape(re.sub(r'<[^>]+>', '', nm.group(1)).strip()) if nm else ''
    pm = re.search(r'class="[^"]*-pixPrice"[^>]*><span>(R\$[^<]+)</span>', html)
    if not pm:
        pm2 = re.search(r'"priceWithDiscount"\s*:\s*"?([\d.]+)', html)
        preco = float(pm2.group(1)) if pm2 else None
    else:
        preco = formatar_preco(unescape(pm.group(1)))
    if not preco:
        return None
    imgm = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
    return {'nome': nome, 'preco': preco, 'link': url, 'imagem': imgm.group(1) if imgm else '', 'loja': 'Pichau'}


def _preco_terabyte_link(url):
    scraper = _nova_sessao_scraper()
    html = _scraper_get(scraper, url, 'https://www.terabyteshop.com.br/')
    o = terabyte_parse_pagina_produto(html, url)
    if o:
        o['loja'] = 'Terabyte'
    return o


def _preco_ml_link(url):
    html = http_get(url, referer='https://www.mercadolivre.com.br/')
    o = _parse_json_ld(html)
    if o:
        o.update({'link': url, 'loja': 'Mercado Livre'})
        return o
    nm = re.search(r'<h1[^>]*class="[^"]*ui-pdp-title[^"]*"[^>]*>([^<]+)</h1>', html)
    if not nm:
        nm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    nome = unescape(nm.group(1).strip()) if nm else ''
    pm = re.search(r'aria-label="(?:Agora:\s*)?([\d.,]+) reais(?:\s+com\s+(\d+)\s+centavos)?"', html)
    if not pm:
        return None
    try:
        reais = float(pm.group(1).replace('.', '').replace(',', '.'))
        centavos = int(pm.group(2) or 0)
        preco = reais + centavos / 100
    except Exception:
        return None
    imgm = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
    return {'nome': nome, 'preco': preco, 'link': url, 'imagem': imgm.group(1) if imgm else '', 'loja': 'Mercado Livre'}


def _preco_magalu_link(url):
    html = http_get(url, referer='https://www.magazineluiza.com.br/')
    o = _parse_json_ld(html)
    if o:
        o.update({'link': url, 'loja': 'Magalu'})
        return o
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        product = data.get('props', {}).get('pageProps', {}).get('data', {}).get('product', {})
        nome = (product.get('title') or product.get('name') or '').strip()
        price_data = product.get('price') or {}
        preco_str = price_data.get('bestPrice') or price_data.get('fullPrice')
        preco = float(preco_str) if preco_str else None
        if not preco:
            return None
        img = product.get('image') or ''
        imagem = img.replace('{w}x{h}', '400x400') if '{w}' in img else img
        return {'nome': nome, 'preco': preco, 'link': url, 'imagem': imagem, 'loja': 'Magalu'}
    except Exception:
        return None


def _preco_amazon_link(url):
    scraper = _nova_sessao_scraper()
    html = _scraper_get(scraper, url, 'https://www.amazon.com.br/')
    o = _parse_json_ld(html)
    if o:
        o.update({'link': url, 'loja': 'Amazon'})
        return o
    nm = re.search(r'<span id="productTitle"[^>]*>([^<]+)</span>', html)
    if not nm:
        nm = re.search(r'<h1[^>]*>.*?<span[^>]*>([^<]+)</span>', html, re.DOTALL)
    nome = unescape(nm.group(1).strip()) if nm else ''
    pm = re.search(r'<span class="a-price"[^>]*>.*?R\$[\s\xa0]*([\d.,]+)', html, re.DOTALL)
    if not pm:
        return None
    preco = formatar_preco(pm.group(1))
    if not preco:
        return None
    imgm = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
    if not imgm:
        imgm = re.search(r'"hiRes":"(https://m\.media-amazon\.com[^"]+)"', html)
    return {'nome': nome, 'preco': preco, 'link': url, 'imagem': imgm.group(1) if imgm else '', 'loja': 'Amazon'}


def _preco_por_link(url):
    """Dado um URL de produto, retorna oferta atual {nome,preco,link,imagem,loja} ou None."""
    url = url.strip()
    try:
        if 'kabum.com.br' in url:
            return _preco_kabum_link(url)
        if 'pichau.com.br' in url:
            return _preco_pichau_link(url)
        if 'terabyteshop.com.br' in url:
            return _preco_terabyte_link(url)
        if 'mercadolivre.com.br' in url or 'mercadolibre.com' in url:
            return _preco_ml_link(url)
        if 'magazineluiza.com.br' in url or 'magalu.com' in url:
            return _preco_magalu_link(url)
        if 'amazon.com.br' in url:
            return _preco_amazon_link(url)
    except Exception as e:
        print(f'_preco_por_link {url}: {e}')
    return None


def _wl_buscar_item(item):
    """Executa busca. Retorna (melhor_oferta, {loja: [ofertas]})."""
    # Link-specific tracking — scrapes the exact product page
    if item.get('link'):
        oferta = _preco_por_link(item['link'])
        if oferta:
            loja_key = (oferta.get('loja') or 'loja').lower().replace(' ', '').replace('!', '')
            return oferta, {loja_key: [oferta]}
        return None, {}

    import concurrent.futures as cf
    buscadores = {
        'kabum': buscar_kabum, 'pichau': buscar_pichau,
        'terabyte': buscar_terabyte, 'mercadolivre': buscar_mercadolivre,
        'magalu': buscar_magalu, 'amazon': buscar_amazon, 'shopee': buscar_shopee,
    }
    lojas = [s for s, v in (item.get('lojas') or {}).items() if v and s in buscadores]
    vm = float(item.get('valor_minimo') or 0)
    vmax_raw = item.get('valor_maximo')
    vmax = float(vmax_raw) if vmax_raw else float('inf')
    query = item.get('query', '')

    por_loja = {}
    with cf.ThreadPoolExecutor() as executor:
        fut_map = {executor.submit(buscadores[s], query, vm, vmax): s for s in lojas}
        try:
            for fut in cf.as_completed(fut_map, timeout=90):
                site = fut_map[fut]
                try:
                    ofertas, _ = fut.result()
                    for o in ofertas:
                        o['loja'] = site.capitalize()
                    por_loja[site] = ofertas
                except Exception:
                    por_loja[site] = []
        except cf.TimeoutError:
            pass

    todas = [o for lst in por_loja.values() for o in lst]
    validas = [o for o in todas if isinstance(o.get('preco'), (int, float)) and o['preco'] > 0]
    best = min(validas, key=lambda x: x['preco']) if validas else None
    return best, por_loja


def _wl_salvar_resultado(item_id, best):
    """Persiste resultado no JSON e retorna trend ('down'|'up'|'same'|None)."""
    wl = _wl_load()
    trend = None
    saved_item = None
    saved_old = None
    for it in wl['items']:
        if it['id'] == item_id:
            old = it.get('melhor_preco')
            saved_old = old
            now_ts = datetime.now(timezone.utc).isoformat()
            it['ultima_busca'] = now_ts
            it['melhor_preco'] = best
            if best:
                hist = (it.get('historico') or [])[-19:]
                hist.append({'ts': now_ts, 'preco': best['preco'], 'loja': best.get('loja', '')})
                it['historico'] = hist
            if old and best:
                diff = best['preco'] - old['preco']
                trend = 'down' if diff < -0.01 else ('up' if diff > 0.01 else 'same')
            saved_item = it
            break
    _wl_save(wl)
    if saved_item and saved_old:
        try:
            _tg_notify_price(saved_item, saved_old, best, trend)
        except Exception:
            pass
    return trend


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TechOfertas - Seu Buscador de Ofertas!</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="icon" href="/logo2.png" type="image/png">

    <style>
        :root {
            --primary-color: #2563eb;
            --primary-hover: #1d4ed8;
            --secondary-color: #64748b;
            --accent-color: #f59e0b;
            --success-color: #10b981;
            --error-color: #ef4444;
            --warning-color: #f59e0b;
            --background: #f8fafc;
            --surface: #ffffff;
            --border: #e2e8f0;
            --text-primary: #1e293b;
            --text-secondary: #64748b;
            --text-muted: #94a3b8;
            --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
            --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
            --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
            --shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
            --radius-sm: 0.375rem;
            --radius-md: 0.5rem;
            --radius-lg: 0.75rem;
            --radius-xl: 1rem;
            --radius-2xl: 1.5rem;
            --header-bg: rgba(255, 255, 255, 0.95);
        }

        [data-theme="dark"] {
            --background: #0f172a;
            --surface: #1e293b;
            --border: #2d3f55;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.4);
            --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.5), 0 2px 4px -2px rgb(0 0 0 / 0.4);
            --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.5), 0 4px 6px -4px rgb(0 0 0 / 0.4);
            --shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.5), 0 8px 10px -6px rgb(0 0 0 / 0.4);
            --header-bg: rgba(15, 23, 42, 0.95);
        }
        [data-theme="dark"] .badge-sem-juros { background: rgb(16 185 129 / 0.18); color: #6ee7b7; }
        [data-theme="dark"] .badge-com-juros { background: rgb(239 68 68 / 0.18); color: #fca5a5; }
        [data-theme="dark"] .badge-melhor-compra { background: linear-gradient(135deg, rgb(99 102 241 / 0.18), rgb(139 92 246 / 0.18)); color: #a5b4fc; border-color: rgba(129, 140, 248, 0.3); }
        [data-theme="dark"] .trend-down { background: rgb(16 185 129 / 0.18); color: #6ee7b7; }
        [data-theme="dark"] .trend-up   { background: rgb(239 68 68 / 0.18); color: #fca5a5; }
        [data-theme="dark"] .trend-same { background: rgb(100 116 139 / 0.25); color: var(--text-secondary); }
        [data-theme="dark"] .auto-update-banner { background: rgb(245 158 11 / 0.12); border-color: rgb(245 158 11 / 0.3); color: #fbbf24; }
        [data-theme="dark"] .skeleton-item { background: linear-gradient(90deg, #1e293b 25%, #334155 50%, #1e293b 75%); background-size: 200% 100%; }
        [data-theme="dark"] .modal-close { background: var(--surface); color: var(--text-primary); }
        [data-theme="dark"] .store-checkbox:checked + .store-label { background: rgb(37 99 235 / 0.15); }
        [data-theme="dark"] .search-status-bar { background: rgb(37 99 235 / 0.08); border-color: rgb(37 99 235 / 0.25); }
        [data-theme="dark"] .search-status-bar.done { background: rgb(16 185 129 / 0.08); border-color: rgb(16 185 129 / 0.25); }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--background);
            color: var(--text-primary);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        /* Layout Principal */
        .app-container {
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        /* Header */
        .app-header {
            border-bottom: 1px solid var(--border);
            padding: 1rem 0;
            position: sticky;
            top: 0;
            z-index: 50;
            backdrop-filter: blur(8px);
            background: var(--header-bg);
        }

        .header-content {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1.5rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 2rem;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--primary-color);
            text-decoration: none;
        }

        .logo-icon {
            font-size: 2rem;
        }
        .logo-img {
            height: 70px;
        }

        /* Main Content */
        .main-content {
            flex: 1;
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem 1.5rem;
            width: 100%;
        }

        /* Search Card */
        .search-card {
            background: var(--surface);
            border-radius: var(--radius-2xl);
            box-shadow: var(--shadow-lg);
            padding: 2.5rem;
            margin-bottom: 2rem;
            margin-left: auto;
            margin-right: auto;
            max-width: 820px;
            border: 1px solid var(--border);
            overflow: hidden;
            box-sizing: border-box;
        }


        .search-title {
            text-align: center;
            margin-bottom: 2rem;
            color: var(--text-primary);
        }

        .search-title h1 {
            font-size: 2.25rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, var(--primary-color), var(--accent-color));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        .search-title p {
            color: var(--text-secondary);
            font-size: 1.125rem;
        }

        /* Form Styles */
        .search-form {
            display: grid;
            gap: 1.5rem;
            width: 100%;
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            min-width: 0;
        }

        .form-label {
            font-weight: 600;
            color: var(--text-primary);
            font-size: 0.875rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .form-input {
            padding: 0.875rem 1rem;
            border: 2px solid var(--border);
            border-radius: var(--radius-lg);
            font-size: 1rem;
            transition: all 0.2s ease;
            background: var(--surface);
            color: var(--text-primary);
            width: 100%;
            box-sizing: border-box;
            min-width: 0;
        }

        .form-input:focus {
            outline: none;
            border-color: var(--primary-color);
            box-shadow: 0 0 0 3px rgb(37 99 235 / 0.1);
        }

        .form-input::placeholder {
            color: var(--text-muted);
        }

        /* Store Selection */
        .stores-section {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .stores-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
        }

        .store-option {
            position: relative;
            cursor: pointer;
            min-width: 0;
        }

        .store-checkbox {
            position: absolute;
            opacity: 0;
            cursor: pointer;
        }

        .store-label {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            padding: 0.75rem 0.5rem;
            border: 2px solid var(--border);
            border-radius: var(--radius-lg);
            background: var(--surface);
            transition: all 0.2s ease;
            font-weight: 500;
            min-height: 3.25rem;
            text-align: center;
            white-space: nowrap;
            box-sizing: border-box;
            overflow: hidden;
            min-width: 0;
            width: 100%;
        }

        .store-checkbox:checked + .store-label {
            border-color: var(--primary-color);
            background: rgb(37 99 235 / 0.05);
            color: var(--primary-color);
        }

        .store-checkbox:checked + .store-label::before {
            content: "✓";
            color: var(--primary-color);
            font-weight: bold;
        }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            padding: 0.875rem 1.5rem;
            border: none;
            border-radius: var(--radius-lg);
            font-size: 1rem;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }

        .btn-primary {
            background: var(--primary-color);
            color: white;
        }

        .btn-primary:hover {
            background: var(--primary-hover);
            transform: translateY(-1px);
            box-shadow: var(--shadow-md);
        }

        .btn-secondary {
            background: var(--secondary-color);
            color: white;
        }

        .btn-secondary:hover {
            background: #475569;
        }

        .btn-outline {
            background: transparent;
            color: var(--text-secondary);
            border: 2px solid var(--border);
        }

        .btn-outline:hover {
            background: var(--text-secondary);
            color: white;
        }

        .load-more-wrap {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            padding: 8px 0 4px;
        }
        .load-more-btn {
            background: none;
            border: none;
            color: var(--primary);
            font-size: 0.8125rem;
            cursor: pointer;
            padding: 6px 14px;
            border-radius: 6px;
            transition: background 0.15s;
            font-weight: 500;
        }
        .load-more-btn:hover {
            background: var(--hover-bg, rgba(99,102,241,0.08));
        }
        .load-more-count {
            color: var(--text-muted);
            font-weight: 400;
            font-size: 0.75rem;
            margin-left: 4px;
        }
        .load-less-btn {
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 0.6875rem;
            cursor: pointer;
            padding: 4px 10px;
            border-radius: 5px;
            transition: background 0.15s, color 0.15s;
        }
        .load-less-btn:hover {
            background: rgba(127,127,127,0.1);
            color: var(--text-secondary);
        }

        .search-actions {
            display: flex;
            gap: 1rem;
            justify-content: center;
            margin-top: 1rem;
        }

        /* Results Section */
        .results-section {
            display: none;
            animation: fadeIn 0.5s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .results-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 2px solid var(--border);
            flex-wrap: wrap;
            gap: 0.75rem;
        }

        .results-title {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
        }

        .results-count {
            color: var(--text-secondary);
            font-size: 0.875rem;
        }

        .search-query-display {
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
            margin-top: 0.25rem;
            margin-bottom: 0.25rem;
        }

        .search-query-label {
            font-size: 0.875rem;
            color: var(--text-secondary);
            font-weight: 400;
        }

        .search-query-term {
            font-size: 1rem;
            font-weight: 600;
            color: var(--accent-color);
            padding: 0.125rem 0.5rem;
            background: rgba(99, 102, 241, 0.12);
            border-radius: 4px;
            max-width: 400px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            display: inline-block;
            vertical-align: baseline;
        }

        /* Store Results */
        .store-results {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .store-card {
            background: var(--surface);
            border-radius: var(--radius-xl);
            box-shadow: var(--shadow-md);
            border: 1px solid var(--border);
            overflow: hidden;
            transition: all 0.3s ease;
        }

        .store-card:hover {
            box-shadow: var(--shadow-lg);
            transform: translateY(-2px);
        }

        .store-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 1.5rem;
            background: linear-gradient(135deg, var(--background), var(--border));
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: background 0.2s ease;
        }

        .store-header:hover {
            filter: brightness(0.97);
        }

        .store-info {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .store-icon {
            width: 2.5rem;
            height: 2.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 10px;
            background: #ffffff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.10), 0 0 0 1px rgba(0,0,0,0.07);
            flex-shrink: 0;
            overflow: hidden;
            padding: 5px;
        }

        [data-theme="dark"] .store-icon {
            background: #ffffff;
            box-shadow: 0 2px 6px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.08);
        }

        .store-logo-img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }

        .store-icon.melhores {
            background: linear-gradient(135deg, #fbbf24, #f59e0b);
            box-shadow: 0 1px 4px rgba(245,158,11,0.45);
            font-size: 1.1rem;
        }

        .store-label-logo {
            width: 18px;
            height: 18px;
            object-fit: contain;
            vertical-align: middle;
            border-radius: 4px;
            background: white;
            padding: 1px;
            flex-shrink: 0;
        }

        .store-details h3 {
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 0.25rem;
        }

        .store-count {
            color: var(--text-secondary);
            font-size: 0.875rem;
        }

        .store-toggle {
            background: none;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            padding: 0.5rem;
            border-radius: var(--radius-md);
            transition: all 0.2s ease;
        }

        .store-toggle:hover {
            background: var(--border);
            color: var(--text-primary);
        }

                 .store-content {
             max-height: 0;
             overflow: hidden;
             transition: max-height 0.4s ease;
         }

         .store-content.expanded {
             max-height: 9999px;
         }

                 .products-grid {
             padding: 1rem;
             display: grid;
             gap: 0.75rem;
         }

                 /* Product Card */
         .product-card {
             display: flex;
             gap: 0.75rem;
             padding: 1rem;
             border: 1px solid var(--border);
             border-radius: var(--radius-lg);
             background: var(--surface);
             transition: all 0.2s ease;
         }

        .product-card:hover {
            border-color: var(--primary-color);
            box-shadow: var(--shadow-md);
            transform: translateY(-1px);
        }

                 .product-image {
             width: 60px;
             height: 60px;
             object-fit: contain;
             border-radius: var(--radius-md);
             background: #f8fafc;
             border: 1px solid var(--border);
             cursor: pointer;
             transition: all 0.2s ease;
         }

        .product-image:hover {
            transform: scale(1.05);
            box-shadow: var(--shadow-md);
        }

                 .product-details {
             flex: 1;
             display: flex;
             flex-direction: column;
             gap: 0.25rem;
         }

                 .product-name {
             font-weight: 600;
             color: var(--text-primary);
             line-height: 1.3;
             font-size: 0.95rem;
         }

                 .product-price {
             font-size: 1.1rem;
             font-weight: 700;
             color: var(--success-color);
         }

        .product-price-label {
            font-size: 0.7rem;
            font-weight: 500;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        .product-installment {
            font-size: 0.85rem;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 0.35rem;
            flex-wrap: wrap;
        }

        .badge-sem-juros {
            font-size: 0.7rem;
            font-weight: 600;
            padding: 0.1rem 0.4rem;
            border-radius: 999px;
            background: #dcfce7;
            color: #15803d;
        }

        .badge-com-juros {
            font-size: 0.7rem;
            font-weight: 600;
            padding: 0.1rem 0.4rem;
            border-radius: 999px;
            background: #fee2e2;
            color: #b91c1c;
        }

        .badge-melhor-compra {
            font-size: 0.7rem;
            font-weight: 600;
            padding: 0.1rem 0.4rem;
            border-radius: 999px;
            background: linear-gradient(135deg, #dbeafe, #ede9fe);
            color: #4338ca;
            border: 1px solid rgba(99, 102, 241, 0.25);
        }

                 .product-store {
             font-size: 0.7rem;
             color: var(--text-muted);
             font-weight: 500;
             letter-spacing: 0.02em;
             margin-top: 2px;
         }

                 .product-actions {
             display: flex;
             gap: 0.5rem;
             margin-top: 0.25rem;
             flex-wrap: wrap;
         }

        .btn-sm {
            padding: 0.5rem 1rem;
            font-size: 0.875rem;
        }

        /* Loading States */
        .loading {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
            color: var(--text-secondary);
        }

        .loading-spinner {
            width: 1.5rem;
            height: 1.5rem;
            border: 2px solid var(--border);
            border-top: 2px solid var(--primary-color);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 0.5rem;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        /* Empty States */
        .empty-state {
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-secondary);
            grid-column: 1 / -1;
        }

        .empty-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
            opacity: 0.5;
        }

        /* Form/Dialog Modal */
        .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.55);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 900;
            padding: 1rem;
            backdrop-filter: blur(3px);
        }
        .form-modal-box {
            position: relative;
            background: var(--surface);
            border-radius: var(--radius-xl);
            padding: 1.75rem;
            max-width: 460px;
            width: 100%;
            box-shadow: var(--shadow-xl);
        }
        .form-modal-title {
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 1.25rem;
        }
        .form-modal-close {
            position: absolute;
            top: 1rem;
            right: 1rem;
            background: none;
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            width: 2rem;
            height: 2rem;
            font-size: 0.85rem;
            cursor: pointer;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.15s ease;
        }
        .form-modal-close:hover {
            background: var(--background);
            color: var(--text-primary);
        }

        /* Image Modal */
        .image-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(4px);
        }

        .image-modal.active {
            display: flex;
        }

        .modal-content {
            position: relative;
            max-width: 90vw;
            max-height: 90vh;
        }

        .modal-image {
            max-width: 100%;
            max-height: 100%;
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-xl);
        }

        .modal-close {
            position: absolute;
            top: -2rem;
            right: -2rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 50%;
            width: 3rem;
            height: 3rem;
            font-size: 1.5rem;
            cursor: pointer;
            box-shadow: var(--shadow-lg);
            transition: all 0.2s ease;
        }

        .modal-close:hover {
            transform: scale(1.1);
        }

        /* Scroll to Top */
        .scroll-top {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: var(--primary-color);
            color: white;
            border: none;
            border-radius: 50%;
            width: 3rem;
            height: 3rem;
            cursor: pointer;
            box-shadow: var(--shadow-lg);
            transition: all 0.2s ease;
            opacity: 0;
            visibility: hidden;
        }

        .scroll-top.visible {
            opacity: 1;
            visibility: visible;
        }

        .scroll-top:hover {
            background: var(--primary-hover);
            transform: translateY(-2px);
        }

        /* Footer */
        .app-footer {
            background: var(--surface);
            border-top: 1px solid var(--border);
            padding: 1.5rem 0;
            margin-top: auto;
        }

        .footer-content {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1.5rem;
            text-align: center;
            color: var(--text-secondary);
        }

        /* ── Desktop compact: fit search card in one viewport ─────────── */
        @media (min-width: 769px) {
            .main-content {
                padding: 1.5rem 1.5rem 1rem;
            }
            .search-card {
                padding: 1.5rem 2rem;
                margin-bottom: 0.875rem;
            }
            .search-title {
                margin-bottom: 0.75rem;
            }
            .search-title h1 {
                font-size: 1.875rem;
                margin-bottom: 0.25rem;
            }
            .search-title p {
                font-size: 1rem;
            }
            .search-form {
                gap: 0.875rem;
            }
            .form-group {
                gap: 0.3rem;
            }
            .form-input {
                padding: 0.6rem 1rem;
            }
            .stores-section {
                gap: 0.5rem;
            }
            .stores-grid {
                grid-template-columns: repeat(4, 1fr);
                gap: 0.625rem;
            }
            .store-label {
                padding: 0.6rem 0.875rem;
                min-height: 2.5rem;
            }
            .search-actions {
                margin-top: 0.25rem;
            }
        }

        /* Responsive Design */
        @media (max-width: 768px) {
            .header-content {
                flex-direction: column;
                gap: 1rem;
                text-align: center;
            }

            .search-card {
                padding: 1.5rem;
                margin: 1rem;
            }

            .search-title h1 {
                font-size: 1.875rem;
            }

            .stores-grid {
                grid-template-columns: repeat(2, 1fr);
            }

            .search-actions {
                flex-direction: column;
            }

            .store-header {
                padding: 1rem;
            }

            .product-card {
                flex-direction: column;
                text-align: center;
            }

            .product-image {
                align-self: center;
            }

            .product-actions {
                justify-content: center;
            }

            .scroll-top {
                bottom: 1rem;
                right: 1rem;
            }

            /* Image modal close button — keep inside bounds */
            .modal-close {
                top: -1rem;
                right: -1rem;
                width: 2.5rem;
                height: 2.5rem;
                font-size: 1.25rem;
            }

            /* Watchlist toolbar stacks on tablets */
            .watchlist-toolbar {
                flex-direction: column;
                align-items: stretch;
            }
            .watchlist-actions {
                justify-content: flex-start;
            }

            /* Results header — wrap layout toggle and nova busca */
            .results-header {
                flex-wrap: wrap;
                gap: 0.75rem;
            }
            .results-header > div:first-child {
                flex: 1 1 100%;
            }
            .layout-toggle {
                margin-left: 0;
                margin-right: auto;
            }

            /* Store header — compact on tablets */
            .store-details h3 {
                font-size: 1.1rem;
            }

            /* Store results gap */
            .store-results {
                gap: 1rem;
            }
        }

        @media (max-width: 480px) {
            .main-content {
                padding: 0.75rem;
            }

            .search-card {
                padding: 1rem;
                margin: 0.5rem;
                border-radius: var(--radius-lg);
            }

            .search-title h1 {
                font-size: 1.5rem;
            }
            .search-title p {
                font-size: 0.95rem;
            }

            .store-header {
                flex-direction: column;
                gap: 0.75rem;
                text-align: center;
            }

            /* Logo smaller on phones */
            .logo-img {
                height: 55px;
            }

            /* Store labels — allow wrapping on small screens */
            .store-label {
                white-space: normal;
                min-height: 2.75rem;
                font-size: 0.78rem;
                padding: 0.5rem 0.75rem;
            }

            /* Tabs — compact for phones */
            .tab-bar-inner {
                padding: 0 0.75rem;
            }
            .tab-btn {
                padding: 0.65rem 0.75rem;
                font-size: 0.82rem;
                gap: 0.35rem;
            }

            /* Results page — stack everything vertically */
            .results-header {
                margin-bottom: 1.25rem;
            }
            .results-title {
                font-size: 1.2rem;
            }
            .results-header > div:first-child {
                flex: 1 1 100%;
            }
            .layout-toggle {
                margin-left: 0;
                margin-right: 0;
                flex: 1 1 auto;
            }
            .layout-toggle-label {
                display: none;
            }
            .layout-toggle button {
                padding: 0.4rem 0.6rem;
                font-size: 0.75rem;
            }
            .results-header > .btn {
                flex: 0 0 auto;
                font-size: 0.82rem;
                padding: 0.5rem 0.875rem;
            }
            .search-query-term {
                max-width: 200px;
                font-size: 0.875rem;
            }

            /* Store card — compact on phones */
            .store-card:hover {
                transform: none;
            }
            .store-header {
                padding: 1rem;
            }
            .store-icon {
                width: 2rem;
                height: 2rem;
                border-radius: 8px;
                padding: 3px;
            }
            .store-details h3 {
                font-size: 1rem;
            }
            .store-count {
                font-size: 0.78rem;
            }
            .store-results {
                gap: 0.75rem;
            }

            /* Product cards — tighter on phones */
            .products-grid {
                padding: 0.625rem;
                gap: 0.5rem;
            }
            .product-card {
                padding: 0.75rem;
                gap: 0.5rem;
            }
            .product-card:hover {
                transform: none;
            }
            .product-name {
                font-size: 0.85rem;
            }
            .product-price {
                font-size: 1rem;
            }
            .product-installment {
                font-size: 0.78rem;
            }
            .product-actions {
                gap: 0.35rem;
            }
            .product-actions .btn-sm {
                padding: 0.4rem 0.75rem;
                font-size: 0.75rem;
            }

            /* Product image smaller on phones */
            .product-image {
                width: 50px;
                height: 50px;
            }

            /* Form modal — full width on phones */
            .form-modal-box {
                max-width: calc(100vw - 1.5rem);
                padding: 1.25rem;
            }

            /* Image modal close — inside bounds */
            .modal-close {
                top: 0.5rem;
                right: 0.5rem;
                width: 2.25rem;
                height: 2.25rem;
                font-size: 1.1rem;
            }

            /* Watchlist items — responsive layout */
            .watch-item {
                padding: 0.875rem 1rem;
                gap: 0.625rem;
                flex-wrap: wrap;
            }
            .watch-item-icon {
                font-size: 1.25rem;
            }
            .watch-item-body {
                min-width: 0;
                flex: 1 1 calc(100% - 6rem);
            }
            .watch-item-price {
                min-width: 0;
                text-align: left;
                flex: 1 1 auto;
            }
            .watch-item-actions {
                margin-left: auto;
            }

            /* Watchlist title */
            .watchlist-title {
                font-size: 1.25rem;
            }

            /* Compact layout — tighter on phones */
            .product-card.compact {
                grid-template-columns: 28px 1fr auto;
                gap: 0.25rem 0.4rem;
                padding: 0.4rem;
            }
            .compact-installment {
                font-size: 0.65rem;
            }

            /* Auto-update banner */
            .auto-update-banner {
                flex-wrap: wrap;
                padding: 0.625rem 1rem;
                font-size: 0.8rem;
            }

            /* Price toast — respect screen edges */
            .price-toast-container {
                top: 4rem;
                right: 0.75rem;
            }
        }

        @media (max-width: 380px) {
            .main-content {
                padding: 0.5rem;
            }
            .search-card {
                padding: 0.75rem;
                margin: 0.25rem;
            }
            .search-title h1 {
                font-size: 1.3rem;
            }
            .logo {
                font-size: 1.2rem;
            }
            .logo-img {
                height: 45px;
            }
            .tab-bar-inner {
                padding: 0 0.5rem;
            }
            .tab-btn {
                padding: 0.6rem 0.5rem;
                font-size: 0.78rem;
            }
            .form-modal-box {
                padding: 1rem;
                max-width: calc(100vw - 1rem);
            }
            .form-modal-title {
                font-size: 0.95rem;
            }
            .watch-item {
                padding: 0.75rem;
            }
            .stores-grid {
                gap: 0.5rem;
            }

            /* Results — ultra compact for very small screens */
            .results-title {
                font-size: 1.05rem;
            }
            .search-query-term {
                max-width: 150px;
                font-size: 0.8rem;
            }
            .results-header > .btn {
                width: 100%;
                justify-content: center;
            }
            .layout-toggle {
                flex: 1 1 100%;
                justify-content: center;
            }
            .products-grid {
                padding: 0.4rem;
            }
            .product-card {
                padding: 0.625rem;
            }
            .store-header {
                padding: 0.75rem;
            }
            .store-info {
                gap: 0.625rem;
            }
        }

        /* Utility Classes */
        .hidden {
            display: none !important;
        }

        .sr-only {
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
        }

        /* Tab Bar */
        .tab-bar {
            background: var(--surface);
            border-bottom: 1px solid var(--border);
        }
        .tab-bar-inner {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 1.5rem;
            display: flex;
            gap: 0.25rem;
        }
        .tab-btn {
            background: none;
            border: none;
            border-bottom: 3px solid transparent;
            padding: 0.75rem 1.25rem;
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-family: inherit;
            margin-bottom: -1px;
        }
        .tab-btn:hover { color: var(--primary-color); }
        .tab-btn.active {
            color: var(--primary-color);
            border-bottom-color: var(--primary-color);
        }
        .tab-badge {
            background: var(--primary-color);
            color: white;
            font-size: 0.7rem;
            font-weight: 700;
            padding: 0.1rem 0.45rem;
            border-radius: 9999px;
            min-width: 1.2rem;
            text-align: center;
        }

        /* Watchlist Section */
        .watchlist-toolbar {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            margin-bottom: 1.5rem;
            gap: 1rem;
            flex-wrap: wrap;
        }
        .watchlist-title {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 0.25rem;
        }
        .watchlist-subtitle {
            font-size: 0.875rem;
            color: var(--text-secondary);
        }
        .watchlist-actions { display: flex; gap: 0.75rem; align-items: center; flex-shrink: 0; flex-wrap: wrap; }
        .watchlist-update-status {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.75rem 1rem;
            background: rgb(37 99 235 / 0.05);
            border: 1px solid rgb(37 99 235 / 0.2);
            border-radius: var(--radius-lg);
            margin-bottom: 1.25rem;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }

        /* Watch Item Card */
        .watch-item {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-xl);
            padding: 1.125rem 1.5rem;
            display: flex;
            align-items: center;
            gap: 1rem;
            transition: all 0.2s ease;
            margin-bottom: 0.75rem;
        }
        .watch-item:hover { box-shadow: var(--shadow-md); border-color: rgb(37 99 235 / 0.25); }
        .watch-item.updating { border-color: rgb(37 99 235 / 0.4); background: rgb(37 99 235 / 0.02); }
        .watch-item-icon { font-size: 1.5rem; flex-shrink: 0; }
        .watch-item-body { flex: 1; min-width: 0; }
        .watch-item-query {
            font-weight: 600;
            color: var(--text-primary);
            font-size: 0.95rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .watch-item-query.clickable {
            cursor: pointer;
            text-decoration: underline;
            text-decoration-color: transparent;
            text-underline-offset: 2px;
            transition: color 0.15s, text-decoration-color 0.15s;
        }
        .watch-item-query.clickable:hover {
            color: var(--primary-color);
            text-decoration-color: var(--primary-color);
        }
        .btn-ver-pesquisa {
            flex-shrink: 0;
            font-size: 0.72rem;
            font-weight: 600;
            padding: 0.2rem 0.55rem;
            border: 1px solid var(--primary-color);
            border-radius: 9999px;
            background: transparent;
            color: var(--primary-color);
            cursor: pointer;
            font-family: inherit;
            white-space: nowrap;
            transition: background 0.15s, color 0.15s;
        }
        .btn-ver-pesquisa:hover {
            background: var(--primary-color);
            color: white;
        }
        .watch-item-meta { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.2rem; }
        .watch-item-price { text-align: right; flex-shrink: 0; min-width: 110px; }
        .watch-price-label { font-size: 0.7rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.1rem; }
        .watch-price-value { font-size: 1.05rem; font-weight: 700; color: var(--success-color); }
        .watch-price-loja { font-size: 0.75rem; color: var(--text-muted); }
        .trend-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.2rem;
            font-size: 0.73rem;
            font-weight: 700;
            padding: 0.15rem 0.5rem;
            border-radius: 9999px;
            margin-top: 0.2rem;
        }
        .trend-down { background: rgb(16 185 129 / 0.12); color: #065f46; }
        .trend-up   { background: rgb(239 68 68 / 0.1);  color: #991b1b; }
        .trend-same { background: var(--border);          color: var(--text-muted); }
        .watch-item-actions { display: flex; gap: 0.4rem; flex-shrink: 0; align-items: center; }
        .drag-handle {
            cursor: grab;
            color: var(--text-muted);
            font-size: 1.1rem;
            line-height: 1;
            padding: 0.2rem 0.3rem;
            flex-shrink: 0;
            user-select: none;
            opacity: 0.35;
            transition: opacity 0.15s;
            touch-action: none;
        }
        .watch-item:hover .drag-handle { opacity: 0.75; }
        .drag-handle:active { cursor: grabbing; }
        .watch-card.dragging { opacity: 0.45; transform: scale(0.985); transition: opacity 0.15s, transform 0.15s; }
        .watch-card.drag-over > .watch-item { border-color: var(--primary-color); box-shadow: 0 0 0 2px rgb(37 99 235 / 0.18); }
        .watch-card.drag-over-before { border-top: 2px solid var(--primary-color); }
        .watch-card.drag-over-after  { border-bottom: 2px solid var(--primary-color); }
        .btn-icon {
            width: 2.1rem;
            height: 2.1rem;
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            background: var(--surface);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 0.85rem;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.15s ease;
            font-family: inherit;
        }
        .btn-icon:hover { background: var(--background); border-color: var(--primary-color); color: var(--primary-color); }
        .btn-icon.danger:hover { border-color: var(--error-color); color: var(--error-color); }
        .watch-spinner {
            width: 1.1rem; height: 1.1rem;
            border: 2px solid var(--border);
            border-top-color: var(--primary-color);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            flex-shrink: 0;
        }

        /* Compact store grid inside modals */
        .modal-stores .stores-grid {
            grid-template-columns: repeat(2, 1fr);
            gap: 0.5rem;
        }
        .modal-stores .store-label {
            padding: 0.5rem 0.75rem;
            font-size: 0.85rem;
            gap: 0.5rem;
        }

        /* Watch button (em product cards) */
        .btn-watch {
            background: transparent;
            color: var(--text-muted);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 0.38rem 0.6rem;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.15s ease;
            font-family: inherit;
            white-space: nowrap;
        }
        .btn-watch:hover { background: rgb(37 99 235 / 0.07); border-color: var(--primary-color); color: var(--primary-color); }

        /* ═══════ Layout Toggle Buttons ═══════ */
        .layout-toggle { display: flex; align-items: center; gap: 0.35rem; margin-left: auto; margin-right: 0.75rem; }
        .layout-toggle-label {
            font-size: 0.72rem; font-weight: 500; color: var(--text-muted);
            letter-spacing: 0.03em; text-transform: uppercase;
            padding-right: 0.5rem;
            border-right: 1px solid var(--border);
            margin-right: 0.1rem;
            white-space: nowrap;
        }
        .layout-toggle button {
            background: transparent; border: 1px solid var(--border); border-radius: var(--radius-md);
            padding: 0.35rem 0.65rem; font-size: 0.78rem; line-height: 1; cursor: pointer;
            color: var(--text-muted); transition: all 0.15s ease; display: flex; align-items: center; gap: 0.3rem;
            font-weight: 500; white-space: nowrap;
        }
        .layout-toggle button:hover { border-color: var(--primary-color); color: var(--primary-color); }
        .layout-toggle button.active { background: var(--primary-color); border-color: var(--primary-color); color: #fff; }

        /* ═══════ Compact Layout ═══════ */
        .products-grid.compact { padding: 0; gap: 0; }

        .product-card.compact {
            display: grid;
            grid-template-columns: 36px 1fr 88px 180px 82px;
            align-items: center;
            gap: 0.75rem;
            padding: 0.5rem 0.75rem;
            border: none; border-bottom: 1px solid var(--border);
            border-radius: 0; background: var(--surface);
            transition: background 0.15s ease;
        }
        .product-card.compact:last-child { border-bottom: none; }
        .product-card.compact:hover { background: rgb(37 99 235 / 0.04); transform: none; box-shadow: none; border-color: var(--border); }
        .products-grid.compact .product-card.compact:nth-child(even) { background: rgba(0,0,0,0.025); }
        .products-grid.compact .product-card.compact:nth-child(even):hover { background: rgb(37 99 235 / 0.04); }

        .compact-img { width: 36px; height: 36px; object-fit: contain; border-radius: 4px; background: #f8fafc; border: 1px solid var(--border); cursor: pointer; flex-shrink: 0; }
        .compact-img-placeholder { width: 36px; height: 36px; border-radius: 4px; background: var(--border); flex-shrink: 0; }
        .compact-name { font-weight: 600; font-size: 0.82rem; color: var(--text-primary); line-height: 1.25; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; min-width: 0; }
        .compact-price-col { text-align: right; white-space: nowrap; flex-shrink: 0; }
        .compact-price { font-size: 0.95rem; font-weight: 700; color: var(--success-color); line-height: 1.2; }
        .compact-price-label { font-size: 0.6rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
        .compact-installment { font-size: 0.72rem; color: var(--text-secondary); justify-self: end; }
        .compact-inst-row { display: flex; align-items: center; gap: 0.3rem; }
        .compact-credit-total { font-weight: 600; color: var(--text-primary); }
        .compact-melhor-compra { font-size: 0.6rem; color: #4338ca; background: linear-gradient(135deg,#dbeafe,#ede9fe); border: 1px solid rgba(99,102,241,0.25); border-radius: 999px; padding: 0.05rem 0.35rem; font-weight: 600; cursor: default; }
        [data-theme="dark"] .compact-melhor-compra { background: linear-gradient(135deg,rgb(99 102 241 / 0.18),rgb(139 92 246 / 0.18)); color: #a5b4fc; border-color: rgba(129,140,248,0.3); }
        .compact-installment .badge-sem-juros, .compact-installment .badge-com-juros, .compact-installment .badge-melhor-compra { font-size: 0.6rem; padding: 0.05rem 0.3rem; }
        .compact-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
        .compact-actions .btn-sm { padding: 0.3rem 0.6rem; font-size: 0.75rem; }
        .compact-actions .btn-watch { padding: 0.28rem 0.45rem; font-size: 0.72rem; }

        .compact-header-row {
            display: grid; grid-template-columns: 36px 1fr 88px 180px 82px;
            gap: 0.75rem; padding: 0.4rem 0.75rem;
            background: var(--background); border-bottom: 2px solid var(--border);
            font-size: 0.68rem; font-weight: 600; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.05em;
        }
        .compact-header-row span:nth-child(3),
        .compact-header-row span:nth-child(4) { text-align: right; }

        [data-theme="dark"] .compact-img { background: #1e293b; border-color: #334155; }
        [data-theme="dark"] .compact-img-placeholder { background: #334155; }
        [data-theme="dark"] .products-grid.compact .product-card.compact:nth-child(even) { background: rgba(255,255,255,0.04); }

        @media (max-width: 768px) {
            .product-card.compact { grid-template-columns: 36px 1fr auto; grid-template-rows: auto auto; gap: 0.35rem 0.5rem; padding: 0.5rem; }
            .product-card.compact .compact-img, .product-card.compact .compact-img-placeholder { grid-row: 1 / 3; }
            .product-card.compact .compact-name { grid-column: 2 / 3; grid-row: 1; }
            .product-card.compact .compact-price-col { grid-column: 3; grid-row: 1; }
            .product-card.compact .compact-installment { grid-column: 2; grid-row: 2; text-align: left; min-width: 0; }
            .product-card.compact .compact-actions { grid-column: 3; grid-row: 2; }
            .compact-header-row { display: none; }
        }

        /* ═══════════════════════════════════════════════════════════
           E-COMMERCE GRID CARD LAYOUT
           ═══════════════════════════════════════════════════════════ */

        .products-grid.grid-cards {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
            gap: 0.875rem;
            padding: 1rem;
        }

        .product-card.grid-card {
            display: flex;
            flex-direction: column;
            padding: 0;
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            background: var(--surface);
            overflow: hidden;
            transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
            position: relative;
            height: 100%;
            text-align: left;
            gap: 0;
        }
        .product-card.grid-card:hover {
            border-color: var(--primary-color);
            box-shadow: 0 8px 25px -5px rgb(0 0 0 / 0.1), 0 4px 10px -6px rgb(0 0 0 / 0.08);
            transform: translateY(-3px);
        }
        [data-theme="dark"] .product-card.grid-card:hover {
            box-shadow: 0 8px 25px -5px rgb(0 0 0 / 0.4), 0 4px 10px -6px rgb(0 0 0 / 0.3);
        }

        /* Image */
        .grid-card .grid-card-image-wrap {
            position: relative;
            width: 100%;
            aspect-ratio: 1 / 1;
            background: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            border-bottom: 1px solid var(--border);
            overflow: hidden;
            cursor: pointer;
        }
        [data-theme="dark"] .grid-card .grid-card-image-wrap {
            background: #0f172a;
        }
        .grid-card .grid-card-image-wrap img {
            max-width: 80%;
            max-height: 80%;
            object-fit: contain;
            transition: transform 0.3s ease;
        }
        .grid-card:hover .grid-card-image-wrap img {
            transform: scale(1.05);
        }

        /* Card Body */
        .grid-card .grid-card-body {
            display: flex;
            flex-direction: column;
            flex: 1;
            padding: 0.75rem;
            gap: 0.3rem;
        }

        /* Store */
        .grid-card .grid-card-store {
            font-size: 0.68rem;
            color: var(--text-muted);
            font-weight: 500;
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }

        /* Product Name */
        .grid-card .grid-card-name {
            font-size: 0.84rem;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.35;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
            min-height: 2.3em;
        }

        /* Price Label */
        .grid-card .grid-card-price-label {
            font-size: 0.63rem;
            font-weight: 500;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-top: 0.2rem;
            margin-bottom: -0.1rem;
        }

        /* Price */
        .grid-card .grid-card-price {
            font-size: 1.2rem;
            font-weight: 700;
            color: var(--success-color);
            line-height: 1.2;
        }

        /* Installment */
        .grid-card .grid-card-installment {
            font-size: 0.73rem;
            color: var(--text-secondary);
            line-height: 1.3;
            display: flex;
            align-items: center;
            gap: 0.25rem;
            flex-wrap: wrap;
        }
        .grid-card .grid-card-installment .badge-sem-juros,
        .grid-card .grid-card-installment .badge-com-juros,
        .grid-card .grid-card-installment .badge-melhor-compra {
            font-size: 0.63rem;
            padding: 0.1rem 0.35rem;
        }

        /* Actions */
        .grid-card .grid-card-actions {
            display: flex;
            flex-direction: column;
            gap: 0.35rem;
            margin-top: auto;
            padding-top: 0.5rem;
        }
        .grid-card .grid-card-actions .btn {
            width: 100%;
            text-align: center;
            justify-content: center;
            font-size: 0.78rem;
            padding: 0.45rem 0.75rem;
            border-radius: var(--radius-md);
        }
        .grid-card .grid-card-actions .btn-watch {
            width: 100%;
            text-align: center;
            justify-content: center;
            font-size: 0.73rem;
            padding: 0.35rem 0.75rem;
            background: transparent;
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.15s ease;
            font-family: inherit;
        }
        .grid-card .grid-card-actions .btn-watch:hover {
            border-color: var(--primary-color);
            color: var(--primary-color);
            background: rgb(37 99 235 / 0.05);
        }

        /* Grid Cards Responsive */
        @media (max-width: 768px) {
            .products-grid.grid-cards {
                gap: 0.75rem;
                padding: 0.75rem;
            }
            .grid-card .grid-card-name { font-size: 0.8rem; }
            .grid-card .grid-card-price { font-size: 1.1rem; }
        }
        @media (max-width: 480px) {
            .products-grid.grid-cards {
                grid-template-columns: repeat(2, 1fr);
                gap: 0.5rem;
                padding: 0.5rem;
            }
            .grid-card .grid-card-body { padding: 0.5rem; gap: 0.2rem; }
            .grid-card .grid-card-name { font-size: 0.74rem; min-height: 2em; }
            .grid-card .grid-card-price { font-size: 1rem; }
            .grid-card .grid-card-price-label { font-size: 0.58rem; }
            .grid-card .grid-card-installment { font-size: 0.66rem; }
            .grid-card .grid-card-actions .btn { font-size: 0.73rem; padding: 0.4rem 0.5rem; }
            .grid-card .grid-card-actions .btn-watch { font-size: 0.66rem; padding: 0.3rem 0.5rem; }
            .product-card.grid-card:hover { transform: none; }
        }
        @media (max-width: 380px) {
            .products-grid.grid-cards { gap: 0.35rem; padding: 0.35rem; }
            .grid-card .grid-card-body { padding: 0.4rem; }
            .grid-card .grid-card-store { display: none; }
            .grid-card .grid-card-installment { font-size: 0.6rem; }
        }

        /* Watchlist empty */
        .watchlist-empty { text-align: center; padding: 4rem 2rem; color: var(--text-secondary); }
        .watchlist-empty .empty-icon { font-size: 3rem; opacity: 0.35; margin-bottom: 1rem; }

        /* Auto-update banner */
        .auto-update-banner {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.75rem 1.25rem;
            background: rgb(245 158 11 / 0.08);
            border: 1px solid rgb(245 158 11 / 0.3);
            border-radius: var(--radius-lg);
            margin-bottom: 1rem;
            font-size: 0.875rem;
            color: #92400e;
            cursor: pointer;
            transition: background 0.2s;
        }
        .auto-update-banner:hover { background: rgb(245 158 11 / 0.14); }
        .auto-update-banner a { font-weight: 600; color: inherit; margin-left: auto; text-decoration: none; }

        /* Watch card wrapper (card row + collapsible expander) */
        .watch-card { margin-bottom: 0.75rem; }
        .watch-card .watch-item { margin-bottom: 0; }
        .watch-card:not(.expanded) .watch-item { border-radius: var(--radius-xl); }
        .watch-card.expanded .watch-item { border-radius: var(--radius-xl) var(--radius-xl) 0 0; }

        /* Expandable results panel */
        .watch-expander {
            border: 1px solid var(--border);
            border-top: none;
            border-radius: 0 0 var(--radius-xl) var(--radius-xl);
            overflow: hidden;
            max-height: 0;
            transition: max-height 0.35s ease;
            background: var(--background);
        }
        .watch-expander.open { max-height: 9999px; }
        .watch-expander-inner { padding: 0.875rem 1.5rem 1rem; }
        .expander-store-group { margin-bottom: 0.875rem; }
        .expander-store-title {
            font-size: 0.75rem; font-weight: 700; color: var(--text-secondary);
            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.4rem;
        }
        .expander-product-row {
            display: flex; align-items: center; gap: 0.75rem;
            padding: 0.4rem 0; border-bottom: 1px solid var(--border);
            font-size: 0.84rem;
        }
        .expander-product-row:last-child { border-bottom: none; }
        .expander-product-name {
            flex: 1; min-width: 0; color: var(--text-primary);
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .expander-product-price { font-weight: 700; color: var(--success-color); flex-shrink: 0; }
        .expander-link-btn {
            flex-shrink: 0; font-size: 0.75rem; padding: 0.2rem 0.5rem;
            border: 1px solid var(--border); border-radius: var(--radius-md);
            background: var(--surface); color: var(--text-secondary);
            cursor: pointer; text-decoration: none; font-family: inherit;
            transition: all 0.15s ease;
        }
        .expander-link-btn:hover { border-color: var(--primary-color); color: var(--primary-color); }
        .expander-empty { font-size: 0.83rem; color: var(--text-muted); padding: 0.3rem 0; }
        .expander-no-data { font-size: 0.875rem; color: var(--text-muted); padding: 0.5rem 0; text-align: center; }

        /* Expand toggle button */
        .btn-expand {
            background: transparent; border: 1px solid var(--border);
            border-radius: var(--radius-md); padding: 0.3rem 0.6rem;
            font-size: 0.78rem; cursor: pointer; transition: all 0.15s ease;
            color: var(--text-secondary); font-family: inherit; white-space: nowrap;
        }
        .btn-expand:hover { border-color: var(--primary-color); color: var(--primary-color); }

        /* Confirmation modal overlay */
        .confirm-overlay {
            position: fixed; inset: 0;
            background: rgba(0,0,0,0.5);
            display: flex; align-items: center; justify-content: center;
            z-index: 1000; padding: 1rem;
        }
        .confirm-box {
            background: var(--surface); border-radius: var(--radius-xl);
            padding: 1.75rem; max-width: 400px; width: 100%;
            box-shadow: var(--shadow-xl);
        }
        .confirm-box-title { font-size: 1.05rem; font-weight: 700; margin-bottom: 0.4rem; color: var(--text-primary); }
        .confirm-box-body { font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1.5rem; line-height: 1.5; }
        .confirm-box-actions { display: flex; gap: 0.75rem; justify-content: flex-end; }

        /* Search Status Bar */
        .search-status-bar {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.875rem 1.25rem;
            background: rgb(37 99 235 / 0.05);
            border: 1px solid rgb(37 99 235 / 0.2);
            border-radius: var(--radius-lg);
            overflow-wrap: anywhere;
            margin-bottom: 1.5rem;
            color: var(--text-secondary);
            font-size: 0.9rem;
            transition: background 0.4s ease, border-color 0.4s ease, color 0.4s ease;
        }
        .search-status-bar.done {
            background: rgb(16 185 129 / 0.05);
            border-color: rgb(16 185 129 / 0.3);
            color: var(--success-color);
            font-weight: 500;
        }
        .search-status-bar.done .status-spinner-small {
            display: none;
        }
        .status-spinner-small {
            width: 1rem;
            height: 1rem;
            border: 2px solid var(--border);
            border-top-color: var(--primary-color);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            flex-shrink: 0;
        }

        /* Skeleton Loading */
        .skeleton-container {
            padding: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        .skeleton-item {
            height: 76px;
            background: linear-gradient(90deg, #f1f5f9 25%, #e2e8f0 50%, #f1f5f9 75%);
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
            border-radius: var(--radius-lg);
        }
        @keyframes shimmer {
            0% { background-position: 200% 0; }
            100% { background-position: -200% 0; }
        }
        .store-card.skeleton-card .store-header {
            cursor: default;
        }

        /* Toast Notification */
        .toast {
            position: fixed;
            top: 1.25rem;
            left: 50%;
            transform: translateX(-50%);
            padding: 0.875rem 1.5rem;
            border-radius: var(--radius-lg);
            font-weight: 500;
            font-size: 0.95rem;
            z-index: 9999;
            animation: fadeIn 0.3s ease;
            max-width: 90vw;
            text-align: center;
            box-shadow: var(--shadow-lg);
        }
        .toast.error {
            background: var(--error-color);
            color: white;
        }
        .toast.info {
            background: var(--primary-color);
            color: white;
        }
        .toast.success {
            background: var(--success-color);
            color: white;
        }

        /* === Alert Section (modals) === */
        .alert-section {
            margin-top: 1rem;
            padding-top: 1rem;
            border-top: 1px solid var(--border);
        }
        .alert-section-title {
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.75rem;
        }
        .alert-toggle {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.625rem 0.75rem;
            margin-bottom: 0.375rem;
            border-radius: var(--radius-lg);
            cursor: pointer;
            transition: background-color 0.2s;
            user-select: none;
        }
        .alert-toggle:hover {
            background-color: var(--background);
        }
        .alert-toggle-text {
            font-size: 0.875rem;
            color: var(--text-primary);
            flex: 1;
            margin-right: 0.75rem;
        }
        .alert-toggle input[type="checkbox"] {
            display: none;
        }
        .toggle-switch {
            position: relative;
            width: 44px;
            height: 24px;
            background-color: var(--border);
            border-radius: 12px;
            transition: background-color 0.25s ease;
            flex-shrink: 0;
        }
        .toggle-switch::after {
            content: '';
            position: absolute;
            top: 3px;
            left: 3px;
            width: 18px;
            height: 18px;
            background-color: var(--text-secondary);
            border-radius: 50%;
            transition: transform 0.25s ease, background-color 0.25s ease;
        }
        .alert-toggle input:checked + .toggle-switch {
            background-color: var(--accent-color);
        }
        .alert-toggle input:checked + .toggle-switch::after {
            transform: translateX(20px);
            background-color: #fff;
        }
        .alert-target-field {
            max-height: 0;
            overflow: hidden;
            opacity: 0;
            transition: max-height 0.3s ease, opacity 0.25s ease, margin 0.3s ease;
            margin-top: 0;
            padding: 0 0.75rem;
        }
        .alert-target-field.visible {
            max-height: 80px;
            opacity: 1;
            margin-top: 0.5rem;
        }
        .alert-target-field .form-input {
            margin-top: 0.25rem;
        }

        /* === Price Alert Toast === */
        .price-toast-container {
            position: fixed;
            top: 5rem;
            right: 1.25rem;
            z-index: 10000;
            pointer-events: none;
        }
        .price-toast {
            pointer-events: auto;
            width: 360px;
            max-width: calc(100vw - 2rem);
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-xl);
            box-shadow: var(--shadow-xl);
            padding: 1rem;
            transform: translateX(calc(100% + 30px));
            opacity: 0;
            animation: priceToastIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            position: relative;
            overflow: hidden;
        }
        .price-toast.dismissing {
            animation: priceToastOut 0.3s ease-in forwards;
        }
        @keyframes priceToastIn {
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes priceToastOut {
            to { transform: translateX(calc(100% + 30px)); opacity: 0; }
        }
        .price-toast-close {
            position: absolute;
            top: 0.625rem;
            right: 0.625rem;
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.1rem;
            cursor: pointer;
            padding: 2px 6px;
            border-radius: 4px;
            line-height: 1;
            transition: color 0.15s, background 0.15s;
        }
        .price-toast-close:hover {
            color: var(--text-primary);
            background: var(--background);
        }
        .price-toast-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 0.5rem;
            padding-right: 1.5rem;
        }
        .price-toast-store {
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .price-toast-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: rgba(34, 197, 94, 0.15);
            color: var(--success-color);
            font-size: 0.75rem;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 20px;
        }
        .price-toast-badge.target {
            background: rgba(99, 102, 241, 0.15);
            color: var(--accent-color);
        }
        .price-toast-product {
            font-size: 0.95rem;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 0.375rem;
            line-height: 1.3;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .price-toast-price-row {
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
            margin-bottom: 0.75rem;
        }
        .price-toast-price {
            font-size: 1.3rem;
            font-weight: 700;
            color: var(--success-color);
        }
        .price-toast-installment {
            font-size: 0.75rem;
            color: var(--text-secondary);
        }
        .price-toast-cta {
            display: block;
            width: 100%;
            padding: 0.625rem;
            background: var(--accent-color);
            color: #fff;
            border: none;
            border-radius: var(--radius-lg);
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            transition: filter 0.15s;
        }
        .price-toast-cta:hover {
            filter: brightness(1.1);
        }

        /* Telegram Button */
        .btn-telegram {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 14px;
            border-radius: var(--radius-md);
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            border: 2px solid #229ED9;
            color: #229ED9;
            background: transparent;
            transition: all 0.2s;
        }
        .btn-telegram:hover { background: #229ED9; color: #fff; }
        .btn-telegram.configured { background: #229ED9; color: #fff; border-color: #229ED9; }
        .btn-telegram.configured:hover { background: #1a8abf; border-color: #1a8abf; }
        .btn-telegram svg { flex-shrink: 0; }

        /* Telegram Modal */
        #telegram-modal .form-modal-box {
            max-height: calc(100vh - 2rem);
            max-height: calc(100dvh - 2rem);
            overflow-y: auto;
            display: flex;
            flex-direction: column;
        }
        #telegram-modal .modal-body { padding: 24px; }
        .tg-status {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            border-radius: var(--radius-md);
            font-size: 0.85rem;
            margin-bottom: 18px;
        }
        .tg-status.ok { background: #d1fae5; color: #065f46; }
        .tg-status.not-ok { background: #fee2e2; color: #991b1b; }
        [data-theme="dark"] .tg-status.ok { background: #064e3b; color: #6ee7b7; }
        [data-theme="dark"] .tg-status.not-ok { background: #7f1d1d; color: #fca5a5; }
        .tg-field-row { display: flex; gap: 8px; align-items: flex-end; margin-bottom: 14px; }
        .tg-field-row .form-group { flex: 1; margin-bottom: 0; }
        .tg-guide-toggle {
            background: none;
            border: none;
            color: var(--primary-color);
            font-size: 0.82rem;
            cursor: pointer;
            padding: 0;
            text-decoration: underline;
            margin-bottom: 14px;
            display: block;
        }
        details.tg-guide { margin-bottom: 18px; }
        details.tg-guide summary {
            cursor: pointer;
            color: var(--primary-color);
            font-size: 0.82rem;
            font-weight: 600;
            user-select: none;
            margin-bottom: 8px;
        }
        .tg-guide-steps {
            background: var(--background);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 14px 16px;
            font-size: 0.82rem;
            line-height: 1.7;
            color: var(--text-secondary);
        }
        .tg-guide-steps ol { margin: 0; padding-left: 18px; }
        .tg-guide-steps li { margin-bottom: 6px; }
        .tg-guide-steps code {
            background: var(--border);
            padding: 1px 5px;
            border-radius: 3px;
            font-family: monospace;
            color: var(--text-primary);
        }
        .tg-actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
        .tg-actions .btn { flex: 1; min-width: 110px; }
        .tg-confirm {
            margin-top: 18px;
            padding: 16px;
            border-radius: var(--radius-md);
            border: 1px solid var(--primary);
            background: rgba(99,102,241,0.05);
            animation: tgConfirmIn 0.25s ease;
        }
        @keyframes tgConfirmIn {
            from { opacity: 0; transform: translateY(6px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .tg-confirm-text {
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-primary);
            margin: 0 0 12px;
            text-align: center;
        }
        .tg-confirm-actions { display: flex; gap: 10px; }
        .tg-confirm-actions .btn { flex: 1; }
        .tg-confirm-yes { min-width: 0; }
        .tg-confirm-no { min-width: 0; }
        @media (max-width: 480px) {
            #telegram-modal .form-modal-box {
                padding: 1.25rem;
                margin: 0.5rem;
                max-width: 100%;
                border-radius: var(--radius-lg);
            }
            #telegram-modal .form-modal-title { font-size: 1rem; margin-bottom: 1rem; }
            .tg-field-row { flex-direction: column; align-items: stretch; }
            .tg-field-row button { height: 38px; }
            .tg-guide-steps { padding: 10px 12px; font-size: 0.78rem; line-height: 1.6; }
            .tg-guide-steps ol { padding-left: 14px; }
            .tg-actions .btn { min-width: 0; font-size: 0.8rem; padding: 0.5rem 0.75rem; }
            .tg-confirm-actions { flex-direction: column; }
        }

        /* Theme Switch Pill */
        .theme-switch {
            display: flex;
            background: var(--background);
            border: 1.5px solid var(--border);
            border-radius: 999px;
            padding: 3px;
            gap: 2px;
            flex-shrink: 0;
            transition: border-color 0.2s;
        }
        .theme-switch:hover { border-color: var(--primary-color); }
        .theme-switch-btn {
            width: 2rem;
            height: 2rem;
            border: none;
            border-radius: 50%;
            background: transparent;
            cursor: pointer;
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s, box-shadow 0.2s;
            line-height: 1;
        }
        .theme-switch-btn.active {
            background: var(--surface);
            box-shadow: var(--shadow-sm);
        }
        .theme-switch-btn:not(.active):hover { background: var(--border); }
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Header -->
        <header class="app-header">
            <div class="header-content">
                <a href="#" class="logo" onclick="resetSearch()">
                    <img src="/logo2.png" alt="TechOfertas" class="logo-img">
                </a>


                <div style="display:flex;align-items:center;gap:0.75rem;margin-left:auto">
                    <div class="theme-switch" role="group" aria-label="Tema">
                        <button class="theme-switch-btn" id="theme-btn-light" onclick="setTheme('light')" title="Modo claro">☀️</button>
                        <button class="theme-switch-btn" id="theme-btn-dark"  onclick="setTheme('dark')"  title="Modo escuro">🌙</button>
                    </div>
                </div>
            </div>
        </header>

        <!-- Tab Navigation -->
        <nav class="tab-bar">
            <div class="tab-bar-inner">
                <button class="tab-btn active" id="tab-buscar" onclick="switchTab('buscar')">🔍 Buscar</button>
                <button class="tab-btn" id="tab-watchlist" onclick="switchTab('watchlist')">
                    👁️ Acompanhar
                    <span class="tab-badge" id="watchlist-badge" style="display:none">0</span>
                </button>
            </div>
        </nav>

        <!-- Main Content -->
        <main class="main-content">
            <!-- Search Section -->
            <section class="search-card" id="search-section">
                <div class="search-title">
                    <h1>Encontre as Melhores Ofertas</h1>
                    <p>Compare preços entre as principais lojas de tecnologia</p>
                </div>

                <form class="search-form" id="search-form">
                    <div class="form-group">
                        <label for="produto" class="form-label">Produto</label>
                        <input type="text" id="produto" name="produto" placeholder="Ex: Memoria 16GB" class="form-input" value="" required>
                    </div>

                    <div class="form-group">
                        <label for="valor_minimo" class="form-label">Valor Mínimo</label>
                        <input type="number" id="valor_minimo" name="valor_minimo" placeholder="Deixe em branco se não houver valor mínimo" class="form-input" value="" min="0" step="0.01">
                    </div>
                    <div class="form-group">
                        <label for="valor_maximo" class="form-label">Valor Máximo</label>
                        <input type="number" id="valor_maximo" name="valor_maximo" placeholder="Deixe em branco se não houver valor máximo" class="form-input" value="" min="0" step="0.01">
                    </div>

                    <div class="stores-section">
                        <label class="form-label">Lojas</label>
                        <div class="stores-grid">
                            <div class="store-option">
                                <input type="checkbox" id="kabum" class="store-checkbox" value="kabum" checked>
                                <label for="kabum" class="store-label"><img src="https://www.google.com/s2/favicons?domain=kabum.com.br&sz=32" class="store-label-logo" alt=""> KaBuM!</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="pichau" class="store-checkbox" value="pichau" checked>
                                <label for="pichau" class="store-label"><img src="https://www.google.com/s2/favicons?domain=pichau.com.br&sz=32" class="store-label-logo" alt=""> Pichau</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="terabyte" class="store-checkbox" value="terabyte" checked>
                                <label for="terabyte" class="store-label"><img src="https://www.google.com/s2/favicons?domain=terabyteshop.com.br&sz=32" class="store-label-logo" alt=""> Terabyte</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="mercadolivre" class="store-checkbox" value="mercadolivre" checked>
                                <label for="mercadolivre" class="store-label"><img src="https://www.google.com/s2/favicons?domain=mercadolivre.com.br&sz=32" class="store-label-logo" alt=""> Mercado Livre</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="magalu" class="store-checkbox" value="magalu" checked>
                                <label for="magalu" class="store-label"><img src="https://www.google.com/s2/favicons?domain=magazineluiza.com.br&sz=32" class="store-label-logo" alt=""> Magalu</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="amazon" class="store-checkbox" value="amazon" checked>
                                <label for="amazon" class="store-label"><img src="https://www.google.com/s2/favicons?domain=amazon.com.br&sz=32" class="store-label-logo" alt=""> Amazon</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="shopee" class="store-checkbox" value="shopee" checked>
                                <label for="shopee" class="store-label"><img src="https://www.google.com/s2/favicons?domain=shopee.com.br&sz=32" class="store-label-logo" alt=""> Shopee</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="casasbahia" class="store-checkbox" value="casasbahia" checked>
                                <label for="casasbahia" class="store-label"><img src="https://www.google.com/s2/favicons?domain=casasbahia.com.br&sz=32" class="store-label-logo" alt=""> Casas Bahia</label>
                            </div>
                        </div>
                    </div>

                    <div class="search-actions">
                        <button type="submit" id="btn-buscar" class="btn btn-primary">
                            <span>🔍</span>
                            Buscar Ofertas
                        </button>
                    </div>
                </form>
            </section>

            <!-- Results Section -->
            <section class="results-section" id="results-section">
                <div class="results-header">
                    <div>
                        <h2 class="results-title">Resultados da Busca</h2>
                        <div class="search-query-display" id="search-query-display" style="display:none">
                            <span class="search-query-label">Buscando por</span>
                            <span class="search-query-term" id="search-term"></span>
                        </div>
                        <p class="results-count" id="total-count"></p>
                    </div>
                    <div class="layout-toggle">
                        <span class="layout-toggle-label">Visualização</span>
                        <button id="layout-btn-default" onclick="setLayout('default')" title="Layout em grade de cards">&#9642;&#9642; Grade</button>
                        <button id="layout-btn-compact" onclick="setLayout('compact')" title="Layout compacto em lista">&#9776; Lista</button>
                    </div>
                    <button class="btn btn-outline" onclick="resetSearch()">
                        <span>🔄</span>
                        Nova Busca
                    </button>
                </div>

                <div class="store-results" id="store-results">
                    <!-- Results will be populated here -->
                </div>
            </section>

            <!-- Watchlist Section -->
            <section id="watchlist-section" style="display:none">
                <div class="watchlist-toolbar">
                    <div>
                        <h2 class="watchlist-title">Produtos Acompanhados</h2>
                        <p class="watchlist-subtitle" id="watchlist-subtitle">Carregando...</p>
                        <p id="watchlist-info-text" style="font-size:0.75rem;color:var(--text-secondary);font-style:italic;margin:0.1rem 0 0;display:none">*Todos os produtos serão atualizados a cada 15 minutos</p>
                    </div>
                    <div class="watchlist-actions">
                        <button id="btn-telegram" class="btn-telegram" onclick="showTelegramModal()" title="Configurar notificações no Telegram">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>
                            Telegram
                        </button>
                        <button class="btn btn-outline" onclick="showAddWatchModal()" style="font-size:0.875rem;padding:0.625rem 1rem">+ Adicionar</button>
                        <button class="btn btn-primary" id="btn-update-all" onclick="updateAllWatched()" style="font-size:0.875rem;padding:0.625rem 1rem">↻ Atualizar todos</button>
                    </div>
                </div>
                <div id="watchlist-update-status" class="watchlist-update-status" style="display:none">
                    <div class="status-spinner-small"></div>
                    <span id="watchlist-status-text">Atualizando...</span>
                </div>
                <div id="watchlist-items"></div>
                <div id="watchlist-empty" class="watchlist-empty" style="display:none">
                    <div class="empty-icon">👁️</div>
                    <p style="font-size:1.1rem;font-weight:600;margin-bottom:0.4rem">Nenhum produto acompanhado</p>
                    <p style="font-size:0.875rem;color:var(--text-muted)">Adicione produtos para monitorar preços automaticamente.</p>
                    <button class="btn btn-primary" onclick="showAddWatchModal()" style="margin-top:1.5rem">+ Adicionar produto</button>
                </div>
            </section>
        </main>

        <!-- Footer -->
        <footer class="app-footer">
            <div class="footer-content">
                <p>&copy; 2026 TechOfertas - Desenvolvido por Rafael Trinchão</p>
            </div>
        </footer>
    </div>

    <!-- Add Watch Modal -->
    <div id="add-watch-modal" class="modal-overlay" style="display:none" onclick="closeAddWatchModal()">
        <div class="form-modal-box" onclick="event.stopPropagation()">
            <button class="form-modal-close" onclick="closeAddWatchModal()">✕</button>
            <h3 class="form-modal-title">Acompanhar produto</h3>
            <div class="form-group">
                <label class="form-label">Nome do produto *</label>
                <input type="text" id="watch-nome" class="form-input" placeholder="Ex: RTX 5060 Ti 16GB" autocomplete="off" />
            </div>
            <div style="display:flex;gap:0.75rem;margin-top:1rem">
                <div class="form-group" style="flex:1">
                    <label class="form-label">Preço mínimo (R$)</label>
                    <input type="number" id="watch-valor-min" class="form-input" placeholder="Sem limite" min="0" step="0.01" />
                </div>
                <div class="form-group" style="flex:1">
                    <label class="form-label">Preço máximo (R$)</label>
                    <input type="number" id="watch-valor-max" class="form-input" placeholder="Sem limite" min="0" step="0.01" />
                </div>
            </div>
            <div class="modal-stores" style="margin-top:1rem">
                <label class="form-label" style="display:block;margin-bottom:0.5rem">Lojas</label>
                <div class="stores-grid">
                    <div class="store-option"><input type="checkbox" id="wl-kabum" class="store-checkbox wl-store-checkbox" value="kabum" checked><label for="wl-kabum" class="store-label"><img src="https://www.google.com/s2/favicons?domain=kabum.com.br&sz=32" class="store-label-logo" alt=""> KaBuM!</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-pichau" class="store-checkbox wl-store-checkbox" value="pichau" checked><label for="wl-pichau" class="store-label"><img src="https://www.google.com/s2/favicons?domain=pichau.com.br&sz=32" class="store-label-logo" alt=""> Pichau</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-terabyte" class="store-checkbox wl-store-checkbox" value="terabyte" checked><label for="wl-terabyte" class="store-label"><img src="https://www.google.com/s2/favicons?domain=terabyteshop.com.br&sz=32" class="store-label-logo" alt=""> Terabyte</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-mercadolivre" class="store-checkbox wl-store-checkbox" value="mercadolivre" checked><label for="wl-mercadolivre" class="store-label"><img src="https://www.google.com/s2/favicons?domain=mercadolivre.com.br&sz=32" class="store-label-logo" alt=""> Mercado Livre</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-magalu" class="store-checkbox wl-store-checkbox" value="magalu" checked><label for="wl-magalu" class="store-label"><img src="https://www.google.com/s2/favicons?domain=magazineluiza.com.br&sz=32" class="store-label-logo" alt=""> Magalu</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-amazon" class="store-checkbox wl-store-checkbox" value="amazon" checked><label for="wl-amazon" class="store-label"><img src="https://www.google.com/s2/favicons?domain=amazon.com.br&sz=32" class="store-label-logo" alt=""> Amazon</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-shopee" class="store-checkbox wl-store-checkbox" value="shopee" checked><label for="wl-shopee" class="store-label"><img src="https://www.google.com/s2/favicons?domain=shopee.com.br&sz=32" class="store-label-logo" alt=""> Shopee</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-casasbahia" class="store-checkbox wl-store-checkbox" value="casasbahia" checked><label for="wl-casasbahia" class="store-label"><img src="https://www.google.com/s2/favicons?domain=casasbahia.com.br&sz=32" class="store-label-logo" alt=""> Casas Bahia</label></div>
                </div>
            </div>
            <div class="alert-section">
                <div class="alert-section-title">Alertas de Preço</div>
                <label class="alert-toggle" for="watch-notify-lower">
                    <span class="alert-toggle-text">Notificar quando encontrar preço mais baixo</span>
                    <input type="checkbox" id="watch-notify-lower" checked>
                    <span class="toggle-switch"></span>
                </label>
                <label class="alert-toggle" for="watch-notify-target">
                    <span class="alert-toggle-text">Notificar quando atingir valor alvo</span>
                    <input type="checkbox" id="watch-notify-target">
                    <span class="toggle-switch"></span>
                </label>
                <div class="alert-target-field" id="watch-target-field">
                    <label class="form-label" for="watch-target-price">Valor alvo (R$)</label>
                    <input type="number" id="watch-target-price" class="form-input" placeholder="Ex: 2499.00" min="0" step="0.01">
                </div>
            </div>
            <div style="display:flex;gap:0.75rem;margin-top:1.5rem">
                <button class="btn btn-primary" style="flex:1" onclick="submitAddWatch()">Adicionar</button>
                <button class="btn btn-outline" onclick="closeAddWatchModal()">Cancelar</button>
            </div>
        </div>
    </div>

    <!-- Edit Watch Modal -->
    <div id="edit-watch-modal" class="modal-overlay" style="display:none" onclick="closeEditWatchModal()">
        <div class="form-modal-box" onclick="event.stopPropagation()">
            <button class="form-modal-close" onclick="closeEditWatchModal()">✕</button>
            <h3 class="form-modal-title">Editar produto acompanhado</h3>
            <div class="form-group">
                <label class="form-label">Nome do produto *</label>
                <input type="text" id="edit-watch-nome" class="form-input" placeholder="Ex: RTX 5060 Ti 16GB" autocomplete="off" />
            </div>
            <div style="display:flex;gap:0.75rem;margin-top:1rem">
                <div class="form-group" style="flex:1">
                    <label class="form-label">Preço mínimo (R$)</label>
                    <input type="number" id="edit-watch-valor-min" class="form-input" placeholder="Sem limite" min="0" step="0.01" />
                </div>
                <div class="form-group" style="flex:1">
                    <label class="form-label">Preço máximo (R$)</label>
                    <input type="number" id="edit-watch-valor-max" class="form-input" placeholder="Sem limite" min="0" step="0.01" />
                </div>
            </div>
            <div class="modal-stores" style="margin-top:1rem">
                <label class="form-label" style="display:block;margin-bottom:0.5rem">Lojas</label>
                <div class="stores-grid">
                    <div class="store-option"><input type="checkbox" id="edit-wl-kabum" class="store-checkbox" value="kabum"><label for="edit-wl-kabum" class="store-label"><img src="https://www.google.com/s2/favicons?domain=kabum.com.br&sz=32" class="store-label-logo" alt=""> KaBuM!</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-pichau" class="store-checkbox" value="pichau"><label for="edit-wl-pichau" class="store-label"><img src="https://www.google.com/s2/favicons?domain=pichau.com.br&sz=32" class="store-label-logo" alt=""> Pichau</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-terabyte" class="store-checkbox" value="terabyte"><label for="edit-wl-terabyte" class="store-label"><img src="https://www.google.com/s2/favicons?domain=terabyteshop.com.br&sz=32" class="store-label-logo" alt=""> Terabyte</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-mercadolivre" class="store-checkbox" value="mercadolivre"><label for="edit-wl-mercadolivre" class="store-label"><img src="https://www.google.com/s2/favicons?domain=mercadolivre.com.br&sz=32" class="store-label-logo" alt=""> Mercado Livre</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-magalu" class="store-checkbox" value="magalu"><label for="edit-wl-magalu" class="store-label"><img src="https://www.google.com/s2/favicons?domain=magazineluiza.com.br&sz=32" class="store-label-logo" alt=""> Magalu</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-amazon" class="store-checkbox" value="amazon"><label for="edit-wl-amazon" class="store-label"><img src="https://www.google.com/s2/favicons?domain=amazon.com.br&sz=32" class="store-label-logo" alt=""> Amazon</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-shopee" class="store-checkbox" value="shopee"><label for="edit-wl-shopee" class="store-label"><img src="https://www.google.com/s2/favicons?domain=shopee.com.br&sz=32" class="store-label-logo" alt=""> Shopee</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-casasbahia" class="store-checkbox" value="casasbahia"><label for="edit-wl-casasbahia" class="store-label"><img src="https://www.google.com/s2/favicons?domain=casasbahia.com.br&sz=32" class="store-label-logo" alt=""> Casas Bahia</label></div>
                </div>
            </div>
            <div class="alert-section">
                <div class="alert-section-title">Alertas de Preço</div>
                <label class="alert-toggle" for="edit-watch-notify-lower">
                    <span class="alert-toggle-text">Notificar quando encontrar preço mais baixo</span>
                    <input type="checkbox" id="edit-watch-notify-lower">
                    <span class="toggle-switch"></span>
                </label>
                <label class="alert-toggle" for="edit-watch-notify-target">
                    <span class="alert-toggle-text">Notificar quando atingir valor alvo</span>
                    <input type="checkbox" id="edit-watch-notify-target">
                    <span class="toggle-switch"></span>
                </label>
                <div class="alert-target-field" id="edit-watch-target-field">
                    <label class="form-label" for="edit-watch-target-price">Valor alvo (R$)</label>
                    <input type="number" id="edit-watch-target-price" class="form-input" placeholder="Ex: 2499.00" min="0" step="0.01">
                </div>
            </div>
            <div style="display:flex;gap:0.75rem;margin-top:1.5rem">
                <button class="btn btn-primary" style="flex:1" onclick="submitEditWatch()">Salvar</button>
                <button class="btn btn-outline" onclick="closeEditWatchModal()">Cancelar</button>
            </div>
        </div>
    </div>

    <!-- Telegram Config Modal -->
    <div id="telegram-modal" class="modal-overlay" style="display:none" onclick="closeTelegramModal()">
        <div class="form-modal-box" onclick="event.stopPropagation()" style="max-width:480px">
            <button class="form-modal-close" onclick="closeTelegramModal()">✕</button>
            <h3 class="form-modal-title">Notificações no Telegram</h3>

            <div id="tg-status-bar" class="tg-status not-ok" style="display:none"></div>

            <div class="form-group">
                <label class="form-label">Token do Bot *</label>
                <input type="text" id="tg-token" class="form-input" placeholder="123456789:ABCdef..." autocomplete="off" />
            </div>

            <div class="tg-field-row">
                <div class="form-group">
                    <label class="form-label">Chat ID *</label>
                    <input type="text" id="tg-chat-id" class="form-input" placeholder="Ex: 123456789" autocomplete="off" />
                </div>
                <button class="btn btn-outline" style="height:42px;font-size:0.8rem;white-space:nowrap" onclick="detectChatId()">Detectar</button>
            </div>

            <details class="tg-guide">
                <summary>Como criar meu bot e obter o Token / Chat ID?</summary>
                <div class="tg-guide-steps">
                    <ol>
                        <li>Abra o Telegram e pesquise por <code>@BotFather</code>.</li>
                        <li>Envie o comando <code>/newbot</code> e siga as instruções para dar um nome ao bot.</li>
                        <li>O BotFather vai te enviar o <b>Token</b> (parece com <code>123456789:ABCdef...</code>). Cole-o no campo acima.</li>
                        <li>Abra uma conversa com o seu novo bot e envie qualquer mensagem (ex: <code>oi</code>).</li>
                        <li>Clique em <b>Detectar</b> para preencher o Chat ID automaticamente.</li>
                        <li>Clique em <b>Salvar</b> e depois em <b>Testar</b> para confirmar que está funcionando.</li>
                        <li>A partir de agora, quando o preço de um produto acompanhado cair ou atingir seu alvo, você receberá uma mensagem no Telegram.</li>
                        <li><b>Dica:</b> Você pode pesquisar por <code>@userinfobot</code> no Telegram e enviar <code>/start</code> para ver seu Chat ID diretamente.</li>
                    </ol>
                </div>
            </details>

            <div class="tg-actions">
                <button class="btn btn-outline" id="btn-tg-test" onclick="testTelegram()" style="display:none">Testar</button>
                <button class="btn btn-danger" id="btn-tg-remove" onclick="removeTelegram()" style="display:none;background:#ef4444;border-color:#ef4444;color:#fff">Remover</button>
                <button class="btn btn-primary" onclick="saveTelegram()">Salvar</button>
            </div>

            <div id="tg-confirm-box" class="tg-confirm" style="display:none">
                <p class="tg-confirm-text">Você recebeu a mensagem de teste no Telegram?</p>
                <div class="tg-confirm-actions">
                    <button class="btn btn-primary tg-confirm-yes" onclick="confirmTelegramYes()">Sim, recebi</button>
                    <button class="btn btn-outline tg-confirm-no" onclick="confirmTelegramNo()">Não recebi</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Confirm Watch from Search Modal -->
    <div id="confirm-watch-modal" class="confirm-overlay" style="display:none" onclick="closeWatchConfirmModal()">
        <div class="confirm-box" onclick="event.stopPropagation()">
            <div class="confirm-box-title">Acompanhar preço?</div>
            <div class="confirm-box-body">
                <div id="confirm-watch-nome" style="font-weight:600;color:var(--text-primary);margin-bottom:0.5rem;line-height:1.4"></div>
                <div id="confirm-watch-meta" style="font-size:0.85rem;color:var(--text-secondary)"></div>
            </div>
            <div class="confirm-box-actions">
                <button class="btn btn-outline" onclick="closeWatchConfirmModal()">Cancelar</button>
                <button class="btn btn-primary" onclick="confirmWatchFromSearch()">Acompanhar</button>
            </div>
        </div>
    </div>

    <!-- Confirm Remove Modal -->
    <div id="confirm-remove-modal" class="confirm-overlay" style="display:none" onclick="closeRemoveConfirmModal()">
        <div class="confirm-box" onclick="event.stopPropagation()">
            <div class="confirm-box-title">Parar de acompanhar?</div>
            <div class="confirm-box-body" id="confirm-remove-body">Remover este produto do acompanhamento?</div>
            <div class="confirm-box-actions">
                <button class="btn btn-outline" onclick="closeRemoveConfirmModal()">Cancelar</button>
                <button class="btn btn-primary" style="background:var(--error-color);border-color:var(--error-color)" onclick="confirmRemove()">Remover</button>
            </div>
        </div>
    </div>

    <!-- Image Modal -->
    <div class="image-modal" id="image-modal" onclick="closeModal()">
        <div class="modal-content">
            <button class="modal-close" onclick="closeModal()">&times;</button>
            <img src="" alt="Produto" class="modal-image" id="modal-image">
        </div>
    </div>

    <!-- Scroll to Top Button -->
    <button class="scroll-top" id="scroll-top" onclick="scrollToTop()" aria-label="Voltar ao topo">
        ↑
    </button>

    <script>
        // State Management
        let currentSearch = {
            produto: '',
            valor_minimo: 0,
            valor_maximo: 0,
            stores: {}
        };
        let currentEventSource = null;
        let _lastSearchResults = {}; // accumulates store results during stream

        // ---- session state persistence (F5 resilience) ----
        const SESSION_KEY = 'techofertas_session';
        function sessionSave(patch) {
            try {
                const cur = JSON.parse(localStorage.getItem(SESSION_KEY) || '{}');
                localStorage.setItem(SESSION_KEY, JSON.stringify({...cur, ...patch}));
            } catch(e) {}
        }
        function sessionLoad() {
            try { return JSON.parse(localStorage.getItem(SESSION_KEY) || '{}'); } catch(e) { return {}; }
        }

        // Watch-candidate map: wcId → {nome, preco, link, loja}
        const _wc = {};
        let _wcNext = 0;

        // ── Theme ──────────────────────────────────────────────────────────
        (function() {
            const saved = localStorage.getItem('theme') || 'dark';
            document.documentElement.setAttribute('data-theme', saved);
        })();

        function setTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('theme', theme);
            updateThemeButtons(theme);
        }

        function updateThemeButtons(theme) {
            const btnLight = document.getElementById('theme-btn-light');
            const btnDark  = document.getElementById('theme-btn-dark');
            if (btnLight) btnLight.classList.toggle('active', theme === 'light');
            if (btnDark)  btnDark.classList.toggle('active',  theme === 'dark');
        }
        // ──────────────────────────────────────────────────────────────────

        // DOM Elements
        const searchSection = document.getElementById('search-section');
        const resultsSection = document.getElementById('results-section');
        const searchForm = document.getElementById('search-form');
        const storeResults = document.getElementById('store-results');
        const totalCount = document.getElementById('total-count');
        const searchTermEl = document.getElementById('search-term');
        const searchQueryDisplay = document.getElementById('search-query-display');

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            updateThemeButtons(localStorage.getItem('theme') || 'dark');

            setupEventListeners();
            loadSearchCache();
            setupScrollToTop();
            checkAutoUpdate();
            _restoreSession();
            loadTelegramConfig();
            document.getElementById('watch-nome').addEventListener('keydown', function(e) {
                if (e.key === 'Enter') submitAddWatch();
            });
        });

        function _restoreSession() {
            const s = sessionLoad();
            if (!s || !s.view) return;

            if (s.tab === 'watchlist') {
                // Watchlist já é carregada pelo checkAutoUpdate — só muda a aba
                switchTab('watchlist');
                return;
            }

            if (s.view === 'results' && s.results && s.search) {
                // Restaura parâmetros de busca
                currentSearch.produto = s.search.produto || '';
                currentSearch.valor_minimo = s.search.valor_minimo || 0;
                currentSearch.valor_maximo = s.search.valor_maximo != null ? s.search.valor_maximo : null;
                currentSearch.stores = s.search.stores || {};

                // Preenche os campos do formulário (para o botão Nova Busca funcionar corretamente)
                document.getElementById('produto').value = currentSearch.produto;
                document.getElementById('valor_minimo').value = currentSearch.valor_minimo > 0 ? currentSearch.valor_minimo : '';
                document.getElementById('valor_maximo').value = currentSearch.valor_maximo != null ? currentSearch.valor_maximo : '';
                document.querySelectorAll('#search-form .store-checkbox').forEach(cb => {
                    cb.checked = currentSearch.stores[cb.value] !== false;
                });
                updateStoreSelection();

                // Renderiza resultados salvos
                showResultsSection();
                _updateSearchTerm(currentSearch.produto);
                displayResults(s.results);
                if (s.totalCount) totalCount.textContent = s.totalCount;

                // Marca barra de status como finalizada
                const bar = document.getElementById('search-status-bar');
                const statusText = document.getElementById('status-text');
                if (bar) bar.classList.add('done');
                if (statusText) statusText.textContent = '✅ Busca finalizada!';
            }
        }

        // ---- Layout toggle ----
        let _currentLayout = localStorage.getItem('techofertas_layout') || 'default';
        let _currentResults = null; // dados da última renderização — necessário para re-renderizar ao trocar layout

        function setLayout(type) {
            _currentLayout = type;
            localStorage.setItem('techofertas_layout', type);
            // Atualiza botões
            const btnDef = document.getElementById('layout-btn-default');
            const btnCmp = document.getElementById('layout-btn-compact');
            if (btnDef) btnDef.classList.toggle('active', type === 'default');
            if (btnCmp) btnCmp.classList.toggle('active', type === 'compact');
            // Re-renderiza completamente — os dois layouts têm HTML interno diferente
            if (_currentResults) displayResults(_currentResults);
        }

        function initLayout() {
            const btnDef = document.getElementById('layout-btn-default');
            const btnCmp = document.getElementById('layout-btn-compact');
            if (btnDef) btnDef.classList.toggle('active', _currentLayout === 'default');
            if (btnCmp) btnCmp.classList.toggle('active', _currentLayout === 'compact');
        }

        function createProductCardCompact(product) {
            const wcId = ++_wcNext;
            _wc[wcId] = { nome: product.nome || '', preco: product.preco, link: product.link || '', loja: product.loja || '' };
            const installmentHTML = product.parcelamento ? (() => {
                const p = product.parcelamento;
                const val = p.valor.toLocaleString('pt-BR', {minimumFractionDigits: 2});
                const totalNum = p.parcelas * p.valor;
                const badge = p.sem_juros
                    ? '<span class="badge-sem-juros">sem juros</span>'
                    : '<span class="badge-com-juros">com juros</span>';
                const total = totalNum.toLocaleString('pt-BR', {minimumFractionDigits: 2});
                const melhorCompra = (p.sem_juros && Math.abs(totalNum - product.preco) < 0.02)
                    ? '<div class="compact-melhor-compra" title="O total parcelado é igual ao preço à vista — você não paga nada a mais parcelando!">=&nbsp;vista</div>' : '';
                return `<div class="compact-inst-row"><span class="compact-credit-total">R$ ${total}</span>${badge}</div><div class="compact-inst-row"><span>${p.parcelas}x R$${val}</span>${melhorCompra}</div>`;
            })() : '<span style="color:var(--text-muted)">--</span>';
            const imgHTML = product.imagem
                ? `<img src="${product.imagem}" alt="${product.nome}" class="compact-img" onclick="openImageModal('${product.imagem}')">`
                : '<div class="compact-img-placeholder"></div>';
            return `
                <div class="product-card compact">
                    ${imgHTML}
                    <div class="compact-name" title="${product.nome}">${product.nome}${product.loja ? ` <span class="product-store">${getStoreDisplayName(product.loja)}</span>` : ''}</div>
                    <div class="compact-price-col">
                        <div class="compact-price-label">à vista</div>
                        <div class="compact-price">R$ ${product.preco.toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div>
                    </div>
                    <div class="compact-installment">${installmentHTML}</div>
                    <div class="compact-actions">
                        <button class="btn btn-primary btn-sm" onclick="openProduct('${product.link}')">Ver ↗</button>
                        <button class="btn-watch" onclick='openWatchConfirmModal(${wcId})' title="Acompanhar preço">👁️</button>
                    </div>
                </div>`;
        }

        function setupEventListeners() {
            // Form submission
            searchForm.addEventListener('submit', handleSearch);

            // Store checkboxes
            document.querySelectorAll('.store-checkbox').forEach(checkbox => {
                checkbox.addEventListener('change', updateStoreSelection);
            });

            // Initialize store selection
            updateStoreSelection();
        }

        function updateStoreSelection() {
            // Only select checkboxes inside the main search form, not the watchlist modal
            const checkboxes = document.querySelectorAll('#search-form .store-checkbox');
            checkboxes.forEach(checkbox => {
                currentSearch.stores[checkbox.value] = checkbox.checked;
            });
        }

        function saveSearchCache() {
            const cache = {
                produto: currentSearch.produto,
                valor_minimo: currentSearch.valor_minimo,
                valor_maximo: currentSearch.valor_maximo,
                stores: currentSearch.stores
            };
            localStorage.setItem('techofertas_last_search', JSON.stringify(cache));
        }

        function loadSearchCache() {
            const raw = localStorage.getItem('techofertas_last_search');
            if (!raw) return;
            try {
                const cache = JSON.parse(raw);
                if (cache.produto) document.getElementById('produto').value = cache.produto;
                if (cache.valor_minimo) document.getElementById('valor_minimo').value = cache.valor_minimo;
                if (cache.valor_maximo) document.getElementById('valor_maximo').value = cache.valor_maximo;
                if (cache.stores) {
                    document.querySelectorAll('#search-form .store-checkbox').forEach(cb => {
                        if (cache.stores[cb.value] !== undefined) cb.checked = cache.stores[cb.value];
                    });
                }
                updateStoreSelection();
            } catch (e) {}
        }

        function handleSearch(e) {
            e.preventDefault();

            const formData = new FormData(searchForm);
            currentSearch.produto = formData.get('produto');
            const valorMinimoStr = formData.get('valor_minimo');
            const valorMaximoStr = formData.get('valor_maximo');
            currentSearch.valor_minimo = valorMinimoStr === '' || valorMinimoStr === null ? 0 : parseFloat(valorMinimoStr);
            currentSearch.valor_maximo = valorMaximoStr === '' || valorMaximoStr === null ? null : parseFloat(valorMaximoStr);

            if (!currentSearch.produto) {
                showNotification('Por favor, preencha o campo de produto', 'error');
                return;
            }

            if (!Object.values(currentSearch.stores).some(selected => selected)) {
                showNotification('Selecione pelo menos uma loja', 'error');
                return;
            }

            saveSearchCache();
            performSearch();
        }

        function _setBuscarBtn(enabled) {
            const btn = document.getElementById('btn-buscar');
            if (btn) btn.disabled = !enabled;
        }

        function performSearch() {
            // Cancela busca anterior se houver
            if (currentEventSource) {
                currentEventSource.close();
                currentEventSource = null;
            }
            _setBuscarBtn(false);
            _lastSearchResults = {};
            _melhoresVisibleCount = MELHORES_PAGE_SIZE;
            _melhoresAllProducts = [];
            Object.keys(_storeExpanded).forEach(k => delete _storeExpanded[k]);
            Object.keys(_storeAllProducts).forEach(k => delete _storeAllProducts[k]);

            autoUpdatePaused = true; // pause background auto-updates during manual search
            showResultsSection();

            _updateSearchTerm(currentSearch.produto);

            totalCount.textContent = '';

            const selectedStores = Object.keys(currentSearch.stores).filter(s => currentSearch.stores[s]);

            // Monta status bar + skeleton cards para cada loja selecionada
            storeResults.innerHTML =
                `<div id="search-status-bar" class="search-status-bar">` +
                    `<div class="status-spinner-small"></div>` +
                    `<span id="status-text">Buscando em ${selectedStores.map(getStoreDisplayName).join(', ')}...</span>` +
                `</div>` +
                `<div id="best-offers-container"></div>` +
                selectedStores.map(createSkeletonCard).join('');

            const params = new URLSearchParams({
                produto: currentSearch.produto,
                valor_minimo: currentSearch.valor_minimo || 0,
                valor_maximo: currentSearch.valor_maximo != null ? currentSearch.valor_maximo : '',
                filtros: JSON.stringify(currentSearch.stores),
            });

            currentEventSource = new EventSource(`/buscar_stream?${params}`);

            currentEventSource.onmessage = function(evt) {
                let data;
                try { data = JSON.parse(evt.data); } catch(e) { return; }

                if (data.type === 'error') {
                    showNotification(data.mensagem || 'Erro na busca.', 'error');
                    currentEventSource.close();
                    currentEventSource = null;
                    autoUpdatePaused = false;
                    _setBuscarBtn(true);
                    hideResultsSection();
                    return;
                }

                if (data.type === 'store') {
                    _lastSearchResults[data.store] = data.ofertas; // acumula para persistência
                    // Substitui skeleton pelo card real
                    const skeletonCard = document.getElementById(`store-${data.store}`);
                    const titulo = getStoreDisplayName(data.store);
                    if (skeletonCard) {
                        skeletonCard.outerHTML = createStoreSection(data.store, titulo, data.ofertas, data.store);
                        const content = document.getElementById(`content-${data.store}`);
                        if (content) content.classList.add('expanded');
                    }

                    // Atualiza status bar
                    const statusText = document.getElementById('status-text');
                    if (statusText) {
                        if (data.pending_sites.length > 0) {
                            const restantes = data.pending_sites.map(getStoreDisplayName).join(', ');
                            statusText.textContent = `Buscando em ${restantes}... (${data.done_sites.length} loja(s) concluída(s))`;
                        } else {
                            statusText.textContent = 'Finalizando...';
                        }
                    }

                    // Atualiza melhores ofertas parciais
                    if (data.melhores_parciais && data.melhores_parciais.length > 0) {
                        const container = document.getElementById('best-offers-container');
                        if (container) {
                            container.innerHTML = createStoreSection('melhores', '⭐ Melhores Ofertas', data.melhores_parciais, 'melhores');
                            const content = document.getElementById('content-melhores');
                            if (content) content.classList.add('expanded');
                        }
                    }

                    // Atualiza contagem total
                    let total = 0;
                    document.querySelectorAll('[id^="content-"]').forEach(function(el) {
                        if (el.id !== 'content-melhores') {
                            total += el.querySelectorAll('.product-card').length;
                        }
                    });
                    totalCount.textContent = total > 0
                        ? `${total} produto${total !== 1 ? 's' : ''} encontrado${total !== 1 ? 's' : ''}`
                        : '';
                }

                if (data.type === 'done') {
                    // Atualiza melhores ofertas finais
                    const container = document.getElementById('best-offers-container');
                    if (container && data.melhores_ofertas && data.melhores_ofertas.length > 0) {
                        container.innerHTML = createStoreSection('melhores', '⭐ Melhores Ofertas', data.melhores_ofertas, 'melhores');
                        const content = document.getElementById('content-melhores');
                        if (content) content.classList.add('expanded');
                    }

                    // Marca busca como finalizada
                    const bar = document.getElementById('search-status-bar');
                    const statusText = document.getElementById('status-text');
                    if (bar) bar.classList.add('done');
                    if (statusText) statusText.textContent = '✅ Busca finalizada!';

                    // Guarda resultados em memória para permitir retorno da aba Acompanhar
                    _currentResults = {..._lastSearchResults, melhores_ofertas: data.melhores_ofertas || []};

                    // Persiste estado completo para sobreviver ao F5
                    sessionSave({
                        view: 'results',
                        tab: 'buscar',
                        search: {
                            produto: currentSearch.produto,
                            valor_minimo: currentSearch.valor_minimo,
                            valor_maximo: currentSearch.valor_maximo,
                            stores: {...currentSearch.stores}
                        },
                        results: _currentResults,
                        totalCount: totalCount.textContent
                    });

                    currentEventSource.close();
                    currentEventSource = null;
                    autoUpdatePaused = false;
                    _setBuscarBtn(true);
                }
            };

            currentEventSource.onerror = function() {
                if (currentEventSource) {
                    currentEventSource.close();
                    currentEventSource = null;
                }
                autoUpdatePaused = false;
                _setBuscarBtn(true);
                const bar = document.getElementById('search-status-bar');
                const statusText = document.getElementById('status-text');
                if (bar) { bar.style.borderColor = 'rgb(239 68 68 / 0.4)'; bar.style.background = 'rgb(239 68 68 / 0.05)'; }
                if (statusText) statusText.textContent = '⚠️ Erro na conexão. Tente novamente.';
            };
        }

        function showLoading() {
            storeResults.innerHTML = `
                <div class="loading">
                    <div class="loading-spinner"></div>
                    <span>Buscando ofertas...</span>
                </div>
            `;
        }

        function createSkeletonCard(store) {
            return `
                <div class="store-card skeleton-card" id="store-${store}">
                    <div class="store-header" style="cursor:default">
                        <div class="store-info">
                            <div class="store-icon ${store}">${getStoreLogo(store)}</div>
                            <div class="store-details">
                                <h3>${getStoreDisplayName(store)}</h3>
                                <p class="store-count">Buscando...</p>
                            </div>
                        </div>
                        <div class="status-spinner-small"></div>
                    </div>
                    <div class="store-content expanded">
                        <div class="skeleton-container">
                            <div class="skeleton-item"></div>
                            <div class="skeleton-item"></div>
                            <div class="skeleton-item"></div>
                        </div>
                    </div>
                </div>
            `;
        }

                 function displayResults(data) {
             _currentResults = data; // salva para re-renderizar ao trocar layout
             const selectedStores = Object.keys(currentSearch.stores).filter(store => currentSearch.stores[store]);
             let totalProducts = 0;
             let resultsHTML = '';

             // Add Best Offers section if there are results
             if (data.melhores_ofertas && data.melhores_ofertas.length > 0) {
                 const bestOffers = data.melhores_ofertas;
                 // CORREÇÃO PROBLEMA 1: Não somar produtos das Melhores Ofertas ao total
                 // totalProducts += bestOffers.length; // LINHA REMOVIDA
                 
                 resultsHTML += createStoreSection('melhores', 'Melhores Ofertas', bestOffers, 'melhores');
             }

             // Add individual store sections (ordem fixa para consistência)
             const storeOrder = ['kabum', 'pichau', 'terabyte', 'mercadolivre', 'magalu', 'amazon', 'shopee', 'casasbahia'];
             const orderedStores = storeOrder.filter(s => selectedStores.includes(s))
                 .concat(selectedStores.filter(s => !storeOrder.includes(s)));
             orderedStores.forEach(store => {
                 const storeData = data[store] || [];
                 const validProducts = storeData.filter(product => product.preco !== '-');
                 totalProducts += validProducts.length;

                 const storeName = getStoreDisplayName(store);
                 resultsHTML += createStoreSection(store, storeName, storeData, store);
             });

             // Update results
             storeResults.innerHTML = resultsHTML;
             // CORREÇÃO PROBLEMA 2: Exibir contagem apenas após carregar novos resultados
             totalCount.textContent = `${totalProducts} produto${totalProducts !== 1 ? 's' : ''} encontrado${totalProducts !== 1 ? 's' : ''}`;

             // Setup store toggles
             setupStoreToggles();
         }

        const MELHORES_PAGE_SIZE = 10;
        let _melhoresVisibleCount = MELHORES_PAGE_SIZE;
        let _melhoresAllProducts = []; // full array for pagination

        function _melhoresPaginationHTML(total) {
            const hasMore = total > _melhoresVisibleCount;
            const canLess = _melhoresVisibleCount > MELHORES_PAGE_SIZE;
            if (!hasMore && !canLess) return '';
            const moreBtn = hasMore
                ? `<button class="load-more-btn" onclick="event.stopPropagation();loadMoreMelhores()">Mais resultados</button>`
                : '';
            const lessBtn = canLess
                ? `<button class="load-less-btn" onclick="event.stopPropagation();loadLessMelhores()">Menos</button>`
                : '';
            return `<div class="load-more-wrap">${moreBtn}${lessBtn}</div>`;
        }

        const STORE_PAGE_SIZE = 18;
        const _storeAllProducts = {}; // storeId → full array of valid products
        const _storeExpanded = {};    // storeId → true if showing all products

        function _storePaginationHTML(storeId, total) {
            if (total <= STORE_PAGE_SIZE) return '';
            const expanded = _storeExpanded[storeId];
            const remaining = total - STORE_PAGE_SIZE;
            if (expanded) {
                return `<div class="load-more-wrap" id="store-pagination-${storeId}"><button class="load-more-btn" onclick="event.stopPropagation();toggleStoreProducts('${storeId}')">Menos resultados</button></div>`;
            } else {
                return `<div class="load-more-wrap" id="store-pagination-${storeId}"><button class="load-more-btn" onclick="event.stopPropagation();toggleStoreProducts('${storeId}')">Mais resultados<span class="load-more-count">(+${remaining})</span></button></div>`;
            }
        }

        function toggleStoreProducts(storeId) {
            const all = _storeAllProducts[storeId];
            if (!all || all.length === 0) return;
            const wasExpanded = _storeExpanded[storeId];
            _storeExpanded[storeId] = !wasExpanded;
            const grid = document.getElementById(`grid-${storeId}`);
            if (!grid) return;
            const display = _storeExpanded[storeId] ? all : all.slice(0, STORE_PAGE_SIZE);
            const compactHeader = _currentLayout === 'compact' ? '<div class="compact-header-row"><span></span><span>Produto</span><span>Preço</span><span>Parcelamento</span><span></span></div>' : '';
            grid.innerHTML = compactHeader + display.map(p => _currentLayout === 'compact' ? createProductCardCompact(p) : createProductCard(p)).join('');
            // Update pagination button
            const wrap = document.getElementById(`store-pagination-${storeId}`);
            const newHTML = _storePaginationHTML(storeId, all.length);
            if (wrap) {
                if (newHTML) { wrap.outerHTML = newHTML; } else { wrap.remove(); }
            } else if (newHTML) {
                const content = document.getElementById(`content-${storeId}`);
                if (content) content.insertAdjacentHTML('beforeend', newHTML);
            }
            // Ao colapsar, scrola até o topo do card da loja
            if (wasExpanded) {
                const card = document.getElementById(`store-${storeId}`);
                if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }

        function createStoreSection(storeId, title, products, storeType) {
            const validProducts = products.filter(product => product.preco !== '-');
            const hasProducts = validProducts.length > 0;

            // For "melhores", paginate and cache full array
            const isMelhores = storeId === 'melhores';
            let displayProducts = validProducts;
            let paginationHTML = '';
            if (isMelhores && hasProducts) {
                _melhoresAllProducts = validProducts;
                displayProducts = validProducts.slice(0, _melhoresVisibleCount);
                paginationHTML = _melhoresPaginationHTML(validProducts.length);
            } else if (!isMelhores && hasProducts) {
                // Store sections: limit to STORE_PAGE_SIZE, toggle shows all
                _storeAllProducts[storeId] = validProducts;
                displayProducts = _storeExpanded[storeId] ? validProducts : validProducts.slice(0, STORE_PAGE_SIZE);
                paginationHTML = _storePaginationHTML(storeId, validProducts.length);
            }

            return `
                <div class="store-card" id="store-${storeId}">
                    <div class="store-header" onclick="toggleStore('${storeId}')">
                        <div class="store-info">
                            <div class="store-icon ${storeType}">${getStoreLogo(storeType)}</div>
                            <div class="store-details">
                                <h3>${title}</h3>
                                <p class="store-count">${isMelhores ? '' : validProducts.length + ' produto' + (validProducts.length !== 1 ? 's' : '')}</p>
                            </div>
                        </div>
                        <button class="store-toggle" id="toggle-${storeId}">
                            <span id="icon-${storeId}">▼</span>
                        </button>
                    </div>
                    <div class="store-content" id="content-${storeId}">
                        <div class="products-grid${_currentLayout === 'compact' ? ' compact' : ' grid-cards'}" id="grid-${storeId}">
                            ${_currentLayout === 'compact' ? '<div class="compact-header-row"><span></span><span>Produto</span><span>Preço</span><span>Parcelamento</span><span></span></div>' : ''}
                            ${hasProducts ? displayProducts.map(product => _currentLayout === 'compact' ? createProductCardCompact(product) : createProductCard(product)).join('') : createEmptyState(storeType)}
                        </div>
                        ${paginationHTML}
                    </div>
                </div>
            `;
        }

        function _melhoresUpdatePagination() {
            const wrap = document.querySelector('.load-more-wrap');
            const newHTML = _melhoresPaginationHTML(_melhoresAllProducts.length);
            if (wrap) {
                if (newHTML) { wrap.outerHTML = newHTML; } else { wrap.remove(); }
            } else if (newHTML) {
                const content = document.getElementById('content-melhores');
                if (content) content.insertAdjacentHTML('beforeend', newHTML);
            }
        }

        function loadMoreMelhores() {
            _melhoresVisibleCount += MELHORES_PAGE_SIZE;
            const all = _melhoresAllProducts;
            if (!all || all.length === 0) return;
            const grid = document.getElementById('grid-melhores');
            if (!grid) return;
            const nextBatch = all.slice(_melhoresVisibleCount - MELHORES_PAGE_SIZE, _melhoresVisibleCount);
            const html = nextBatch.map(p => _currentLayout === 'compact' ? createProductCardCompact(p) : createProductCard(p)).join('');
            grid.insertAdjacentHTML('beforeend', html);
            _melhoresUpdatePagination();
        }

        function loadLessMelhores() {
            if (_melhoresVisibleCount <= MELHORES_PAGE_SIZE) return;
            _melhoresVisibleCount -= MELHORES_PAGE_SIZE;
            const all = _melhoresAllProducts;
            if (!all || all.length === 0) return;
            const grid = document.getElementById('grid-melhores');
            if (!grid) return;
            // Re-render grid with fewer items
            const display = all.slice(0, _melhoresVisibleCount);
            const headerHTML = _currentLayout === 'compact' ? '<div class="compact-header-row"><span></span><span>Produto</span><span>Preço</span><span>Parcelamento</span><span></span></div>' : '';
            grid.innerHTML = headerHTML + display.map(p => _currentLayout === 'compact' ? createProductCardCompact(p) : createProductCard(p)).join('');
            _melhoresUpdatePagination();
            // Scroll suave — posiciona um pouco acima do conteúdo para mostrar mais do card
            const storeCard = document.getElementById('store-melhores');
            if (storeCard) {
                const y = storeCard.getBoundingClientRect().top + window.scrollY - 120;
                window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
            }
        }

        function createProductCard(product) {
            const wcId = ++_wcNext;
            _wc[wcId] = {
                nome: product.nome || '',
                preco: product.preco,
                link: product.link || '',
                loja: product.loja || '',
            };
            const installmentHTML = product.parcelamento ? (() => {
                const p = product.parcelamento;
                const val = p.valor.toLocaleString('pt-BR', {minimumFractionDigits: 2});
                const totalNum = p.parcelas * p.valor;
                const badge = p.sem_juros
                    ? '<span class="badge-sem-juros">sem juros</span>'
                    : '<span class="badge-com-juros">com juros</span>';
                const melhorCompra = (p.sem_juros && Math.abs(totalNum - product.preco) < 0.02)
                    ? ' <span class="badge-melhor-compra">mesmo preço à vista</span>'
                    : '';
                const total = totalNum.toLocaleString('pt-BR', {minimumFractionDigits: 2});
                return `<div class="grid-card-installment">💳 R$ ${total} · ${p.parcelas}x R$ ${val} ${badge}${melhorCompra}</div>`;
            })() : '';

            const storeHTML = product.loja
                ? `<div class="grid-card-store">${getStoreDisplayName(product.loja)}</div>`
                : '';

            return `
                <div class="product-card grid-card">
                    <div class="grid-card-image-wrap" onclick="openImageModal('${product.imagem || ''}')">
                        ${product.imagem
                            ? `<img src="${product.imagem}" alt="${escHtml(product.nome)}" loading="lazy">`
                            : `<span style="font-size:2.5rem;opacity:0.3">📦</span>`
                        }
                    </div>
                    <div class="grid-card-body">
                        ${storeHTML}
                        <div class="grid-card-name" title="${escHtml(product.nome)}">${escHtml(product.nome)}</div>
                        <div class="grid-card-price-label">à vista</div>
                        <div class="grid-card-price">R$ ${product.preco.toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div>
                        ${installmentHTML}
                        <div class="grid-card-actions">
                            <button class="btn btn-primary btn-sm" onclick="openProduct('${product.link}')">Ver Produto</button>
                            <button class="btn-watch" onclick='openWatchConfirmModal(${wcId})'>👁️ Acompanhar</button>
                        </div>
                    </div>
                </div>
            `;
        }

        function createEmptyState(storeType) {
            const messages = {
                'kabum': 'Nenhuma oferta encontrada na KaBuM!',
                'pichau': 'Nenhuma oferta encontrada na Pichau',
                'terabyte': 'Nenhuma oferta encontrada na Terabyte',
                'mercadolivre': 'Nenhuma oferta encontrada no Mercado Livre',
                'magalu': 'Nenhuma oferta encontrada no Magalu',
                'amazon': 'Nenhuma oferta encontrada na Amazon',
                'shopee': 'Nenhuma oferta encontrada na Shopee',
                'casasbahia': 'Nenhuma oferta encontrada nas Casas Bahia',
                'melhores': 'Nenhuma oferta encontrada'
            };

            return `
                <div class="empty-state">
                    <div class="empty-icon">🔍</div>
                    <p>${messages[storeType] || 'Nenhuma oferta encontrada'}</p>
                </div>
            `;
        }

        function setupStoreToggles() {
            document.querySelectorAll('.store-content').forEach(content => {
                content.classList.add('expanded');
            });
            initLayout();
        }

        function toggleStore(storeId) {
            const content = document.getElementById(`content-${storeId}`);
            const icon = document.getElementById(`icon-${storeId}`);
            
            if (content.classList.contains('expanded')) {
                content.classList.remove('expanded');
                icon.textContent = '▶';
            } else {
                content.classList.add('expanded');
                icon.textContent = '▼';
            }
        }

        function getStoreDisplayName(store) {
            const names = {
                'kabum': 'KaBuM!',
                'pichau': 'Pichau',
                'terabyte': 'Terabyte',
                'mercadolivre': 'Mercado Livre',
                'Mercadolivre': 'Mercado Livre',
                'magalu': 'Magalu',
                'amazon': 'Amazon',
                'shopee': 'Shopee',
                'casasbahia': 'Casas Bahia',
                'Casasbahia': 'Casas Bahia',
                'melhores': 'Melhores Ofertas'
            };
            return names[store] || store;
        }

        function getStoreIcon(store) { return ''; }

        const SHOPEE_DOMAIN = 'shopee.com.br';

        function getStoreLogo(store) {
            const domains = {
                'kabum': 'kabum.com.br',
                'pichau': 'pichau.com.br',
                'terabyte': 'terabyteshop.com.br',
                'mercadolivre': 'mercadolivre.com.br',
                'Mercadolivre': 'mercadolivre.com.br',
                'magalu': 'magazineluiza.com.br',
                'amazon': 'amazon.com.br',
                'shopee': 'shopee.com.br',
                'casasbahia': 'casasbahia.com.br',
                'Casasbahia': 'casasbahia.com.br',
            };
            const domain = domains[store];
            if (!domain) return '<span style="line-height:1">⭐</span>';
            return `<img src="https://www.google.com/s2/favicons?domain=${domain}&sz=64" class="store-logo-img" alt="${store}">`;
        }

        function resetSearch() {
            if (currentEventSource) {
                currentEventSource.close();
                currentEventSource = null;
            }
            autoUpdatePaused = false;
            _setBuscarBtn(true);
            sessionSave({view: 'form', results: null, tab: 'buscar'});
            _activeTab = 'buscar';
            document.getElementById('watchlist-section').style.display = 'none';
            document.getElementById('tab-buscar').classList.add('active');
            document.getElementById('tab-watchlist').classList.remove('active');
            searchSection.style.display = 'block';
            resultsSection.style.display = 'none';
        }

        

        function showResultsSection() {
            searchSection.style.display = 'none';
            resultsSection.style.display = 'block';
            initLayout();
        }

        function _updateSearchTerm(produto) {
            if (produto) {
                searchTermEl.textContent = produto;
                searchTermEl.title = produto;
                searchQueryDisplay.style.display = 'flex';
            } else {
                searchQueryDisplay.style.display = 'none';
            }
        }

        function hideResultsSection() {
            resultsSection.style.display = 'none';
        }

        function openProduct(url) {
            window.open(url, '_blank');
        }

        function openImageModal(imageUrl) {
            const modal = document.getElementById('image-modal');
            const modalImage = document.getElementById('modal-image');
            
            modalImage.src = imageUrl;
            modal.classList.add('active');
        }

        function closeModal() {
            const modal = document.getElementById('image-modal');
            modal.classList.remove('active');
        }

        function setupScrollToTop() {
            const scrollTopBtn = document.getElementById('scroll-top');
            
            window.addEventListener('scroll', () => {
                if (window.pageYOffset > 300) {
                    scrollTopBtn.classList.add('visible');
                } else {
                    scrollTopBtn.classList.remove('visible');
                }
            });
        }

        function scrollToTop() {
            window.scrollTo({
                top: 0,
                behavior: 'smooth'
            });
        }

        function showNotification(message, type = 'info') {
            const existing = document.querySelector('.toast');
            if (existing) existing.remove();
            const el = document.createElement('div');
            el.className = `toast ${type}`;
            el.textContent = message;
            document.body.appendChild(el);
            setTimeout(() => el && el.remove(), 4000);
        }

        // Close modal on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeModal();
                closeAddWatchModal();
                closeWatchConfirmModal();
                closeRemoveConfirmModal();
            }
        });

        // ================================================================
        // WATCHLIST
        // ================================================================
        let watchlistData = [];
        let watchUpdateSource = null;
        let _activeTab = 'buscar'; // tracks which tab is visible
        const _updatingIds = new Set(); // items currently being fetched (prevents duplicates)
        let _updateAllRunning = false;  // true while updateAllWatched SSE is open
        const _wlUpdateQueue = [];      // fila de ids aguardando atualização individual
        let _wlQueueRunning = false;    // true enquanto a fila estiver processando
        let _wlQueueTotal = 0;         // total de itens adicionados na fila atual (para exibir progresso)

        function _watchlistVisible() {
            return _activeTab === 'watchlist';
        }
        // Pending confirm state
        let _pendingWatchQuery = null;
        let _pendingRemoveId = null;
        let _pendingWatchProduct = null;

        // ---- localStorage result cache ----
        const WL_CACHE_PREFIX = 'wlcache_';
        const WL_CACHE_TTL = 15 * 60 * 1000; // 15 minutes

        function wlCacheSave(itemId, resultados) {
            try {
                localStorage.setItem(WL_CACHE_PREFIX + itemId, JSON.stringify({ ts: Date.now(), resultados }));
            } catch(e) {}
        }
        // Usado pela lógica de staleness — respeita TTL
        function wlCacheLoad(itemId) {
            try {
                const raw = localStorage.getItem(WL_CACHE_PREFIX + itemId);
                if (!raw) return null;
                const d = JSON.parse(raw);
                if (Date.now() - d.ts > WL_CACHE_TTL) { localStorage.removeItem(WL_CACHE_PREFIX + itemId); return null; }
                return d.resultados;
            } catch(e) { return null; }
        }
        // Usado para visualização — nunca expira o cache, apenas lê o que existe
        function wlCacheLoadRaw(itemId) {
            try {
                const raw = localStorage.getItem(WL_CACHE_PREFIX + itemId);
                if (!raw) return null;
                return JSON.parse(raw).resultados || null;
            } catch(e) { return null; }
        }
        function wlCacheClear(itemId) {
            try { localStorage.removeItem(WL_CACHE_PREFIX + itemId); } catch(e) {}
        }

        // Apply update result in-memory for instant UI (no network round-trip)
        function _applyUpdateResult(id, data) {
            const idx = watchlistData.findIndex(i => i.id === id);
            if (idx === -1) return;
            const item = watchlistData[idx];
            if (data.melhor_preco !== undefined) {
                const oldBest = item.melhor_preco;
                item.melhor_preco = data.melhor_preco;
                if (data.melhor_preco && typeof data.melhor_preco.preco === 'number') {
                    const hist = item.historico || [];
                    hist.push({
                        ts: new Date().toISOString(),
                        preco: data.melhor_preco.preco,
                        loja: data.melhor_preco.loja || ''
                    });
                    item.historico = hist.slice(-20);
                }
                // Fire price alerts
                _checkPriceAlerts(item, oldBest, data.melhor_preco, data.trend);
            }
            item.ultima_busca = new Date().toISOString();
            item._stale = false;
        }

        function escHtml(s) {
            return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        function formatTimeSince(isoTs) {
            try {
                const d = new Date(isoTs);
                const secs = Math.floor((Date.now() - d) / 1000);
                if (secs < 60)    return 'Atualizado agora';
                if (secs < 3600)  return `Atualizado há ${Math.floor(secs / 60)} min`;
                if (secs < 86400) return `Atualizado há ${Math.floor(secs / 3600)}h`;
                return `Atualizado há ${Math.floor(secs / 86400)} dia(s)`;
            } catch(e) { return ''; }
        }

        function switchTab(tab) {
            _activeTab = tab;
            sessionSave({tab});
            const isWl = tab === 'watchlist';
            document.getElementById('tab-buscar').classList.toggle('active', !isWl);
            document.getElementById('tab-watchlist').classList.toggle('active', isWl);
            document.getElementById('watchlist-section').style.display = isWl ? 'block' : 'none';
            if (!isWl) {
                if (_currentResults) {
                    searchSection.style.display = 'none';
                    resultsSection.style.display = 'block';
                    initLayout();
                } else {
                    searchSection.style.display = 'block';
                    resultsSection.style.display = 'none';
                }
            } else {
                searchSection.style.display = 'none';
                resultsSection.style.display = 'none';
                // Render immediately from in-memory data (already updated by background timer)
                // then sync fresh data from server
                renderWatchlist();
                loadWatchlist();
            }
        }

        function loadWatchlist() {
            fetch('/watchlist')
                .then(r => r.json())
                .then(data => {
                    watchlistData = data.items || [];
                    updateWatchlistBadge();
                    // Only render DOM if watchlist tab is visible (avoids phantom re-renders)
                    if (_watchlistVisible()) renderWatchlist();
                })
                .catch(() => {});
        }

        function updateWatchlistBadge() {
            const badge = document.getElementById('watchlist-badge');
            if (!badge) return;
            if (watchlistData.length > 0) {
                badge.textContent = watchlistData.length;
                badge.style.display = 'inline';
            } else {
                badge.style.display = 'none';
            }
        }

        function renderWatchlist() {
            const container = document.getElementById('watchlist-items');
            const empty = document.getElementById('watchlist-empty');
            const subtitle = document.getElementById('watchlist-subtitle');
            const infoText = document.getElementById('watchlist-info-text');
            if (!container) return;
            if (watchlistData.length === 0) {
                container.innerHTML = '';
                if (empty) empty.style.display = 'block';
                if (subtitle) subtitle.textContent = 'Nenhum produto cadastrado.';
                if (infoText) infoText.style.display = 'none';
                return;
            }
            if (empty) empty.style.display = 'none';
            if (infoText) infoText.style.display = '';
            const staleCount = watchlistData.filter(i => i._stale).length;
            if (subtitle) {
                subtitle.textContent = staleCount > 0
                    ? `${watchlistData.length} produto(s) · ${staleCount} aguardando atualização`
                    : `${watchlistData.length} produto(s) · todos atualizados`;
            }
            container.innerHTML = watchlistData.map(renderWatchCard).join('');
            initDragDrop();
        }

        function renderWatchCard(item) {
            const best = item.melhor_preco;
            const hist = item.historico || [];
            const prev = hist.length >= 2 ? hist[hist.length - 2] : null;
            const sid = item.id;
            const isLink = !!item.link;

            // Trend badge
            let trendHTML = '';
            if (best && prev) {
                const diff = best.preco - prev.preco;
                if (diff < -0.01) {
                    trendHTML = `<div class="trend-badge trend-down">↓ −R$ ${Math.abs(diff).toLocaleString('pt-BR',{minimumFractionDigits:2})}</div>`;
                } else if (diff > 0.01) {
                    trendHTML = `<div class="trend-badge trend-up">↑ +R$ ${diff.toLocaleString('pt-BR',{minimumFractionDigits:2})}</div>`;
                } else {
                    trendHTML = `<div class="trend-badge trend-same">→ Sem variação</div>`;
                }
            }

            // Price section
            let precoHTML;
            if (best) {
                const lojaLabel = isLink ? '' : `<div class="watch-price-loja">${escHtml(getStoreDisplayName((best.loja||'').toLowerCase()))}</div>`;
                const menorPrecoLabel = isLink ? '' : `<div class="watch-price-label">Menor preço</div>`;
                precoHTML = `
                    ${menorPrecoLabel}
                    <div class="watch-price-value">R$ ${best.preco.toLocaleString('pt-BR',{minimumFractionDigits:2})}</div>
                    ${lojaLabel}
                    ${trendHTML}`;
            } else {
                precoHTML = `<div style="color:var(--text-muted);font-size:0.85rem">${item.ultima_busca ? 'Sem resultados' : 'Não buscado'}</div>`;
            }

            // Meta line
            const ts = item.ultima_busca ? formatTimeSince(item.ultima_busca) : 'Nunca';

            let metaHTML;
            if (isLink) {
                metaHTML = ts;
            } else {
                const lojas = Object.entries(item.lojas||{}).filter(([,v])=>v).map(([k])=>getStoreDisplayName(k)).join(', ');
                metaHTML = `${escHtml(lojas)} · ${ts}`;
            }

            // Name: opens product link for link-based, opens results for query-based
            const nameHTML = isLink
                ? `<div class="watch-item-query clickable" onclick='window.open(${JSON.stringify(item.link)},"_blank","noopener")' title="Abrir produto na loja">${escHtml(item.query || item.link)}</div>`
                : `<div style="display:flex;align-items:center;gap:0.5rem;min-width:0">
                       <div class="watch-item-query clickable" onclick='openWatchResults("${sid}")' title="Ver resultados da busca" style="min-width:0">${escHtml(item.query)}</div>
                       <button class="btn-ver-pesquisa" onclick='openWatchResults("${sid}")'>Ver pesquisa completa</button>
                   </div>`;

            return `
                <div class="watch-card" data-id="${sid}" draggable="true">
                    <div class="watch-item" id="wi-${sid}">
                        <div class="drag-handle" title="Arrastar para reordenar">⠿</div>
                        <div class="watch-item-icon">${isLink ? '🔗' : '💻'}</div>
                        <div class="watch-item-body">
                            ${nameHTML}
                            <div class="watch-item-meta">${metaHTML}</div>
                        </div>
                        <div class="watch-item-price">${precoHTML}</div>
                        <div class="watch-item-actions">
                            <button class="btn-icon" draggable="false" onclick='updateWatchItem("${sid}")' title="Atualizar preço">↻</button>
                            ${!isLink ? `<button class="btn-icon" draggable="false" onclick='openEditWatchModal("${sid}")' title="Editar">✏️</button>` : ''}
                            <button class="btn-icon danger" draggable="false" onclick='openRemoveConfirmModal("${sid}")' title="Remover">✕</button>
                        </div>
                    </div>
                </div>`;
        }

        // Open results for a query-based watch item — ONLY from cache, never triggers search
        function openWatchResults(id) {
            const item = watchlistData.find(i => i.id === id);
            if (!item || item.link) return;
            const cached = wlCacheLoadRaw(id); // ignores TTL — visualização não deve disparar busca
            if (cached) {
                displayWatchResults(item, cached);
            } else {
                showNotification('Nenhum resultado em cache. Clique em ↻ para buscar.', 'info');
            }
        }

        // Display cached results in search view (same rendering as main search)
        function displayWatchResults(item, resultados) {
            currentSearch.produto = item.query;
            currentSearch.valor_minimo = item.valor_minimo || 0;
            currentSearch.valor_maximo = item.valor_maximo || null;
            currentSearch.stores = {};
            Object.keys(resultados).forEach(s => { currentSearch.stores[s] = true; });

            document.getElementById('tab-buscar').classList.add('active');
            document.getElementById('tab-watchlist').classList.remove('active');
            document.getElementById('watchlist-section').style.display = 'none';
            showResultsSection();
            _updateSearchTerm(item.query);

            // Reset pagination so displayResults/createStoreSection handles it
            _melhoresVisibleCount = MELHORES_PAGE_SIZE;
            _melhoresAllProducts = [];
            Object.keys(_storeExpanded).forEach(k => delete _storeExpanded[k]);
            Object.keys(_storeAllProducts).forEach(k => delete _storeAllProducts[k]);

            // Compute melhores from cached data (full list — pagination handled by createStoreSection)
            const todas = Object.values(resultados).flat();
            const validas = todas.filter(o => typeof o.preco === 'number' && o.preco > 0);
            const melhores = validas.sort((a, b) => a.preco - b.preco);
            displayResults({ ...resultados, melhores_ofertas: melhores });

            // Append timestamp if available
            const tsLabel = item.ultima_busca ? formatTimeSince(item.ultima_busca) : '';
            if (tsLabel) {
                const current = totalCount.textContent;
                if (current) totalCount.textContent = current + ' · ' + tsLabel;
            }
        }

        function _wlQueueSpinnerAdd(id) {
            const el = document.getElementById(`wi-${id}`);
            if (!el) return;
            el.classList.add('updating');
            const actEl = el.querySelector('.watch-item-actions');
            if (actEl && !actEl.querySelector('.watch-spinner')) {
                const sp = document.createElement('div'); sp.className = 'watch-spinner'; actEl.prepend(sp);
            }
        }

        function _wlQueueSpinnerRemove(id) {
            const el = document.getElementById(`wi-${id}`);
            if (!el) return;
            el.classList.remove('updating');
            const sp = el.querySelector('.watch-spinner');
            if (sp) sp.remove();
        }

        // Reaplica spinners nos cards após renderWatchlist() apagar o DOM
        function _wlReapplySpinners() {
            _updatingIds.forEach(id => _wlQueueSpinnerAdd(id));
            _wlUpdateQueue.forEach(id => _wlQueueSpinnerAdd(id));
        }

        function _wlQueueStatusShow(text) {
            if (!_watchlistVisible()) return;
            const statusEl = document.getElementById('watchlist-update-status');
            const statusText = document.getElementById('watchlist-status-text');
            if (statusEl) statusEl.style.display = 'flex';
            if (statusText) statusText.textContent = text;
        }

        function _wlQueueStatusHide() {
            // Só esconde se o updateAll não estiver rodando
            if (_updateAllRunning) return;
            const statusEl = document.getElementById('watchlist-update-status');
            if (statusEl) statusEl.style.display = 'none';
        }

        function updateWatchItem(id) {
            // Ignorar se já está na fila ou sendo atualizado
            if (_updatingIds.has(id) || _wlUpdateQueue.includes(id)) return;

            const item = watchlistData.find(i => i.id === id);
            if (!item) return;

            _wlUpdateQueue.push(id);
            _wlQueueTotal++;

            // Spinner imediato no card — mesmo que ainda não começou a processar
            _wlQueueSpinnerAdd(id);

            // Mostrar status bar imediatamente com total na fila
            const pending = _wlUpdateQueue.length + (_wlQueueRunning ? 1 : 0);
            _wlQueueStatusShow(`${pending} produto(s) na fila de atualização...`);

            _processWlQueue();
        }

        function _processWlQueue() {
            // Se já está rodando ou a fila está vazia, não faz nada
            if (_wlQueueRunning || _wlUpdateQueue.length === 0) return;

            const id = _wlUpdateQueue.shift();
            const item = watchlistData.find(i => i.id === id);
            if (!item) {
                // Item sumiu da watchlist — pula para o próximo
                _processWlQueue();
                return;
            }

            _wlQueueRunning = true;
            _updatingIds.add(id);

            // Status bar: mostra qual está sendo atualizado agora e quantos restam
            const done = _wlQueueTotal - _wlUpdateQueue.length - 1; // itens já concluídos
            const current = done + 1;
            _wlQueueStatusShow(`[${current}/${_wlQueueTotal}] Atualizando "${escHtml(item.query || item.nome || id)}"...`);

            _updateWatchSimple(id, item);
        }

        function _updateWatchSimple(id, item) {
            // Spinner já foi adicionado em updateWatchItem; garante que está presente caso
            // renderWatchlist() tenha sido chamado entre o enfileiramento e o início do processamento
            _wlQueueSpinnerAdd(id);

            const src = new EventSource(`/watchlist/update/${id}`);
            src.onmessage = function(evt) {
                let data; try { data = JSON.parse(evt.data); } catch(e) { return; }
                if (data.type === 'done' || data.type === 'error') {
                    src.close();
                    _updatingIds.delete(id);
                    _wlQueueRunning = false;
                    if (data.type === 'error') {
                        showNotification(data.mensagem || 'Erro ao atualizar.', 'error');
                        _wlQueueSpinnerRemove(id);
                    } else {
                        if (data.todos_resultados) wlCacheSave(id, data.todos_resultados);
                        _applyUpdateResult(id, data);
                        renderWatchlist();
                        _wlReapplySpinners();
                    }
                    // Se a fila esvaziou, reseta contadores e esconde status bar
                    if (_wlUpdateQueue.length === 0) {
                        _wlQueueTotal = 0;
                        _wlQueueStatusHide();
                    }
                    loadWatchlist();
                    scheduleNextAutoUpdate();
                    _processWlQueue();
                }
            };
            src.onerror = function() {
                src.close();
                _updatingIds.delete(id);
                _wlQueueRunning = false;
                _wlQueueSpinnerRemove(id);
                if (_wlUpdateQueue.length === 0) {
                    _wlQueueTotal = 0;
                    _wlQueueStatusHide();
                }
                loadWatchlist();
                scheduleNextAutoUpdate();
                _processWlQueue();
            };
        }

        function _updateWatchStream(id, item) {
            // Switch to search tab with streaming UI
            _activeTab = 'buscar';
            document.getElementById('tab-buscar').classList.add('active');
            document.getElementById('tab-watchlist').classList.remove('active');
            document.getElementById('watchlist-section').style.display = 'none';

            currentSearch.produto = item.query;
            currentSearch.valor_minimo = item.valor_minimo || 0;
            currentSearch.valor_maximo = item.valor_maximo || null;
            currentSearch.stores = {};
            Object.entries(item.lojas||{}).forEach(([k,v]) => { currentSearch.stores[k] = !!v; });

            showResultsSection();
            _updateSearchTerm(item.query);
            _melhoresVisibleCount = MELHORES_PAGE_SIZE;
            _melhoresAllProducts = [];

            totalCount.textContent = '';

            const lojas = Object.entries(item.lojas||{}).filter(([,v])=>v).map(([k])=>k);
            storeResults.innerHTML =
                `<div id="search-status-bar" class="search-status-bar">` +
                    `<div class="status-spinner-small"></div>` +
                    `<span id="status-text">Atualizando "${escHtml(item.query)}"...</span>` +
                `</div>` +
                `<div id="best-offers-container"></div>` +
                lojas.map(createSkeletonCard).join('');

            watchUpdateSource = new EventSource(`/watchlist/update/${id}`);
            watchUpdateSource.onmessage = function(evt) {
                let data; try { data = JSON.parse(evt.data); } catch(e) { return; }

                if (data.type === 'store') {
                    const skCard = document.getElementById(`store-${data.store}`);
                    if (skCard) {
                        skCard.outerHTML = createStoreSection(data.store, getStoreDisplayName(data.store), data.ofertas, data.store);
                        const c = document.getElementById(`content-${data.store}`);
                        if (c) c.classList.add('expanded');
                    }
                    const st = document.getElementById('status-text');
                    if (st) {
                        st.textContent = data.pending_sites && data.pending_sites.length > 0
                            ? `Buscando em ${data.pending_sites.map(getStoreDisplayName).join(', ')}... (${data.done_sites.length} concluída(s))`
                            : 'Finalizando...';
                    }
                    if (data.melhores_parciais && data.melhores_parciais.length > 0) {
                        const bc = document.getElementById('best-offers-container');
                        if (bc) { bc.innerHTML = createStoreSection('melhores', '⭐ Melhores Ofertas', data.melhores_parciais, 'melhores'); const c = document.getElementById('content-melhores'); if (c) c.classList.add('expanded'); }
                    }
                }

                if (data.type === 'done') {
                    watchUpdateSource.close(); watchUpdateSource = null;
                    _updatingIds.delete(id);
                    if (data.todos_resultados) wlCacheSave(id, data.todos_resultados);
                    _applyUpdateResult(id, data);
                    updateWatchlistBadge();
                    const bar = document.getElementById('search-status-bar');
                    const st = document.getElementById('status-text');
                    if (bar) bar.classList.add('done');
                    if (st) st.textContent = '✅ Atualização concluída!';
                    if (data.melhores_ofertas && data.melhores_ofertas.length > 0) {
                        if (!_currentResults) _currentResults = {};
                        _currentResults.melhores_ofertas = data.melhores_ofertas;
                        const bc = document.getElementById('best-offers-container');
                        if (bc) { bc.innerHTML = createStoreSection('melhores', '⭐ Melhores Ofertas', data.melhores_ofertas, 'melhores'); const c = document.getElementById('content-melhores'); if (c) c.classList.add('expanded'); }
                    }
                    let total = 0;
                    document.querySelectorAll('[id^="content-"]').forEach(el => { if (el.id !== 'content-melhores') total += el.querySelectorAll('.product-card').length; });
                    totalCount.textContent = total > 0 ? `${total} produto${total !== 1 ? 's' : ''} encontrado${total !== 1 ? 's' : ''}` : '';
                    fetch('/watchlist').then(r => r.json()).then(d => { watchlistData = d.items || []; updateWatchlistBadge(); });
                    scheduleNextAutoUpdate();
                }

                if (data.type === 'error') {
                    watchUpdateSource.close(); watchUpdateSource = null;
                    _updatingIds.delete(id);
                    showNotification(data.mensagem || 'Erro ao atualizar.', 'error');
                }
            };
            watchUpdateSource.onerror = function() {
                if (watchUpdateSource) { watchUpdateSource.close(); watchUpdateSource = null; }
                _updatingIds.delete(id);
                const st = document.getElementById('status-text');
                if (st) st.textContent = '⚠️ Erro na conexão.';
            };
        }

        function updateAllWatched(force) {
            // force=true → update every item; force=false/undefined → only stale
            if (typeof force === 'undefined') force = true;
            // Não iniciar se updateAll já está rodando
            if (_updateAllRunning) return;
            // Se tem busca por item específico em curso, aborta (updateAll tem prioridade menor)
            if (watchUpdateSource) { watchUpdateSource.close(); watchUpdateSource = null; }
            _updateAllRunning = true;

            const statusEl = document.getElementById('watchlist-update-status');
            const statusText = document.getElementById('watchlist-status-text');
            const btn = document.getElementById('btn-update-all');
            // Show status bar only if user is currently on the watchlist tab
            if (_watchlistVisible()) {
                if (statusEl) statusEl.style.display = 'flex';
                if (statusText) statusText.textContent = 'Iniciando fila de atualização...';
                if (btn) btn.disabled = true;
            }

            const url = force ? '/watchlist/update_all?force=1' : '/watchlist/update_all';
            watchUpdateSource = new EventSource(url);

            watchUpdateSource.onmessage = function(evt) {
                let data; try { data = JSON.parse(evt.data); } catch(e) { return; }

                if (data.type === 'start') {
                    if (_watchlistVisible() && statusText) statusText.textContent = `Fila iniciada — ${data.total} produto(s) na fila...`;
                }
                if (data.type === 'progress') {
                    if (_watchlistVisible() && statusText) statusText.textContent = `[${data.idx + 1}/${data.total}] Atualizando "${data.query}"...`;
                }
                if (data.type === 'result') {
                    if (data.todos_resultados && data.id) wlCacheSave(data.id, data.todos_resultados);
                    if (data.id) _applyUpdateResult(data.id, data); // instant in-memory
                    if (_watchlistVisible()) renderWatchlist(); // re-render only if tab is visible
                }
                if (data.type === 'done') {
                    watchUpdateSource.close(); watchUpdateSource = null;
                    _updateAllRunning = false;
                    if (statusEl) statusEl.style.display = 'none';
                    if (btn) btn.disabled = false;
                    updateWatchlistBadge();
                    if (_watchlistVisible()) renderWatchlist();
                    loadWatchlist();
                    scheduleNextAutoUpdate();
                }
            };
            watchUpdateSource.onerror = function() {
                if (watchUpdateSource) { watchUpdateSource.close(); watchUpdateSource = null; }
                _updateAllRunning = false;
                if (statusEl) statusEl.style.display = 'none';
                if (btn) btn.disabled = false;
                loadWatchlist();
                scheduleNextAutoUpdate();
            };
        }

        // --- Confirm remove modal ---
        function openRemoveConfirmModal(id) {
            const item = watchlistData.find(i => i.id === id);
            _pendingRemoveId = id;
            const bodyEl = document.getElementById('confirm-remove-body');
            if (bodyEl) bodyEl.textContent = item ? `Parar de acompanhar "${item.query}"?` : 'Remover este produto do acompanhamento?';
            document.getElementById('confirm-remove-modal').style.display = 'flex';
        }
        function closeRemoveConfirmModal() {
            document.getElementById('confirm-remove-modal').style.display = 'none';
            _pendingRemoveId = null;
        }
        function confirmRemove() {
            const id = _pendingRemoveId;
            closeRemoveConfirmModal();
            if (!id) return;
            wlCacheClear(id);
            // Optimistic UI update — remove immediately from view
            watchlistData = watchlistData.filter(i => i.id !== id);
            updateWatchlistBadge();
            renderWatchlist();
            fetch(`/watchlist/${id}`, {method: 'DELETE'})
                .then(r => r.json())
                .then(() => { showNotification('Produto removido.', 'info'); })
                .catch(() => { loadWatchlist(); showNotification('Erro ao remover.', 'error'); });
        }

        // --- Confirm watch from search modal ---
        function openWatchConfirmModal(wcId) {
            const p = _wc[wcId];
            if (!p) return;
            _pendingWatchProduct = p;
            const nomeEl = document.getElementById('confirm-watch-nome');
            const metaEl = document.getElementById('confirm-watch-meta');
            if (nomeEl) nomeEl.textContent = p.nome || 'Produto';
            if (metaEl) {
                const priceStr = typeof p.preco === 'number'
                    ? `R$ ${p.preco.toLocaleString('pt-BR', {minimumFractionDigits: 2})}`
                    : '';
                const store = p.loja ? getStoreDisplayName(p.loja) : '';
                metaEl.textContent = [store, priceStr].filter(Boolean).join(' · ');
            }
            document.getElementById('confirm-watch-modal').style.display = 'flex';
        }
        function closeWatchConfirmModal() {
            document.getElementById('confirm-watch-modal').style.display = 'none';
            _pendingWatchProduct = null;
        }
        function confirmWatchFromSearch() {
            const p = _pendingWatchProduct;
            closeWatchConfirmModal();
            if (!p) return;
            fetch('/watchlist', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    query: p.nome || p.link,
                    link: p.link,
                    nome: p.nome,
                    loja: p.loja,
                }),
            })
            .then(r => r.json())
            .then(data => {
                if (data.erro) { showNotification(data.erro, 'error'); return; }
                if (data.duplicate) {
                    showNotification(`Este produto já está sendo acompanhado.`, 'info');
                } else {
                    showNotification(`"${p.nome || 'Produto'}" adicionado ao acompanhamento!`, 'info');
                    const newId = data.item && data.item.id;
                    if (newId) {
                        fetch('/watchlist').then(r => r.json()).then(d => {
                            watchlistData = d.items || [];
                            updateWatchlistBadge();
                            updateWatchItem(newId);
                        });
                        return;
                    }
                }
                fetch('/watchlist').then(r => r.json()).then(d => {
                    watchlistData = d.items || [];
                    updateWatchlistBadge();
                });
            })
            .catch(() => showNotification('Erro ao adicionar.', 'error'));
        }

        // ================================================================
        // PRICE TOAST NOTIFICATION SYSTEM
        // ================================================================
        const PriceToast = (() => {
            let container = null;
            let currentToast = null;

            function getContainer() {
                if (!container) {
                    container = document.createElement('div');
                    container.className = 'price-toast-container';
                    document.body.appendChild(container);
                }
                return container;
            }

            function fmtPrice(v) {
                return parseFloat(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            }

            function dismiss(el) {
                if (el.dataset.dismissed) return;
                el.dataset.dismissed = '1';
                if (currentToast === el) currentToast = null;
                el.classList.add('dismissing');
                el.addEventListener('animationend', () => el.remove(), { once: true });
            }

            function show(data) {
                // Close previous toast before showing new one
                if (currentToast) dismiss(currentToast);

                const toast = document.createElement('div');
                toast.className = 'price-toast';

                const badgeClass = data.type === 'target' ? 'price-toast-badge target' : 'price-toast-badge';
                const badgeText = data.type === 'target'
                    ? 'Valor alvo atingido!'
                    : `&#9660; ${fmtPrice(data.savings)} mais barato`;

                toast.innerHTML = `
                    <button class="price-toast-close" aria-label="Fechar">&times;</button>
                    <div class="price-toast-header">
                        <span class="price-toast-store">${escHtml(data.store)}</span>
                        <span class="${badgeClass}">
                            ${badgeText}
                        </span>
                    </div>
                    <div class="price-toast-product">${escHtml(data.product)}</div>
                    <div class="price-toast-price-row">
                        <span class="price-toast-price">${fmtPrice(data.price)}</span>
                        ${data.installment ? `<span class="price-toast-installment">${escHtml(data.installment)}</span>` : ''}
                    </div>
                    ${data.url ? `<a href="${escHtml(data.url)}" target="_blank" rel="noopener" class="price-toast-cta">Ver oferta</a>` : ''}
                `;

                toast.querySelector('.price-toast-close').addEventListener('click', () => dismiss(toast));

                currentToast = toast;
                getContainer().appendChild(toast);
            }

            return { show };
        })();

        // ================================================================
        // BROWSER TAB ALERT
        // ================================================================
        const TabAlert = (() => {
            let intervalId = null;
            let originalTitle = document.title;
            let isOriginal = true;

            function start(message) {
                if (intervalId) return;
                originalTitle = document.title;
                message = message || String.fromCodePoint(0x1F514) + ' Queda de preco!';
                intervalId = setInterval(() => {
                    document.title = isOriginal ? message : originalTitle;
                    isOriginal = !isOriginal;
                }, 1000);
                window.addEventListener('focus', stop, { once: true });
            }

            function stop() {
                if (!intervalId) return;
                clearInterval(intervalId);
                intervalId = null;
                document.title = originalTitle;
                isOriginal = true;
            }

            return { start, stop };
        })();

        // Track which items already triggered target notification this session
        const _notifiedTargets = new Set();

        // ================================================================
        // OS PUSH NOTIFICATION (Web Notifications API)
        // ================================================================
        const OSNotify = (() => {
            let _permissionRequested = false;

            function requestPermission() {
                if (!('Notification' in window)) return;
                if (Notification.permission === 'granted' || Notification.permission === 'denied') return;
                if (_permissionRequested) return;
                _permissionRequested = true;
                Notification.requestPermission();
            }

            function show(data) {
                if (!('Notification' in window) || Notification.permission !== 'granted') return;

                const fmtPrice = v => parseFloat(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

                let title, body;
                if (data.type === 'target') {
                    title = 'TechOfertas — Valor alvo atingido!';
                    body = `${data.product}\n${fmtPrice(data.price)} na ${data.store}`;
                } else {
                    title = `TechOfertas — Queda de preco! ${fmtPrice(data.savings)} mais barato`;
                    body = `${data.product}\n${fmtPrice(data.price)} na ${data.store}`;
                }

                const n = new Notification(title, {
                    body,
                    icon: '/favicon.ico',
                    tag: 'techofertas-price-alert',  // replaces previous notification of same tag
                    renotify: true,
                });

                if (data.url) {
                    n.onclick = () => { window.open(data.url, '_blank', 'noopener'); n.close(); };
                }
            }

            return { requestPermission, show };
        })();

        function _fmtInstallment(p) {
            if (!p || !p.parcelas) return null;
            return `${p.parcelas}x R$ ${p.valor.toLocaleString('pt-BR', {minimumFractionDigits: 2})}${p.sem_juros ? ' sem juros' : ''}`;
        }

        // Check and fire price alerts after an update
        function _checkPriceAlerts(item, oldBest, newBest, trend) {
            if (!newBest || typeof newBest.preco !== 'number') return;
            if (item.notificar_preco_baixo === false && !item.notificar_valor_alvo) return;

            // 1. No previous price yet — skip, wait for next update to compare
            if (!oldBest) return;

            // 2. Price dropped
            if (item.notificar_preco_baixo !== false && trend === 'down' && oldBest) {
                const savings = oldBest.preco - newBest.preco;
                if (savings > 0.01) {
                    const d = {
                        product: newBest.nome || item.query,
                        price: newBest.preco,
                        installment: _fmtInstallment(newBest.parcelamento),
                        store: newBest.loja || '',
                        savings: savings,
                        url: newBest.link || '',
                        type: 'drop'
                    };
                    PriceToast.show(d);
                    OSNotify.show(d);
                    TabAlert.start();
                }
            }

            // 3. Target price reached
            const target = item.notificar_valor_alvo;
            if (target && newBest.preco <= target && !_notifiedTargets.has(item.id)) {
                _notifiedTargets.add(item.id);
                const d = {
                    product: newBest.nome || item.query,
                    price: newBest.preco,
                    installment: _fmtInstallment(newBest.parcelamento),
                    store: newBest.loja || '',
                    savings: target - newBest.preco,
                    url: newBest.link || '',
                    type: 'target'
                };
                PriceToast.show(d);
                OSNotify.show(d);
                TabAlert.start();
            }
        }

        // ================================================================
        // ALERT TOGGLE HELPERS
        // ================================================================
        function initAlertToggles(prefix) {
            const cbLower  = document.getElementById(prefix + 'notify-lower');
            const cbTarget = document.getElementById(prefix + 'notify-target');
            const field    = document.getElementById(prefix + 'target-field');
            if (!cbLower || !cbTarget || !field) return;

            const syncField = () => field.classList.toggle('visible', cbTarget.checked);

            // Use onchange (assignment) to avoid listener accumulation on repeated modal opens
            cbLower.onchange = () => {
                if (cbLower.checked) {
                    cbTarget.checked = false;
                    syncField();
                }
            };
            cbTarget.onchange = () => {
                if (cbTarget.checked) {
                    cbLower.checked = false;
                }
                syncField();
            };

            syncField();
        }

        function showAddWatchModal() {
            OSNotify.requestPermission();
            document.getElementById('add-watch-modal').style.display = 'flex';
            // Reset alert toggles to defaults
            document.getElementById('watch-notify-lower').checked = true;
            document.getElementById('watch-notify-target').checked = false;
            document.getElementById('watch-target-price').value = '';
            initAlertToggles('watch-');
            setTimeout(() => document.getElementById('watch-nome').focus(), 50);
        }

        function closeAddWatchModal() {
            document.getElementById('add-watch-modal').style.display = 'none';
            document.getElementById('watch-nome').value = '';
            document.getElementById('watch-valor-min').value = '';
            document.getElementById('watch-valor-max').value = '';
            document.getElementById('watch-notify-lower').checked = true;
            document.getElementById('watch-notify-target').checked = false;
            document.getElementById('watch-target-price').value = '';
            const f = document.getElementById('watch-target-field');
            if (f) f.classList.remove('visible');
        }

        let _editingWatchId = null;

        function openEditWatchModal(id) {
            const item = watchlistData.find(i => i.id === id);
            if (!item) return;
            _editingWatchId = id;
            document.getElementById('edit-watch-nome').value = item.query || '';
            document.getElementById('edit-watch-valor-min').value = item.valor_minimo > 0 ? item.valor_minimo : '';
            document.getElementById('edit-watch-valor-max').value = item.valor_maximo != null ? item.valor_maximo : '';
            // Priority: if a numeric target price is saved → show target; otherwise → show lower
            const hasTarget = typeof item.notificar_valor_alvo === 'number' && item.notificar_valor_alvo > 0;
            document.getElementById('edit-watch-notify-lower').checked = !hasTarget;
            document.getElementById('edit-watch-notify-target').checked = hasTarget;
            document.getElementById('edit-watch-target-price').value = hasTarget ? item.notificar_valor_alvo : '';
            const lojas = item.lojas || {};
            ['kabum','pichau','terabyte','mercadolivre','magalu','amazon','shopee','casasbahia'].forEach(s => {
                const cb = document.getElementById(`edit-wl-${s}`);
                if (cb) cb.checked = lojas[s] !== false;
            });
            initAlertToggles('edit-watch-');
            document.getElementById('edit-watch-modal').style.display = 'flex';
            setTimeout(() => document.getElementById('edit-watch-nome').focus(), 50);
        }

        function closeEditWatchModal() {
            document.getElementById('edit-watch-modal').style.display = 'none';
            _editingWatchId = null;
        }

        function submitEditWatch() {
            const nome = document.getElementById('edit-watch-nome').value.trim();
            if (!nome) { showNotification('Informe o nome do produto.', 'error'); return; }
            const valorMin = document.getElementById('edit-watch-valor-min').value;
            const valorMax = document.getElementById('edit-watch-valor-max').value;
            const notifyLower = document.getElementById('edit-watch-notify-lower').checked;
            const notifyTarget = document.getElementById('edit-watch-notify-target').checked;
            const targetPrice = document.getElementById('edit-watch-target-price').value;
            const lojas = {};
            ['kabum','pichau','terabyte','mercadolivre','magalu','amazon','shopee','casasbahia'].forEach(s => {
                lojas[s] = document.getElementById(`edit-wl-${s}`).checked;
            });
            if (!Object.values(lojas).some(v => v)) { showNotification('Selecione pelo menos uma loja.', 'error'); return; }
            fetch(`/watchlist/${_editingWatchId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    query: nome,
                    valor_minimo: valorMin === '' ? 0 : parseFloat(valorMin),
                    valor_maximo: valorMax === '' ? null : parseFloat(valorMax),
                    lojas,
                    notificar_preco_baixo: notifyLower,
                    notificar_valor_alvo: notifyTarget && targetPrice ? parseFloat(targetPrice) : null
                })
            })
            .then(r => r.json())
            .then(d => {
                if (d.ok) {
                    const idx = watchlistData.findIndex(i => i.id === _editingWatchId);
                    if (idx !== -1) watchlistData[idx] = d.item;
                    closeEditWatchModal();
                    renderWatchlist();
                    showNotification('Produto atualizado!', 'success');
                } else {
                    showNotification(d.erro || 'Erro ao salvar.', 'error');
                }
            })
            .catch(() => showNotification('Erro ao salvar.', 'error'));
        }

        function submitAddWatch() {
            const query = document.getElementById('watch-nome').value.trim();
            if (!query) { showNotification('Informe o nome do produto.', 'error'); return; }
            const lojas = {};
            ['kabum','pichau','terabyte','mercadolivre','magalu','amazon','shopee','casasbahia'].forEach(s => {
                const cb = document.getElementById(`wl-${s}`);
                lojas[s] = cb ? cb.checked : true;
            });
            const vminRaw = document.getElementById('watch-valor-min').value;
            const vmRaw = document.getElementById('watch-valor-max').value;
            const notifyLower = document.getElementById('watch-notify-lower').checked;
            const notifyTarget = document.getElementById('watch-notify-target').checked;
            const targetPrice = document.getElementById('watch-target-price').value;
            fetch('/watchlist', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    query, lojas,
                    valor_minimo: vminRaw ? parseFloat(vminRaw) : 0,
                    valor_maximo: vmRaw ? parseFloat(vmRaw) : null,
                    notificar_preco_baixo: notifyLower,
                    notificar_valor_alvo: notifyTarget && targetPrice ? parseFloat(targetPrice) : null
                }),
            })
            .then(r => r.json())
            .then(data => {
                if (data.erro) { showNotification(data.erro, 'error'); return; }
                closeAddWatchModal();
                if (data.duplicate) {
                    showNotification(`"${query}" já está na lista.`, 'info');
                } else {
                    showNotification(`"${query}" adicionado! Buscando preços...`, 'info');
                    const newId = data.item && data.item.id;
                    if (newId) {
                        fetch('/watchlist').then(r => r.json()).then(d => {
                            watchlistData = d.items || [];
                            updateWatchlistBadge();
                            renderWatchlist();
                            switchTab('watchlist');
                            setTimeout(() => updateWatchItem(newId), 200);
                        });
                        return;
                    }
                }
                loadWatchlist();
            })
            .catch(() => showNotification('Erro ao adicionar.', 'error'));
        }

        // ================================================================
        // DRAG-AND-DROP REORDER
        // ================================================================
        let _dragSrcId = null;
        let _dragFromHandle = false;

        function initDragDrop() {
            const container = document.getElementById('watchlist-items');
            if (!container) return;
            container.querySelectorAll('.watch-card').forEach(card => {
                // Track whether the drag started from the handle
                const handle = card.querySelector('.drag-handle');
                if (handle) {
                    handle.addEventListener('mousedown', function() { _dragFromHandle = true; });
                }
                card.addEventListener('dragstart', _onDragStart);
                card.addEventListener('dragend',   _onDragEnd);
                card.addEventListener('dragover',  _onDragOver);
                card.addEventListener('dragleave', _onDragLeave);
                card.addEventListener('drop',      _onDrop);
            });
        }

        function _onDragStart(e) {
            if (!_dragFromHandle) { e.preventDefault(); _dragFromHandle = false; return; }
            _dragFromHandle = false;
            _dragSrcId = this.dataset.id;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', _dragSrcId);
            requestAnimationFrame(() => this.classList.add('dragging'));
        }

        function _onDragEnd(e) {
            _dragFromHandle = false;
            this.classList.remove('dragging');
            document.querySelectorAll('.watch-card').forEach(c => {
                c.classList.remove('drag-over');
            });
            _dragSrcId = null;
        }

        function _onDragOver(e) {
            if (!_dragSrcId) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            document.querySelectorAll('.watch-card').forEach(c => c.classList.remove('drag-over'));
            if (this.dataset.id !== _dragSrcId) this.classList.add('drag-over');
        }

        function _onDragLeave(e) {
            if (!e.relatedTarget || !this.contains(e.relatedTarget)) {
                this.classList.remove('drag-over');
            }
        }

        function _onDrop(e) {
            e.preventDefault();
            this.classList.remove('drag-over');
            const targetId = this.dataset.id;
            if (!_dragSrcId || _dragSrcId === targetId) return;

            const srcIdx = watchlistData.findIndex(i => i.id === _dragSrcId);
            const tgtIdx = watchlistData.findIndex(i => i.id === targetId);
            if (srcIdx === -1 || tgtIdx === -1) return;

            const [moved] = watchlistData.splice(srcIdx, 1);
            watchlistData.splice(tgtIdx, 0, moved);

            renderWatchlist();

            fetch('/watchlist/reorder', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ ids: watchlistData.map(i => i.id) })
            }).catch(() => {});
        }

        let autoUpdatePaused = false;
        let _autoUpdateTimer = null;
        const STALE_MS = 15 * 60 * 1000; // 15 minutes

        // Schedule next auto-update at the exact moment the earliest item becomes stale
        function scheduleNextAutoUpdate() {
            if (_autoUpdateTimer) { clearTimeout(_autoUpdateTimer); _autoUpdateTimer = null; }
            if (watchlistData.length === 0) return;
            const now = Date.now();
            let minDelay = Infinity;
            for (const item of watchlistData) {
                if (!item.ultima_busca) { minDelay = 0; break; }
                try {
                    const age = now - new Date(item.ultima_busca).getTime();
                    const remaining = STALE_MS - age;
                    if (remaining <= 0) { minDelay = 0; break; }
                    if (remaining < minDelay) minDelay = remaining;
                } catch(e) {}
            }
            if (minDelay === Infinity) return; // no items with timestamps yet
            // Add 2s buffer so backend staleness is guaranteed
            const delay = Math.max(2000, minDelay + 2000);
            _autoUpdateTimer = setTimeout(function() {
                _autoUpdateTimer = null;
                if (autoUpdatePaused || watchUpdateSource || _updateAllRunning || _updatingIds.size > 0 || _wlUpdateQueue.length > 0) {
                    // Retry in 10s if paused or any update already in progress
                    _autoUpdateTimer = setTimeout(scheduleNextAutoUpdate, 10000);
                    return;
                }
                // Refresh from server to get accurate stale flags, then update
                fetch('/watchlist')
                    .then(r => r.json())
                    .then(d => {
                        watchlistData = d.items || [];
                        updateWatchlistBadge();
                        // Only re-render watchlist DOM if the tab is currently visible
                        if (_watchlistVisible()) renderWatchlist();
                        const stale = watchlistData.filter(i => i._stale);
                        if (stale.length > 0) {
                            updateAllWatched(false); // updateAllWatched calls scheduleNextAutoUpdate when done
                        } else {
                            scheduleNextAutoUpdate(); // nothing stale yet, reschedule
                        }
                    })
                    .catch(() => scheduleNextAutoUpdate());
            }, delay);
        }

        function checkAutoUpdate() {
            fetch('/watchlist')
                .then(r => r.json())
                .then(data => {
                    watchlistData = data.items || [];
                    updateWatchlistBadge();
                    const stale = watchlistData.filter(i => i._stale);
                    if (stale.length > 0) {
                        // Small delay so page finishes rendering first
                        setTimeout(() => updateAllWatched(false), 1500);
                    } else {
                        scheduleNextAutoUpdate();
                    }
                })
                .catch(() => {});
        }

        // Refresh displayed timestamps every 60s without full reload
        setInterval(function() {
            if (watchlistData.length > 0) renderWatchlist();
        }, 60000);

        // ── Telegram Integration ──────────────────────────────────────────
        let _tgConfigured = false;

        function updateTelegramButton(configured) {
            _tgConfigured = configured;
            const btn = document.getElementById('btn-telegram');
            if (!btn) return;
            if (configured) {
                btn.classList.add('configured');
                btn.title = 'Telegram configurado — clique para editar';
            } else {
                btn.classList.remove('configured');
                btn.title = 'Configurar notificações no Telegram';
            }
        }

        function loadTelegramConfig() {
            fetch('/telegram/config')
                .then(r => r.json())
                .then(d => {
                    updateTelegramButton(d.configured);
                })
                .catch(() => {});
        }

        function showTelegramModal() {
            document.getElementById('tg-status-bar').style.display = 'none';
            document.getElementById('tg-token').value = '';
            document.getElementById('tg-chat-id').value = '';
            const testBtn = document.getElementById('btn-tg-test');
            const removeBtn = document.getElementById('btn-tg-remove');
            fetch('/telegram/config')
                .then(r => r.json())
                .then(d => {
                    if (d.configured) {
                        document.getElementById('tg-chat-id').value = d.chat_id || '';
                        document.getElementById('tg-token').placeholder = d.token_masked || 'Token já configurado';
                        _showTgStatus('ok', 'Telegram configurado. Deixe o Token em branco para manter o atual.');
                        testBtn.style.display = '';
                        removeBtn.style.display = '';
                    } else {
                        testBtn.style.display = 'none';
                        removeBtn.style.display = 'none';
                    }
                })
                .catch(() => {});
            document.getElementById('telegram-modal').style.display = 'flex';
        }

        function closeTelegramModal() {
            document.getElementById('tg-confirm-box').style.display = 'none';
            document.getElementById('telegram-modal').style.display = 'none';
        }

        function _showTgStatus(type, msg) {
            const bar = document.getElementById('tg-status-bar');
            bar.className = 'tg-status ' + type;
            bar.textContent = msg;
            bar.style.display = 'flex';
        }

        function saveTelegram() {
            const token = document.getElementById('tg-token').value.trim();
            const chatId = document.getElementById('tg-chat-id').value.trim();
            if (!chatId) { _showTgStatus('not-ok', 'Preencha o Chat ID.'); return; }

            // If token blank and already configured, just update chat_id with current token
            const payload = token ? { token, chat_id: chatId } : null;
            if (!payload) {
                // Need token to save — fetch current masked and ask user
                _showTgStatus('not-ok', 'Digite o Token para salvar (ou use o botão Testar se já configurado).');
                return;
            }

            fetch('/telegram/config', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        updateTelegramButton(true);
                        document.getElementById('btn-tg-test').style.display = '';
                        document.getElementById('btn-tg-remove').style.display = '';
                        document.getElementById('tg-token').value = '';
                        document.getElementById('tg-token').placeholder = 'Token salvo';
                        _showTgStatus('ok', 'Configuração salva! Clique em Testar para verificar.');
                    } else {
                        _showTgStatus('not-ok', d.error || 'Erro ao salvar.');
                    }
                })
                .catch(() => _showTgStatus('not-ok', 'Erro de rede.'));
        }

        function removeTelegram() {
            if (!confirm('Remover configuração do Telegram?')) return;
            fetch('/telegram/config', { method: 'DELETE' })
                .then(r => r.json())
                .then(() => {
                    updateTelegramButton(false);
                    document.getElementById('tg-token').value = '';
                    document.getElementById('tg-token').placeholder = '123456789:ABCdef...';
                    document.getElementById('tg-chat-id').value = '';
                    document.getElementById('btn-tg-test').style.display = 'none';
                    document.getElementById('btn-tg-remove').style.display = 'none';
                    _showTgStatus('not-ok', 'Configuração removida.');
                })
                .catch(() => _showTgStatus('not-ok', 'Erro de rede.'));
        }

        function detectChatId() {
            const token = document.getElementById('tg-token').value.trim();
            if (!token) { _showTgStatus('not-ok', 'Cole o Token antes de detectar o Chat ID.'); return; }
            _showTgStatus('ok', 'Detectando Chat ID...');
            fetch('/telegram/detect_chat_id', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ token }) })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        document.getElementById('tg-chat-id').value = d.chat_id;
                        _showTgStatus('ok', 'Chat ID detectado: ' + d.chat_id + '. Clique em Salvar para confirmar.');
                    } else {
                        _showTgStatus('not-ok', d.error || 'Não foi possível detectar o Chat ID.');
                    }
                })
                .catch(() => _showTgStatus('not-ok', 'Erro de rede.'));
        }

        function testTelegram() {
            const token = document.getElementById('tg-token').value.trim();
            const chatId = document.getElementById('tg-chat-id').value.trim();
            const confirmBox = document.getElementById('tg-confirm-box');
            confirmBox.style.display = 'none';
            _showTgStatus('ok', 'Enviando mensagem de teste...');
            fetch('/telegram/test', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ token: token || undefined, chat_id: chatId || undefined }) })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        _showTgStatus('ok', 'Mensagem enviada! Confira no seu Telegram.');
                        confirmBox.style.display = '';
                    } else {
                        _showTgStatus('not-ok', d.error || 'Falha ao enviar mensagem.');
                    }
                })
                .catch(() => _showTgStatus('not-ok', 'Erro de rede.'));
        }

        function confirmTelegramYes() {
            document.getElementById('tg-confirm-box').style.display = 'none';
            // Salvar config (reutiliza lógica do saveTelegram, mas fecha ao final)
            const token = document.getElementById('tg-token').value.trim();
            const chatId = document.getElementById('tg-chat-id').value.trim();
            if (!chatId) { _showTgStatus('not-ok', 'Preencha o Chat ID.'); return; }
            const payload = token ? { token, chat_id: chatId } : null;
            if (!payload) {
                // Já está salvo (token em branco = mantém atual) — fecha direto
                closeTelegramModal();
                showNotification('Alertas no Telegram configurados corretamente!', 'success');
                return;
            }
            fetch('/telegram/config', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        updateTelegramButton(true);
                        closeTelegramModal();
                        showNotification('Alertas no Telegram configurados corretamente!', 'success');
                    } else {
                        _showTgStatus('not-ok', d.error || 'Erro ao salvar.');
                    }
                })
                .catch(() => _showTgStatus('not-ok', 'Erro de rede ao salvar.'));
        }

        function confirmTelegramNo() {
            document.getElementById('tg-confirm-box').style.display = 'none';
            _showTgStatus('not-ok', 'Verifique o Token e Chat ID e refaça o passo a passo do tutorial acima.');
        }
        // ─────────────────────────────────────────────────────────────────
    </script>
</body>
</html>
'''



def formatar_preco(preco_str):
    s = preco_str.replace('R$', '').replace(u'\xa0', ' ').strip()
    s = unescape(s)
    s = re.sub(r'\s+', '', s)
    # BR: 1.234,56 ou 234,56
    if re.match(r'^[\d.]+,\d{1,2}$', s):
        s = s.replace('.', '').replace(',', '.')
    # US: 1,234.56 ou 219.9 (JSON com 1 ou 2 casas decimais)
    elif re.match(r'^[\d,]*\.\d{1,2}$', s):
        s = s.replace(',', '')
    else:
        # Tenta float direto antes de manipular (evita destruir valores já corretos)
        try:
            v = float(s)
            if v > 0:
                return v
        except ValueError:
            pass
        s = s.replace('.', '').replace(',', '.')
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


HTTP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
}


def http_get(url, referer=None):
    headers = dict(HTTP_HEADERS)
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode('utf-8', 'replace')


def _nova_sessao_scraper():
    """Cria um cloudscraper que contorna Cloudflare/WAF automaticamente."""
    return _cloudscraper.create_scraper()


def _scraper_get(scraper, url, referer=None, tentativas=3):
    """GET com retry."""
    headers = {'Referer': referer} if referer else {}
    for i in range(tentativas):
        try:
            r = scraper.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                return r.text
            if i < tentativas - 1:
                time.sleep(1.0 * (i + 1))
        except Exception:
            if i < tentativas - 1:
                time.sleep(1.0)
    return ''


def padronizar_texto(texto):
    texto = texto.lower()
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII')
    texto = re.sub(r'(\d+)\s?(gb|tb|mb)', r'\1\2', texto)
    texto = re.sub(r'[^a-z0-9\s]', '', texto)
    return texto.split()


def tokens_busca_significativos(produto_query):
    """Evita token de 1 letra (ex.: 'a') que filtraria quase tudo."""
    tokens = padronizar_texto(produto_query)
    out = [t for t in tokens if len(t) >= 2 or t.isdigit()]
    return out if out else tokens


def nome_compativel_com_busca(nome, produto_query):
    """Cada termo relevante da busca deve aparecer no nome (substring no texto normalizado)."""
    tokens = tokens_busca_significativos(produto_query)
    if not tokens:
        return True
    blob = ''.join(padronizar_texto(nome))
    return all(t in blob for t in tokens)


def _terabyte_produto_indisponivel_listagem(bloco):
    """Retorna True se o bloco de produto indicar fora de estoque."""
    if re.search(r'class="[^"]*(?:out-of-stock|sem-estoque|esgotado|unavailable)[^"]*"', bloco, re.IGNORECASE):
        return True
    if re.search(r'(?:Indispon[ií]vel|Esgotado|Avise-me|out.of.stock)', bloco, re.IGNORECASE):
        return True
    return False


def _terabyte_produto_indisponivel_pagina(html):
    """Retorna True se a página do produto indicar fora de estoque.

    Terabyte sempre inclui id="indisponivel" na página — só está ativo quando o produto
    está sem estoque, quando contém o texto "Produto Indisponível".
    """
    # id="indisponivel" próximo a "Produto Indisponível" = produto sem estoque
    m = re.search(r'id="indisponivel"', html, re.IGNORECASE)
    if m:
        janela = html[m.start():m.start() + 600]
        if re.search(r'Produto\s+Indispon[ií]vel', janela, re.IGNORECASE):
            return True
    # Formulário "Avise-me" — só aparece quando sem estoque
    if re.search(r'id="frmaviseme"', html, re.IGNORECASE):
        return True
    # Classes CSS de estoque zerado
    if re.search(r'class="[^"]*(?:out-of-stock|sem-estoque|esgotado|unavailable)[^"]*"', html, re.IGNORECASE):
        return True
    return False


def terabyte_tentar_parse_listagem(html, produto, valor_minimo, valor_maximo):
    """Tenta extrair nome+preço+link direto da listagem da Terabyte.
    Retorna lista de ofertas ou [] se não encontrar preços (cai para page-by-page)."""
    ofertas = []
    blocos = re.split(r'(?=<div[^>]+class="[^"]*(?:product-item|prd-item|item-product)[^"]*")', html)
    if len(blocos) <= 1:
        return []
    for bloco in blocos[1:]:
        if _terabyte_produto_indisponivel_listagem(bloco):
            continue
        lm = re.search(r'href="(https://www\.terabyteshop\.com\.br/produto/[^"]+)"', bloco)
        if not lm:
            continue
        link = lm.group(1).split('"')[0]
        nm = re.search(
            r'<(?:h[1-6]|span)[^>]*class="[^"]*(?:product-name|prd-name|name|title)[^"]*"[^>]*>([^<]+)</(?:h[1-6]|span)>',
            bloco,
        )
        if not nm:
            nm = re.search(r'<h[23][^>]*>([^<]+)</h[23]>', bloco)
        if not nm:
            continue
        nome = unescape(re.sub(r'\s+', ' ', nm.group(1).strip()))
        preco_valor = None
        for pat in (
            # Apenas dígitos/vírgulas/pontos após R$ (evita capturar texto de parcelamento)
            r'class="[^"]*prod-new-price[^"]*"[^>]*>\s*(?:<[^>]+>\s*)?(R\$\s*[\d.,]+)',
            r'class="[^"]*product-item__new-price[^"]*"[^>]*>\s*<span>\s*(R\$\s*[\d.,]+)',
            r'class="[^"]*price[^"]*"[^>]*>\s*(R\$\s*[\d.,]+)',
            r'"priceWithDiscount"\s*:\s*"?([\d]+\.[\d]{1,2})',
        ):
            pm = re.search(pat, bloco)
            if pm:
                preco_valor = formatar_preco(unescape(pm.group(1)))
                if preco_valor and preco_valor > 0:
                    break
        if preco_valor is None:
            continue
        if not nome_compativel_com_busca(nome, produto):
            continue
        if not is_produto_principal(nome, produto):
            continue
        if not is_produto_novo(nome, produto):
            continue
        if not (valor_minimo <= preco_valor <= valor_maximo):
            continue
        imgm = re.search(r'<img[^>]+src="(https://img\.terabyteshop\.com\.br[^"]+)"', bloco)
        imagem = imgm.group(1) if imgm else None
        ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem})
    return ofertas


def terabyte_parse_pagina_produto(html, link):
    """Extrai preço à vista + parcelamento da página do produto.

    Estrutura real da Terabyte (produto principal):
      <p id="valVista" class="val-prod valVista">R$ 1.089,99</p>   ← preço à vista
      <span id="nParc" class="nParc">12x</span>                    ← parcelas
      <span id="Parc" class="Parc">R$ 106,86</span>               ← valor por parcela
      <span id="jrParc">sem juros no cartão</span>                 ← sem juros?

    ATENÇÃO: product-item__new-price / product-item__old-price são dos PRODUTOS RELACIONADOS
    no rodapé, não do produto principal — não usar para o produto principal.
    """
    if _terabyte_produto_indisponivel_pagina(html):
        return None
    h1m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if not h1m:
        return None
    nome = unescape(re.sub(r'\s+', ' ', h1m.group(1).strip()))

    # Preço à vista do produto principal
    vista_m = re.search(r'id="valVista"[^>]*>\s*(R\$\s*[\d.,]+)', html)
    if not vista_m:
        return None
    preco_valor = formatar_preco(unescape(vista_m.group(1)))
    if preco_valor is None:
        return None

    imgm = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
    if not imgm:
        imgm = re.search(r'"image"\s*:\s*"(https://img\.terabyteshop\.com\.br[^"]+)"', html)
    imagem = imgm.group(1) if imgm else None

    # Parcelamento: id="nParc" (Nx), id="Parc" (R$ valor), id="jrParc" (sem juros?)
    parcelamento = None
    nparc_m = re.search(r'id="nParc"[^>]*>(\d+)x', html)
    parc_m = re.search(r'id="Parc"[^>]*>\s*(R\$\s*[\d.,]+)', html)
    if nparc_m and parc_m:
        n_parc = int(nparc_m.group(1))
        val_parc = formatar_preco(unescape(parc_m.group(1)))
        sem_juros_flag = bool(re.search(r'id="jrParc"[^>]*>[^<]*sem juros', html, re.IGNORECASE))
        if val_parc and n_parc >= 2:
            parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': sem_juros_flag}
    return {'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento}


def _terabyte_processar_links_sequencial(args):
    """Cloudscraper, requisições em série (paralelismo dispara bloqueio no WAF)."""
    links, url_busca, produto, valor_minimo, valor_maximo, scraper = args
    ofertas = []
    if not links:
        return ofertas

    for link in links:
        try:
            html = _scraper_get(scraper, link, url_busca, tentativas=2)
            if not html:
                continue
            o = terabyte_parse_pagina_produto(html, link)
            if not o:
                continue
            if not nome_compativel_com_busca(o['nome'], produto):
                continue
            if not is_produto_principal(o['nome'], produto):
                continue
            if not is_produto_novo(o['nome'], produto):
                continue
            if not (valor_minimo <= o['preco'] <= valor_maximo):
                continue
            ofertas.append(o)
        except Exception as e:
            print(f'Terabyte produto {link}: {e}')
        time.sleep(0.15)
    return ofertas


def kabum_extrair_preco(item):
    off = item.get('offer') if isinstance(item.get('offer'), dict) else {}
    for src in (off, item):
        for key in ('priceWithDiscount', 'price'):
            v = src.get(key)
            if v is None:
                continue
            try:
                fv = float(v)
                if fv > 0:
                    return fv
            except (TypeError, ValueError):
                continue
    return None


def buscar_kabum(produto, valor_minimo, valor_maximo):
    ofertas = []
    encontrou_produtos = False
    try:
        slug = re.sub(r'\s+', '-', produto.strip())
        slug = urllib.parse.quote(slug, safe='-')
        url = f'https://www.kabum.com.br/busca/{slug}?page_size=60'
        html = http_get(url, referer='https://www.kabum.com.br/')
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return [], False
        payload = json.loads(m.group(1))
        data = (
            payload.get('props', {})
            .get('pageProps', {})
            .get('data', {})
        )
        # Kabum sometimes double-encodes data as a JSON string
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return [], False
        items = (
            data.get('catalogServer', {})
            .get('data') if isinstance(data, dict) else None
        )
        if not isinstance(items, list):
            return [], False
        encontrou_produtos = len(items) > 0
        for item in items:
            nome = (item.get('name') or '').strip()
            code = item.get('code')
            if not nome or code is None:
                continue
            preco_valor = kabum_extrair_preco(item)
            if preco_valor is None:
                continue
            if not nome_compativel_com_busca(nome, produto):
                continue
            if not is_produto_principal(nome, produto):
                continue
            if not is_produto_novo(nome, produto):
                continue
            if not (valor_minimo <= preco_valor <= valor_maximo):
                continue
            link = f'https://www.kabum.com.br/produto/{code}'
            imagem = item.get('image') or item.get('thumbnail')
            parcelamento = None
            mi = str(item.get('maxInstallment') or '')
            mp = re.match(r'(\d+)x\s*de\s*R\$\s*([\d,.]+)', mi)
            if mp:
                n_parc = int(mp.group(1))
                val_parc = formatar_preco(mp.group(2))
                if val_parc:
                    price_card = float(item.get('price') or 0)
                    sem_juros = price_card > 0 and n_parc * val_parc <= price_card + 1.0
                    parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': sem_juros}
            ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})
        ofertas.sort(key=lambda x: x['preco'])
    except Exception as e:
        print(f'Erro Kabum: {e}')
        return [], False
    return ofertas, encontrou_produtos


def _pichau_rsc_headers(q, page=1):
    """Headers para requisição RSC (React Server Components) da Pichau."""
    params = f'q={q}&pageSize=48'
    if page > 1:
        params += f'&page={page}'
    return {
        'User-Agent': HTTP_HEADERS['User-Agent'],
        'Accept': '*/*',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        'RSC': '1',
        'Next-Router-State-Tree': '%5B%22%22%2C%7B%22children%22%3A%5B%22search%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D',
        'Next-Url': f'/search?{params}',
        'Referer': 'https://www.pichau.com.br/',
    }


def _pichau_parse_rsc(data, produto, valor_minimo, valor_maximo):
    """Extrai ofertas do payload RSC da Pichau (JSON estruturado)."""
    ofertas = []
    m = re.search(r'"items"\s*:\s*(\[.*?\])\s*,\s*"page_info"', data, re.DOTALL)
    if not m:
        return [], 0
    try:
        items = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return [], 0

    for item in items:
        nome = (item.get('name') or '').strip()
        if not nome:
            continue
        if item.get('stock_status') != 'IN_STOCK':
            continue
        if not nome_compativel_com_busca(nome, produto):
            continue
        if not is_produto_principal(nome, produto):
            continue
        if not is_produto_novo(nome, produto):
            continue

        prices = item.get('pichau_prices') or {}
        preco_valor = prices.get('avista')
        if preco_valor is None or preco_valor <= 0:
            preco_valor = prices.get('final_price')
        if preco_valor is None or preco_valor <= 0:
            continue
        preco_valor = float(preco_valor)

        if not (valor_minimo <= preco_valor <= valor_maximo):
            continue

        url_key = item.get('url_key') or ''
        link = f'https://www.pichau.com.br/{url_key}' if url_key else ''

        img_data = item.get('image') or {}
        imagem = img_data.get('url_listing') or img_data.get('url')

        parcelamento = None
        max_inst = prices.get('max_installments')
        min_inst_price = prices.get('min_installment_price')
        if max_inst and min_inst_price and int(max_inst) >= 2:
            n_parc = int(max_inst)
            val_parc = float(min_inst_price)
            total_parc = n_parc * val_parc
            final_price = prices.get('final_price') or (preco_valor / 0.85)
            sem_juros = total_parc <= float(final_price) + 1.0
            parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': sem_juros}

        ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})
    return ofertas, len(items)


def _pichau_parse_html_fallback(html, produto, valor_minimo, valor_maximo):
    """Fallback: extrai ofertas via HTML quando RSC não retorna items (buscas genéricas)."""
    result = []
    partes = re.split(r'(?=<a\s[^>]*data-cy="list-product")', html)
    for parte in partes[1:]:
        hm = re.search(r'data-cy="list-product"\s+href="([^"]+)"', parte)
        if not hm:
            continue
        href = hm.group(1)
        link = 'https://www.pichau.com.br' + href if href.startswith('/') else href
        h2m = re.search(r'<h2[^>]*>([^<]+)</h2>', parte)
        if not h2m:
            continue
        nome = unescape(re.sub(r'\s+', ' ', h2m.group(1).strip()))
        pm = re.search(r'class="[^"]*-pixPrice"[^>]*><span>(R\$[^<]+)</span>', parte)
        if not pm:
            pm = re.search(r'class="[^"]*-price_vista"[^>]*>(R\$[^<]+)<', parte)
        if not pm:
            continue
        preco_valor = formatar_preco(unescape(pm.group(1)))
        if preco_valor is None:
            continue
        if not nome_compativel_com_busca(nome, produto):
            continue
        if not is_produto_principal(nome, produto):
            continue
        if not is_produto_novo(nome, produto):
            continue
        if not (valor_minimo <= preco_valor <= valor_maximo):
            continue
        imgm = re.search(r'<img[^>]+src="(https://media\.pichau\.com\.br[^"]+)"', parte)
        imagem = imgm.group(1) if imgm else None
        parcelamento = None
        texto_bloco = re.sub(r'<!--.*?-->', '', parte, flags=re.DOTALL)
        texto_bloco = re.sub(r'<[^>]+>', ' ', texto_bloco)
        texto_bloco = re.sub(r'\s+', ' ', texto_bloco)
        im = re.search(
            r'\b([2-9]|[1-9]\d+)\s*x\s*(?:de\s*)?R\$\s*([\d,.]+)(?:[^0-9]{0,60}?(sem juros))?',
            texto_bloco, re.IGNORECASE,
        )
        if im:
            n_parc = int(im.group(1))
            val_parc = formatar_preco(im.group(2))
            if val_parc:
                parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': bool(im.group(3))}
        result.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})
    return result


def buscar_pichau(produto, valor_minimo, valor_maximo):
    """Busca via RSC flight payload (1 request, JSON estruturado). Fallback: cloudscraper + HTML."""
    ofertas = []
    try:
        q = urllib.parse.quote(produto.strip())
        url = f'https://www.pichau.com.br/search?q={q}&pageSize=48'

        # Tenta RSC primeiro (rápido, sem cloudscraper)
        try:
            headers = _pichau_rsc_headers(q)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read().decode('utf-8', 'replace')
            result, total_items = _pichau_parse_rsc(data, produto, valor_minimo, valor_maximo)
            if total_items > 0:
                ofertas.extend(result)
                ofertas.sort(key=lambda x: x['preco'])
                return ofertas, True
        except Exception:
            pass

        # Fallback: cloudscraper + HTML (buscas genéricas que o RSC não suporta)
        scraper = _nova_sessao_scraper()
        html = _scraper_get(scraper, f'https://www.pichau.com.br/search?q={q}', 'https://www.pichau.com.br/')
        encontrou = 'data-cy="list-product"' in (html or '')
        if html:
            ofertas.extend(_pichau_parse_html_fallback(html, produto, valor_minimo, valor_maximo))

        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, len(ofertas) > 0 or encontrou
    except Exception as e:
        print(f'Erro Pichau: {e}')
        return [], False


def _terabyte_parse_listagem(html, produto, valor_minimo, valor_maximo):
    """Extrai ofertas direto da página de listagem da Terabyte (sem abrir cada produto)."""
    ofertas = []
    cards = re.split(r'(?=<div class="product-item">)', html)
    for card in cards[1:]:
        # Pula produtos esgotados
        if 'tbt_esgotado' in card or _terabyte_produto_indisponivel_listagem(card):
            continue

        # Nome via <h2> dentro de a.product-item__name
        nm = re.search(r'class="product-item__name"[^>]*>\s*<h2>([^<]+)</h2>', card)
        if not nm:
            continue
        nome = unescape(re.sub(r'\s+', ' ', nm.group(1).strip()))

        if not nome_compativel_com_busca(nome, produto):
            continue
        if not is_produto_principal(nome, produto):
            continue
        if not is_produto_novo(nome, produto):
            continue

        # Link
        lm = re.search(r'href="(https://www\.terabyteshop\.com\.br/produto/\d+/[^"]+)"', card)
        if not lm:
            continue
        link = lm.group(1).split('"')[0]

        # Preço à vista (product-item__new-price > span)
        pm = re.search(r'class="product-item__new-price"[^>]*>\s*<span>(R\$\s*[\d.,]+)</span>', card)
        if not pm:
            continue
        preco_valor = formatar_preco(unescape(pm.group(1)))
        if preco_valor is None:
            continue

        if not (valor_minimo <= preco_valor <= valor_maximo):
            continue

        # Imagem
        imgm = re.search(r'<img[^>]+class="image-thumbnail"[^>]+src="([^"]+)"', card)
        imagem = imgm.group(1) if imgm else None

        # Parcelamento (product-item__juros)
        parcelamento = None
        juros_m = re.search(r'class="product-item__juros"(.*?)</div>', card, re.DOTALL)
        if juros_m:
            txt = re.sub(r'<[^>]+>', ' ', juros_m.group(1))
            txt = re.sub(r'\s+', ' ', txt).strip()
            im = re.search(r'(\d+)x\s*(?:de\s*)?(R\$\s*[\d.,]+)(?:.*?(sem juros))?', txt, re.IGNORECASE)
            if im:
                n_parc = int(im.group(1))
                val_parc = formatar_preco(im.group(2))
                if val_parc and n_parc >= 2:
                    parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': bool(im.group(3))}

        ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})
    return ofertas


def buscar_terabyte(produto, valor_minimo, valor_maximo):
    """Extrai preços direto da listagem (1-2 requests em vez de ~22)."""
    ofertas = []
    vistos = set()
    encontrou_produtos = False
    try:
        q = urllib.parse.quote_plus(produto.strip())
        url_busca = f'https://www.terabyteshop.com.br/busca?str={q}'

        scraper = _nova_sessao_scraper()
        html_p1 = _scraper_get(scraper, url_busca, 'https://www.terabyteshop.com.br/')
        encontrou_produtos = '<div class="product-item">' in (html_p1 or '')

        if html_p1:
            for o in _terabyte_parse_listagem(html_p1, produto, valor_minimo, valor_maximo):
                if o['link'] not in vistos:
                    vistos.add(o['link'])
                    ofertas.append(o)

        html_p2 = _scraper_get(scraper, f'{url_busca}&pagina=2', url_busca) if html_p1 else ''
        if html_p2:
            for o in _terabyte_parse_listagem(html_p2, produto, valor_minimo, valor_maximo):
                if o['link'] not in vistos:
                    vistos.add(o['link'])
                    ofertas.append(o)

        ofertas.sort(key=lambda x: x['preco'])
    except Exception as e:
        print(f'Erro Terabyte: {e}')
        return [], False
    return ofertas, encontrou_produtos

def _ml_nome_bate_query(query, nome):
    """Verifica se todas as especificações numéricas do query estão no nome do produto.
    Ex: query '16GB' não pode bater com produto '8GB'."""
    def normalizar(s):
        s = re.sub(r'[^a-z0-9]', ' ', s.lower())
        # colapsa "16 gb" → "16gb", "5060 ti" permanece separado
        s = re.sub(r'(\d+)\s+([a-z]+)', r'\1\2', s)
        return s

    q = normalizar(query)
    n = normalizar(nome)
    # tokens do query que contêm dígitos são as especificações críticas
    spec_tokens = [t for t in q.split() if re.search(r'\d', t)]
    return all(t in n for t in spec_tokens)


def _ml_extrair_parcelamentos_html(html):
    """Extrai mapa de parcelamento do HTML (link -> parcelamento) para enriquecer JSON-LD."""
    parcelamentos = {}
    blocos = re.split(r'(?=<li[^>]*class="[^"]*ui-search-layout__item[^"]*")', html)
    for bloco in blocos[1:]:
        lm = re.search(r'class="poly-component__title[^"]*">\s*<a[^>]*href="([^"]+)"', bloco)
        if not lm:
            continue
        link = lm.group(1).split('#')[0].split('?')[0]
        bloco_sem_old = re.sub(r'<s\b[^>]*>.*?</s>', '', bloco, flags=re.DOTALL)
        inst_block = re.search(
            r'class="poly-price__installments"[^>]*>(.*?)(?=<div\b|</li>)',
            bloco_sem_old, re.DOTALL,
        )
        if inst_block:
            txt_inst = re.sub(r'<[^>]+>', ' ', inst_block.group(1))
            txt_inst = re.sub(r'\s+', ' ', txt_inst).strip()
            m_inst = re.match(r'(\d+)x\s*R\$\s*([\d\s,]+?)(?:\s+(sem juros))?$', txt_inst, re.IGNORECASE)
            if m_inst:
                n_parc = int(m_inst.group(1))
                val_str = m_inst.group(2).replace(' ', '').replace(',', '.')
                try:
                    val_parc = float(val_str)
                    parcelamentos[link] = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': bool(m_inst.group(3))}
                except ValueError:
                    pass
    return parcelamentos


def buscar_mercadolivre(produto, valor_minimo, valor_maximo):
    """Usa JSON-LD (application/ld+json) para dados estruturados + HTML para parcelamento."""
    ofertas = []
    try:
        slug = urllib.parse.quote(re.sub(r'\s+', '-', produto.strip().lower()), safe='-')
        url = f'https://lista.mercadolivre.com.br/{slug}'
        if valor_minimo > 0 or valor_maximo != float('inf'):
            price_min = int(valor_minimo) if valor_minimo > 0 else ''
            price_max = int(valor_maximo) if valor_maximo != float('inf') else ''
            url += f'_PriceRange_{price_min}BRL-{price_max}BRL'

        html = http_get(url, referer='https://www.mercadolivre.com.br/')

        # Tenta JSON-LD primeiro (muito mais robusto)
        ld_match = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if ld_match:
            try:
                ld_data = json.loads(ld_match.group(1))
                items = ld_data.get('@graph', [])
            except (json.JSONDecodeError, TypeError):
                items = []
        else:
            items = []

        if not items:
            # Fallback: sem JSON-LD, retorna vazio (HTML mudou muito)
            return [], False

        # Extrai parcelamentos do HTML para enriquecer
        parcelamentos = _ml_extrair_parcelamentos_html(html)

        for item in items:
            nome = (item.get('name') or '').strip()
            if not nome:
                continue
            if not _ml_nome_bate_query(produto, nome):
                continue
            if not is_produto_principal(nome, produto):
                continue

            offers = item.get('offers') or {}

            # Filtrar usado/recondicionado via campo itemCondition do schema.org
            # Valores: "https://schema.org/NewCondition", "UsedCondition", "RefurbishedCondition"
            item_condition = (offers.get('itemCondition') or '').lower()
            if any(c in item_condition for c in ('used', 'refurbished')):
                continue
            # Fallback: filtro textual no nome (captura "(Recondicionado)", "Usado:", etc.)
            if not is_produto_novo(nome, produto):
                continue

            preco_valor = offers.get('price')
            if preco_valor is None:
                continue
            try:
                preco_valor = float(preco_valor)
            except (TypeError, ValueError):
                continue

            if not (valor_minimo <= preco_valor <= valor_maximo):
                continue

            link = (offers.get('url') or '').split('#')[0].split('?')[0]
            if not link.startswith('https://'):
                continue

            imagem = item.get('image')
            parcelamento = parcelamentos.get(link)

            ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})

        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, len(items) > 0
    except Exception as e:
        print(f'Erro Mercado Livre: {e}')
        return [], False


def buscar_magalu(produto, valor_minimo, valor_maximo):
    """
    Busca via Next.js data API (/_next/data/{buildId}/busca/{slug}.json):
    - BuildId cacheado em módulo → ~1s (warm) vs ~3s (cold)
    - Cold: busca HTML completo, extrai buildId + produtos, aquece cache
    - Warm: chama API JSON diretamente (~1.1s, 3x mais rápido)
    - Em caso de 404 (buildId expirou): invalida cache e refaz via HTML

    Otimização: ~3s → ~1s nas buscas subsequentes
    """
    global _MAGALU_BUILD_ID
    ofertas = []

    def _parse_products(products):
        """Extrai ofertas da lista de produtos da Magalu."""
        resultado = []
        for p in products:
            if not p.get('available'):
                continue
            nome = (p.get('title') or '').strip()
            if not nome:
                continue
            if not nome_compativel_com_busca(nome, produto):
                continue
            if not is_produto_principal(nome, produto):
                continue
            if not is_produto_novo(nome, produto):
                continue
            price_data = p.get('price') or {}
            preco_str = price_data.get('bestPrice') or price_data.get('fullPrice')
            if not preco_str:
                continue
            try:
                preco_valor = float(preco_str)
            except (TypeError, ValueError):
                continue
            if preco_valor <= 0 or not (valor_minimo <= preco_valor <= valor_maximo):
                continue
            path = p.get('path') or ''
            link = f'https://www.magazineluiza.com.br{path}' if path.startswith('/') else ''
            img_url = p.get('image') or ''
            imagem = img_url.replace('{w}x{h}', '400x400') if '{w}' in img_url else img_url
            parcelamento = None
            inst = p.get('installment') or {}
            qty = inst.get('quantity')
            amt = inst.get('amount')
            if qty and amt:
                try:
                    n_parc = int(qty)
                    val_parc = float(amt)
                    if n_parc >= 2 and val_parc > 0:
                        sem_juros = 'sem juros' in (inst.get('paymentMethodDescription') or '').lower()
                        parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': sem_juros}
                except (TypeError, ValueError):
                    pass
            resultado.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})
        return resultado

    def _fetch_via_html(slug):
        """Busca HTML completa — lenta (~3s) mas extrai buildId para aquecer cache."""
        url = f'https://www.magazineluiza.com.br/busca/{slug}/'
        if _CB_SESSION is not None:
            r = _CB_SESSION.get(url, timeout=15)
            html = r.text if r.status_code == 200 else ''
        else:
            html = http_get(url, referer='https://www.magazineluiza.com.br/')
        if not html:
            return None, None
        # Extrair buildId
        bid_m = re.search(r'"buildId":"([^"]+)"', html)
        new_bid = bid_m.group(1) if bid_m else None
        # Extrair produtos do __NEXT_DATA__
        nd_m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not nd_m:
            return new_bid, None
        payload = json.loads(nd_m.group(1))
        products = (
            payload.get('props', {})
            .get('pageProps', {})
            .get('data', {})
            .get('search', {})
            .get('products', [])
        )
        return new_bid, products

    def _fetch_via_api(slug, build_id):
        """Busca via _next/data API — rápida (~1s) quando buildId está em cache."""
        url = f'https://www.magazineluiza.com.br/_next/data/{build_id}/busca/{slug}.json'
        if _CB_SESSION is not None:
            r = _CB_SESSION.get(url, timeout=10)
        else:
            req = urllib.request.Request(url, headers=dict(HTTP_HEADERS))
            with urllib.request.urlopen(req, timeout=10) as resp:
                r = type('R', (), {'status_code': resp.status, 'json': lambda: json.loads(resp.read().decode())})()
        if r.status_code == 404:
            return None  # buildId expirou
        if r.status_code != 200:
            return []
        products = (
            r.json()
            .get('pageProps', {})
            .get('data', {})
            .get('search', {})
            .get('products', [])
        )
        return products

    try:
        slug = urllib.parse.quote_plus(produto.strip())
        products = None

        # Busca quente: tentar API direta se buildId estiver cacheado
        if _MAGALU_BUILD_ID:
            products = _fetch_via_api(slug, _MAGALU_BUILD_ID)
            if products is None:
                # buildId expirou — invalidar e cair no fallback HTML
                _MAGALU_BUILD_ID = None

        # Busca fria (ou após invalidação): HTML completo + extração de buildId
        if products is None:
            new_bid, products = _fetch_via_html(slug)
            if new_bid:
                _MAGALU_BUILD_ID = new_bid

        if not products:
            return [], False

        ofertas = _parse_products(products)
        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, len(products) > 0
    except Exception as e:
        print(f'Erro Magalu: {e}')
        return [], False


def buscar_amazon(produto, valor_minimo, valor_maximo):
    """Busca via HTML parsing (cloudscraper) — 1 request."""
    ofertas = []
    encontrou_produtos = False
    try:
        q = urllib.parse.quote_plus(produto.strip())
        url = f'https://www.amazon.com.br/s?k={q}'

        scraper = _nova_sessao_scraper()
        html = _scraper_get(scraper, url, 'https://www.amazon.com.br/')
        if not html:
            return [], False

        cards = re.split(r'(?=data-component-type="s-search-result")', html)
        encontrou_produtos = len(cards) > 1

        for card in cards[1:]:
            # Precisa ter link direto /dp/ (não patrocinados com /sspa/)
            link_m = re.search(r'href="(/[^"]*?/dp/([A-Z0-9]{10})[^"]*)"', card)
            if not link_m:
                continue
            asin = link_m.group(2)
            link = f'https://www.amazon.com.br/dp/{asin}'

            # Nome
            h2 = re.search(r'<h2[^>]*>.*?<span[^>]*>([^<]+)</span>', card, re.DOTALL)
            if not h2:
                continue
            nome = unescape(re.sub(r'\s+', ' ', h2.group(1).strip()))

            if not nome_compativel_com_busca(nome, produto):
                continue
            if not is_produto_principal(nome, produto):
                continue
            if not is_produto_novo(nome, produto):
                continue

            # Preço principal (a-price com data-a-size="xl" = preço destaque)
            price_m = re.search(
                r'<span class="a-price"[^>]*data-a-size="xl"[^>]*>.*?R\$[\s\xa0]*([\d.,]+)',
                card, re.DOTALL,
            )
            if not price_m:
                # Fallback: qualquer a-price
                price_m = re.search(r'<span class="a-price"[^>]*>.*?R\$[\s\xa0]*([\d.,]+)', card, re.DOTALL)
            if not price_m:
                continue
            preco_valor = formatar_preco(price_m.group(1))
            if preco_valor is None:
                continue

            if not (valor_minimo <= preco_valor <= valor_maximo):
                continue

            # Imagem
            img_m = re.search(r'<img[^>]+class="s-image"[^>]+src="([^"]+)"', card)
            imagem = img_m.group(1) if img_m else None

            # Parcelamento: "em até 12x de R$ X" (tags intercaladas, precisa strip)
            parcelamento = None
            inst_area = re.search(r'(em\s+at.{1,3}\s+\d+x\s+de\s+.*?(?:sem juros|</div>))', card, re.IGNORECASE | re.DOTALL)
            if inst_area:
                txt = re.sub(r'<[^>]+>', ' ', inst_area.group(1))
                txt = re.sub(r'\s+', ' ', txt)
                inst_m = re.search(r'(\d+)x\s+de\s+R\$[\s\xa0]*([\d.,]+)', txt)
                if inst_m:
                    n_parc = int(inst_m.group(1))
                    val_parc = formatar_preco(inst_m.group(2))
                    if val_parc and n_parc >= 2:
                        sem_juros = bool(re.search(r'sem\s+juros', txt, re.IGNORECASE))
                        parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': sem_juros}

            ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})

        ofertas.sort(key=lambda x: x['preco'])
    except Exception as e:
        print(f'Erro Amazon: {e}')
        return [], False
    return ofertas, encontrou_produtos


def buscar_shopee(produto, valor_minimo, valor_maximo):
    """
    Busca na Shopee via Googlebot SSR + curl_cffi (JSON-LD):
    - 4 sorts em paralelo (pop, price, sales, ctime) → ~30-40 produtos únicos
    - N reqs produto em paralelo (20 workers) → precos via JSON-LD offers
    - curl_cffi como motor HTTP (TLS fingerprint ~8x mais rápido que urllib)
    - urllib como fallback se curl_cffi indisponível

    Otimização v3: ~21s/10 produtos → ~3-4s/30-40 produtos
    """
    UA_BOT = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
    _HEADERS_BOT = {
        'User-Agent': UA_BOT,
        'Accept': 'text/html',
        'Accept-Language': 'pt-BR,pt;q=0.9',
    }

    def _bot_get(url, timeout=8):
        """GET com Googlebot UA — usa curl_cffi se disponível, senão urllib."""
        try:
            if _cffi_requests is not None:
                r = _cffi_requests.get(url, headers=_HEADERS_BOT, timeout=timeout, impersonate=None)
                return r.text if r.status_code == 200 else ''
            else:
                req = urllib.request.Request(url, headers=_HEADERS_BOT)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode('utf-8', 'replace')
        except Exception:
            return ''

    def _parse_busca(html):
        produtos = []
        for bloco in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(bloco)
                if isinstance(d, dict) and d.get('@type') == 'ItemList':
                    for it in d.get('itemListElement', []):
                        nome = it.get('name', '').strip()
                        link = it.get('url', '').strip()
                        if nome and link:
                            produtos.append({'nome': nome, 'link': link, 'imagem': it.get('image', ''), 'loja': 'Shopee', 'preco': None})
            except Exception:
                pass
        return produtos

    def _get_preco(prod):
        html = _bot_get(prod['link'])
        if not html:
            return prod
        for bloco in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(bloco)
                if isinstance(d, dict) and d.get('@type') == 'Product':
                    offers = d.get('offers', {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    p_raw = offers.get('price') or offers.get('lowPrice')
                    if p_raw is not None:
                        preco = float(str(p_raw).replace(',', '.'))
                        if preco > 0:
                            prod['preco'] = preco
                            imagem = d.get('image', '')
                            if isinstance(imagem, list):
                                imagem = imagem[0] if imagem else ''
                            if imagem and not prod.get('imagem'):
                                prod['imagem'] = str(imagem)
                            return prod
            except Exception:
                pass
        return prod

    ofertas = []
    try:
        from concurrent.futures import as_completed as _as_completed

        kw = urllib.parse.quote(produto.strip())

        # Fase 1: 4 sorts em paralelo → máximo de produtos únicos (~30-40)
        # Paginação não funciona (page>0 retorna vazio no SSR), mas sorts
        # diferentes retornam conjuntos parcialmente disjuntos.
        _SORTS = ('pop', 'price', 'sales', 'ctime')
        produtos_uniq = {}

        def _fetch_sort(sort):
            html = _bot_get(f'https://shopee.com.br/search?keyword={kw}&page=0&sortBy={sort}', timeout=12)
            return _parse_busca(html)

        with ThreadPoolExecutor(max_workers=4) as ex:
            futuros = {ex.submit(_fetch_sort, s): s for s in _SORTS}
            for fut in _as_completed(futuros):
                for p in fut.result():
                    produtos_uniq[p['link']] = p

        produtos = list(produtos_uniq.values())
        if not produtos:
            return [], False

        # Fase 2: preços em paralelo (20 workers — requests são leves)
        com_preco = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            futuros = {ex.submit(_get_preco, p): p for p in produtos}
            for fut in _as_completed(futuros):
                try:
                    com_preco.append(fut.result())
                except Exception:
                    com_preco.append(futuros[fut])

        for p in com_preco:
            preco = p.get('preco')
            if not preco:
                continue
            if not nome_compativel_com_busca(p['nome'], produto):
                continue
            if not is_produto_principal(p['nome'], produto):
                continue
            if not is_produto_novo(p['nome'], produto):
                continue
            if not (valor_minimo <= preco <= valor_maximo):
                continue
            ofertas.append({'nome': p['nome'], 'preco': preco, 'link': p['link'], 'imagem': p.get('imagem', ''), 'loja': 'Shopee'})

        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, len(produtos) > 0
    except Exception as e:
        print(f'Erro Shopee: {e}')
        return [], False


def buscar_casas_bahia(produto, valor_minimo, valor_maximo):
    """
    Busca na Casas Bahia via APIs internas (2 requests, sem Akamai):
    1. api-partner-prd.casasbahia.com.br/api/v3/web/busca → lista de produtos + IDs
    2. api.casasbahia.com.br/merchandising/oferta/v1/Preco → preços + parcelamento
    Header necessário: apiKey (exposta no __NEXT_DATA__ da página)
    """
    if _CB_SESSION is None:
        return [], False

    CB_PRICE_API_KEY = 'd081fef8c2c44645bb082712ed32a047'
    CB_COMPOSICAO = 'DescontoFormaPagamento,MelhoresParcelamentos'
    CB_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'Referer': 'https://www.casasbahia.com.br/',
        'Origin': 'https://www.casasbahia.com.br',
        'apiKey': CB_PRICE_API_KEY,
    }

    ofertas = []
    encontrou = False
    try:
        query = urllib.parse.quote_plus(produto.strip())

        # Step 1: Busca
        r_search = _CB_SESSION.get(
            f'https://api-partner-prd.casasbahia.com.br/api/v3/web/busca?Terms={query}&Page=1&PageSize=20',
            headers=CB_HEADERS,
            timeout=20,
        )
        if r_search.status_code != 200:
            return [], False

        search_data = r_search.json()
        products = search_data.get('products', [])
        if not products:
            return [], False

        encontrou = True

        prod_ids = [p['id'] for p in products]
        meta = {p['id']: p for p in products}

        # Step 2: Preços (batch por product IDs)
        params = urllib.parse.urlencode({
            'idsProduto': ','.join(prod_ids),
            'composicao': CB_COMPOSICAO,
        })
        r_price = _CB_SESSION.get(
            f'https://api.casasbahia.com.br/merchandising/oferta/v1/Preco/Produto/PrecoVenda/?{params}',
            headers=CB_HEADERS,
            timeout=20,
        )
        if r_price.status_code != 200:
            return [], encontrou

        price_data = r_price.json()

        for item in price_data.get('PrecoProdutos', []):
            pv = item.get('PrecoVenda', {})
            if not pv:
                continue

            prod_id = str(pv.get('IdProduto', ''))
            info = meta.get(prod_id, {})

            if not info.get('status') == 'AVAILABLE':
                continue
            if not pv.get('DisponibilidadeVenda'):
                continue

            df = item.get('DescontoFormaPagamento', {})
            preco_pix = df.get('PrecoVendaComDesconto') if df.get('PossuiDesconto') else None
            preco = float(preco_pix or pv.get('Preco', 0))
            if preco <= 0:
                continue

            nome = info.get('name', pv.get('IdProduto', ''))
            if not nome_compativel_com_busca(nome, produto):
                continue
            if not is_produto_principal(nome, produto):
                continue
            if not is_produto_novo(nome, produto):
                continue
            if not (valor_minimo <= preco <= valor_maximo):
                continue

            n_parc = pv.get('NumeroParcelas', 0)
            val_parc = pv.get('ValorParcela', 0.0)
            parcelamento = None
            if n_parc and val_parc:
                preco_normal = float(pv.get('Preco', 0))
                total_parc = round(n_parc * val_parc, 2)
                sem_juros = abs(total_parc - preco_normal) < 0.02
                parcelamento = {'parcelas': n_parc, 'valor': float(val_parc), 'sem_juros': sem_juros}

            ofertas.append({
                'nome': nome,
                'preco': preco,
                'link': info.get('url', ''),
                'imagem': info.get('image', ''),
                'loja': 'Casas Bahia',
                'parcelamento': parcelamento,
            })

        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, encontrou

    except Exception as e:
        print(f'Erro Casas Bahia: {e}')
        return [], encontrou


@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

def _resposta_busca_vazia(mensagem=None):
    out = {'kabum': [], 'pichau': [], 'terabyte': [], 'mercadolivre': [], 'magalu': [], 'amazon': [], 'shopee': [], 'casasbahia': [], 'melhores_ofertas': []}
    if mensagem:
        out['erro'] = mensagem
    return jsonify(out)


@app.route('/buscar_todas', methods=['POST'])
def buscar_todas():
    import concurrent.futures as cf

    dados = request.get_json(silent=True)
    if not dados or not str(dados.get('produto', '')).strip():
        return _resposta_busca_vazia('Informe o nome do produto.'), 400

    produto = dados['produto'].strip()
    valor_minimo = dados.get('valor_minimo', None)
    if valor_minimo is None or valor_minimo == '' or valor_minimo is False:
        valor_minimo = 0
    else:
        valor_minimo = float(valor_minimo)
    valor_maximo = dados.get('valor_maximo', None)
    if valor_maximo is None or valor_maximo == '' or valor_maximo is False:
        valor_maximo = float('inf')
    else:
        valor_maximo = float(valor_maximo)
    filtros = dados.get('filtros', {'kabum': True, 'pichau': True, 'terabyte': True, 'mercadolivre': True, 'magalu': True, 'amazon': True, 'shopee': True, 'casasbahia': True})

    result = {}
    todas_ofertas = []
    todas_lojas = ['kabum', 'pichau', 'terabyte', 'mercadolivre', 'magalu', 'amazon', 'shopee', 'casasbahia']
    buscadores = {
        'kabum': buscar_kabum,
        'pichau': buscar_pichau,
        'terabyte': buscar_terabyte,
        'mercadolivre': buscar_mercadolivre,
        'magalu': buscar_magalu,
        'amazon': buscar_amazon,
        'shopee': buscar_shopee,
        'casasbahia': buscar_casas_bahia,
    }

    def verificar_site(resultados, nome_site, encontrou_produtos):
        if resultados:
            return resultados
        if encontrou_produtos:
            return [{'nome': f'⚠️ Nenhuma oferta dentro do valor desejado em {nome_site}.', 'preco': '-', 'link': '#', 'loja': nome_site}]
        return [{'nome': f'⚠️ Problema técnico na busca de {nome_site}. Verifique manualmente.', 'preco': '-', 'link': '#', 'loja': nome_site}]

    try:
        with ThreadPoolExecutor() as executor:
            futures = {
                site: executor.submit(buscadores[site], produto, valor_minimo, valor_maximo)
                for site in todas_lojas
                if filtros.get(site)
            }
            for site in todas_lojas:
                if site not in futures:
                    result.setdefault(site, [])
                    continue
                try:
                    ofertas, encontrou = futures[site].result(timeout=90)
                except cf.TimeoutError:
                    app.logger.warning('Timeout na busca de %s', site)
                    ofertas, encontrou = [], False
                except Exception:
                    app.logger.exception('Erro na thread de busca (%s)', site)
                    ofertas, encontrou = [], False

                for oferta in ofertas:
                    oferta['loja'] = site.capitalize()
                todas_ofertas.extend(ofertas)
                result[site] = verificar_site(ofertas, site.capitalize(), encontrou)

        melhores_ofertas = sorted(
            [o for o in todas_ofertas if o['preco'] != '-'],
            key=lambda x: x['preco']
        )
        result['melhores_ofertas'] = melhores_ofertas

        return jsonify(result)
    except Exception:
        app.logger.exception('buscar_todas')
        return _resposta_busca_vazia('Falha ao processar a busca. Verifique o console do servidor.'), 500

@app.route('/buscar_stream')
def buscar_stream():
    import concurrent.futures as cf

    produto = request.args.get('produto', '').strip()

    def _sse_error(msg):
        def _gen():
            yield f"data: {json.dumps({'type': 'error', 'mensagem': msg})}\n\n"
        return Response(stream_with_context(_gen()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    if not produto:
        return _sse_error('Informe o nome do produto.')

    try:
        valor_minimo = float(request.args.get('valor_minimo') or 0)
    except (ValueError, TypeError):
        valor_minimo = 0.0

    vm_raw = request.args.get('valor_maximo', '').strip()
    try:
        valor_maximo = float(vm_raw) if vm_raw else float('inf')
    except (ValueError, TypeError):
        valor_maximo = float('inf')

    try:
        filtros = json.loads(request.args.get('filtros', '{}'))
    except Exception:
        filtros = {s: True for s in ['kabum', 'pichau', 'terabyte', 'mercadolivre', 'magalu', 'amazon', 'shopee', 'casasbahia']}

    todas_lojas = ['kabum', 'pichau', 'terabyte', 'mercadolivre', 'magalu', 'amazon', 'shopee', 'casasbahia']
    lojas_ativas = [s for s in todas_lojas if filtros.get(s)]

    if not lojas_ativas:
        return _sse_error('Selecione pelo menos uma loja.')

    buscadores = {
        'kabum': buscar_kabum,
        'pichau': buscar_pichau,
        'terabyte': buscar_terabyte,
        'mercadolivre': buscar_mercadolivre,
        'magalu': buscar_magalu,
        'amazon': buscar_amazon,
        'shopee': buscar_shopee,
        'casasbahia': buscar_casas_bahia,
    }

    def _verificar(resultados, nome_site, encontrou):
        if resultados:
            return resultados
        if encontrou:
            return [{'nome': f'⚠️ Nenhuma oferta dentro do valor desejado em {nome_site}.', 'preco': '-', 'link': '#', 'loja': nome_site}]
        return [{'nome': f'⚠️ Problema técnico na busca de {nome_site}. Verifique manualmente.', 'preco': '-', 'link': '#', 'loja': nome_site}]

    def generate():
        todas_ofertas = []
        done_sites = []

        with cf.ThreadPoolExecutor() as executor:
            future_to_site = {
                executor.submit(buscadores[s], produto, valor_minimo, valor_maximo): s
                for s in lojas_ativas
            }

            try:
                for fut in cf.as_completed(future_to_site, timeout=90):
                    site = future_to_site[fut]
                    try:
                        ofertas, encontrou = fut.result()
                    except Exception:
                        app.logger.exception('Erro na busca de %s', site)
                        ofertas, encontrou = [], False

                    for o in ofertas:
                        o['loja'] = site.capitalize()
                    todas_ofertas.extend(ofertas)
                    done_sites.append(site)
                    pending = [s for s in lojas_ativas if s not in done_sites]

                    melhores_parciais = sorted(
                        [o for o in todas_ofertas if o['preco'] != '-'],
                        key=lambda x: x['preco']
                    )[:5]

                    evento = {
                        'type': 'store',
                        'store': site,
                        'ofertas': _verificar(ofertas, site.capitalize(), encontrou),
                        'done_sites': done_sites[:],
                        'pending_sites': pending,
                        'melhores_parciais': melhores_parciais,
                    }
                    yield f"data: {json.dumps(evento)}\n\n"

            except cf.TimeoutError:
                for fut, site in future_to_site.items():
                    if site not in done_sites:
                        evt = {
                            'type': 'store',
                            'store': site,
                            'ofertas': [{'nome': f'⚠️ Timeout na busca de {site.capitalize()}. Tente novamente.', 'preco': '-', 'link': '#', 'loja': site}],
                            'done_sites': done_sites[:],
                            'pending_sites': [],
                            'melhores_parciais': [],
                        }
                        yield f"data: {json.dumps(evt)}\n\n"

        melhores_finais = sorted(
            [o for o in todas_ofertas if o['preco'] != '-'],
            key=lambda x: x['preco']
        )
        yield f"data: {json.dumps({'type': 'done', 'melhores_ofertas': melhores_finais})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


# ---------------------------------------------------------------------------
# Rotas — Watchlist
# ---------------------------------------------------------------------------

@app.route('/watchlist', methods=['GET'])
def wl_get():
    wl = _wl_load()
    for item in wl['items']:
        item['_stale'] = _wl_is_stale(item)
    return jsonify(wl)


@app.route('/watchlist', methods=['POST'])
def wl_add():
    dados = request.get_json(silent=True) or {}
    link = str(dados.get('link') or '').strip()
    query = str(dados.get('query') or dados.get('nome') or '').strip()
    if not query:
        return jsonify({'erro': 'Informe o nome do produto.'}), 400
    wl = _wl_load()
    # Dedup: link-based items match by exact URL; query-based match by query text
    if link:
        existing = next((i for i in wl['items'] if i.get('link') == link), None)
    else:
        existing = next((i for i in wl['items'] if i['query'].lower() == query.lower() and not i.get('link')), None)
    if existing:
        return jsonify({'ok': True, 'item': existing, 'duplicate': True})
    item = {
        'id': uuid.uuid4().hex[:8],
        'query': query,
        'link': link or None,
        'lojas': dados.get('lojas', {'kabum': True, 'pichau': True, 'terabyte': True, 'mercadolivre': True, 'magalu': True, 'amazon': True, 'shopee': True, 'casasbahia': True}),
        'valor_minimo': dados.get('valor_minimo', 0),
        'valor_maximo': dados.get('valor_maximo', None),
        'notificar_preco_baixo': dados.get('notificar_preco_baixo', True),
        'notificar_valor_alvo': dados.get('notificar_valor_alvo', None),
        'adicionado_em': datetime.now(timezone.utc).isoformat(),
        'ultima_busca': None,
        'melhor_preco': None,
        'historico': [],
    }
    wl['items'].append(item)
    _wl_save(wl)
    return jsonify({'ok': True, 'item': item})


@app.route('/watchlist/<item_id>', methods=['PUT'])
def wl_edit(item_id):
    dados = request.get_json(silent=True) or {}
    query = str(dados.get('query') or '').strip()
    if not query:
        return jsonify({'erro': 'Informe o nome do produto.'}), 400
    wl = _wl_load()
    item = next((i for i in wl['items'] if i['id'] == item_id), None)
    if not item:
        return jsonify({'erro': 'Produto não encontrado.'}), 404
    item['query'] = query
    item['valor_minimo'] = dados.get('valor_minimo', 0)
    item['valor_maximo'] = dados.get('valor_maximo', None)
    item['lojas'] = dados.get('lojas', item.get('lojas', {}))
    item['notificar_preco_baixo'] = dados.get('notificar_preco_baixo', item.get('notificar_preco_baixo', True))
    item['notificar_valor_alvo'] = dados.get('notificar_valor_alvo', item.get('notificar_valor_alvo', None))
    _wl_save(wl)
    return jsonify({'ok': True, 'item': item})


@app.route('/watchlist/reorder', methods=['POST'])
def wl_reorder():
    dados = request.get_json(silent=True) or {}
    ids = dados.get('ids', [])
    if not ids:
        return jsonify({'erro': 'ids required'}), 400
    wl = _wl_load()
    # Build lookup for fast access
    item_map = {i['id']: i for i in wl['items']}
    # Reorder: put known ids first in given order, then any remaining items
    reordered = [item_map[id_] for id_ in ids if id_ in item_map]
    extras = [i for i in wl['items'] if i['id'] not in {id_ for id_ in ids}]
    wl['items'] = reordered + extras
    _wl_save(wl)
    return jsonify({'ok': True})


@app.route('/watchlist/<item_id>', methods=['DELETE'])
def wl_remove(item_id):
    wl = _wl_load()
    wl['items'] = [i for i in wl['items'] if i['id'] != item_id]
    _wl_save(wl)
    return jsonify({'ok': True})


@app.route('/watchlist/update/<item_id>')
def wl_update_one(item_id):
    wl = _wl_load()
    item = next((i for i in wl['items'] if i['id'] == item_id), None)

    def _sse_err(msg):
        def _g():
            yield f"data: {json.dumps({'type': 'error', 'mensagem': msg})}\n\n"
        return Response(stream_with_context(_g()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    if not item:
        return _sse_err('Item não encontrado.')

    def generate():
        import concurrent.futures as cf
        yield f"data: {json.dumps({'type': 'start', 'id': item_id, 'query': item['query']})}\n\n"

        # Link-based: simple fetch, no per-store streaming
        if item.get('link'):
            best, por_loja = _wl_buscar_item(item)
            trend = _wl_salvar_resultado(item_id, best)
            yield f"data: {json.dumps({'type': 'done', 'id': item_id, 'melhor_preco': best, 'trend': trend, 'todos_resultados': por_loja})}\n\n"
            return

        # Query-based: stream per-store progress (same format as /buscar_stream)
        buscadores = {
            'kabum': buscar_kabum, 'pichau': buscar_pichau,
            'terabyte': buscar_terabyte, 'mercadolivre': buscar_mercadolivre,
            'magalu': buscar_magalu, 'amazon': buscar_amazon, 'shopee': buscar_shopee,
            'casasbahia': buscar_casas_bahia,
        }
        lojas = [s for s, v in (item.get('lojas') or {}).items() if v and s in buscadores]
        vm = float(item.get('valor_minimo') or 0)
        vmax_raw = item.get('valor_maximo')
        vmax = float(vmax_raw) if vmax_raw else float('inf')
        query = item.get('query', '')

        por_loja = {}
        todas_ofertas = []
        done_sites = []
        pending = list(lojas)

        with cf.ThreadPoolExecutor() as executor:
            fut_map = {executor.submit(buscadores[s], query, vm, vmax): s for s in lojas}
            try:
                for fut in cf.as_completed(fut_map, timeout=90):
                    site = fut_map[fut]
                    pending = [s for s in pending if s != site]
                    done_sites.append(site)
                    try:
                        ofertas, _ = fut.result()
                        for o in ofertas:
                            o['loja'] = site.capitalize()
                        por_loja[site] = ofertas
                        todas_ofertas.extend(ofertas)
                    except Exception:
                        por_loja[site] = []
                        ofertas = []
                    validas_p = [o for o in todas_ofertas if isinstance(o.get('preco'), (int, float)) and o['preco'] > 0]
                    melhores_p = sorted(validas_p, key=lambda x: x['preco'])[:5]
                    yield f"data: {json.dumps({'type': 'store', 'store': site, 'ofertas': por_loja[site], 'done_sites': done_sites, 'pending_sites': pending, 'melhores_parciais': melhores_p})}\n\n"
            except cf.TimeoutError:
                pass

        validas = [o for o in todas_ofertas if isinstance(o.get('preco'), (int, float)) and o['preco'] > 0]
        best = min(validas, key=lambda x: x['preco']) if validas else None
        trend = _wl_salvar_resultado(item_id, best)
        melhores = sorted(validas, key=lambda x: x['preco'])
        yield f"data: {json.dumps({'type': 'done', 'id': item_id, 'melhor_preco': best, 'trend': trend, 'todos_resultados': por_loja, 'melhores_ofertas': melhores})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/watchlist/update_all')
def wl_update_all():
    wl = _wl_load()
    force = request.args.get('force', '0') == '1'
    items = wl['items'] if force else [i for i in wl['items'] if _wl_is_stale(i)]

    def generate():
        if not items:
            yield f"data: {json.dumps({'type': 'done', 'total': 0, 'updated': 0})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'total': len(items)})}\n\n"

        for idx, item in enumerate(items):
            yield f"data: {json.dumps({'type': 'progress', 'id': item['id'], 'query': item['query'], 'idx': idx, 'total': len(items)})}\n\n"
            best, por_loja = _wl_buscar_item(item)
            trend = _wl_salvar_resultado(item['id'], best)
            yield f"data: {json.dumps({'type': 'result', 'id': item['id'], 'query': item['query'], 'melhor_preco': best, 'trend': trend, 'todos_resultados': por_loja})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'total': len(items), 'updated': len(items)})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})


@app.route('/telegram/config', methods=['GET'])
def tg_config_get():
    cfg = _tg_load()
    # Never return the full token to the frontend — mask it
    token = cfg.get('token', '')
    masked = ('*' * (len(token) - 6) + token[-6:]) if len(token) > 6 else ('*' * len(token))
    return jsonify({'configured': bool(token and cfg.get('chat_id')),
                    'token_masked': masked,
                    'chat_id': cfg.get('chat_id', '')})


@app.route('/telegram/config', methods=['POST'])
def tg_config_save():
    body = request.get_json(force=True) or {}
    token = (body.get('token') or '').strip()
    chat_id = (body.get('chat_id') or '').strip()
    if not token or not chat_id:
        return jsonify({'ok': False, 'error': 'Token e Chat ID são obrigatórios.'}), 400
    _tg_save({'token': token, 'chat_id': chat_id})
    return jsonify({'ok': True})


@app.route('/telegram/config', methods=['DELETE'])
def tg_config_delete():
    _tg_save({'token': '', 'chat_id': ''})
    return jsonify({'ok': True})


@app.route('/telegram/test', methods=['POST'])
def tg_test():
    cfg = _tg_load()
    body = request.get_json(force=True) or {}
    token = (body.get('token') or cfg.get('token', '')).strip()
    chat_id = (body.get('chat_id') or cfg.get('chat_id', '')).strip()
    if not token or not chat_id:
        return jsonify({'ok': False, 'error': 'Configure token e chat_id antes de testar.'}), 400
    ok, err = _tg_send(token, chat_id,
                       '✅ <b>TechOfertas</b>\n\nNotificações do Telegram configuradas com sucesso!')
    if ok:
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': err}), 502


@app.route('/telegram/detect_chat_id', methods=['POST'])
def tg_detect_chat_id():
    body = request.get_json(force=True) or {}
    token = (body.get('token') or '').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'Token não informado.'}), 400
    url = f'https://api.telegram.org/bot{token}/getUpdates?limit=100'
    req = urllib.request.Request(url, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode(errors='replace')
        try:
            msg = json.loads(body_err).get('description', body_err)
        except Exception:
            msg = body_err
        return jsonify({'ok': False, 'error': f'Telegram recusou: {msg}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 502

    if not data.get('ok'):
        return jsonify({'ok': False, 'error': data.get('description', 'Token inválido.')}), 400

    results = data.get('result', [])
    if not results:
        return jsonify({
            'ok': False,
            'error': 'Nenhuma mensagem encontrada. Abra o Telegram, encontre seu bot e envie qualquer mensagem (ex: "oi"), depois clique em Detectar novamente.'
        }), 404

    # Percorre updates do mais recente para o mais antigo procurando qualquer chat
    chat_id = None
    for update in reversed(results):
        for key in ('message', 'edited_message', 'channel_post', 'my_chat_member', 'chat_member'):
            entry = update.get(key)
            if entry and entry.get('chat', {}).get('id'):
                chat_id = str(entry['chat']['id'])
                break
        if chat_id:
            break

    if not chat_id:
        return jsonify({'ok': False, 'error': 'Não foi possível extrair o Chat ID. Tente enviar outra mensagem ao bot.'}), 404

    return jsonify({'ok': True, 'chat_id': chat_id})


import sys
import webbrowser
from threading import Timer

def abrir_navegador():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == '__main__':
    frozen = getattr(_sys, 'frozen', False)
    debug = not frozen

    if frozen or "--abrir" in sys.argv:
        if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            Timer(1, abrir_navegador).start()

    app.run(debug=debug)

