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

app = Flask(__name__)

@app.route('/logo2.png')
def serve_logo():
    return send_file(Path(__file__).parent / 'logo2.png', mimetype='image/png')

# ---------------------------------------------------------------------------
# Watchlist — persistência local em JSON
# ---------------------------------------------------------------------------
WATCHLIST_PATH = Path(__file__).parent / 'watchlist.json'


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
    for it in wl['items']:
        if it['id'] == item_id:
            old = it.get('melhor_preco')
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
            break
    _wl_save(wl)
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
            font-size: 1.5rem;
            width: 2.5rem;
            height: 2.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: var(--radius-lg);
            color: white;
            font-weight: bold;
        }

        .store-icon.kabum { background: #ff6b35; }
        .store-icon.pichau { background: #dc2626; }
        .store-icon.terabyte { background: #334155; }
        .store-icon.mercadolivre { background: #f3a00e; }
        .store-icon.melhores { background: var(--accent-color); }
        [data-theme="dark"] .store-icon.terabyte { background: #475569; }

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
             font-size: 0.8rem;
             color: var(--text-secondary);
             font-weight: 500;
         }

                 .product-actions {
             display: flex;
             gap: 0.5rem;
             margin-top: 0.25rem;
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
        }

        @media (max-width: 480px) {
            .main-content {
                padding: 1rem;
            }

            .search-card {
                padding: 1rem;
                margin: 0.5rem;
            }

            .store-header {
                flex-direction: column;
                gap: 1rem;
                text-align: center;
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
        .watchlist-actions { display: flex; gap: 0.75rem; align-items: center; flex-shrink: 0; }
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
            grid-template-columns: 36px 1fr 88px 152px 82px;
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
        .compact-installment { font-size: 0.72rem; color: var(--text-secondary); text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 0.1rem; }
        .compact-melhor-compra { font-size: 0.6rem; color: #4338ca; background: linear-gradient(135deg,#dbeafe,#ede9fe); border: 1px solid rgba(99,102,241,0.25); border-radius: 999px; padding: 0.05rem 0.35rem; font-weight: 600; cursor: default; }
        [data-theme="dark"] .compact-melhor-compra { background: linear-gradient(135deg,rgb(99 102 241 / 0.18),rgb(139 92 246 / 0.18)); color: #a5b4fc; border-color: rgba(129,140,248,0.3); }
        .compact-installment .badge-sem-juros, .compact-installment .badge-com-juros, .compact-installment .badge-melhor-compra { font-size: 0.6rem; padding: 0.05rem 0.3rem; }
        .compact-actions { display: flex; gap: 0.35rem; flex-shrink: 0; }
        .compact-actions .btn-sm { padding: 0.3rem 0.6rem; font-size: 0.75rem; }
        .compact-actions .btn-watch { padding: 0.28rem 0.45rem; font-size: 0.72rem; }

        .compact-header-row {
            display: grid; grid-template-columns: 36px 1fr 88px 152px 82px;
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
                    <img src="/logo2.png" alt="TechOfertas" style="height:70px;">
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
                                <label for="kabum" class="store-label">🟠 KaBuM!</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="pichau" class="store-checkbox" value="pichau" checked>
                                <label for="pichau" class="store-label">🔴 Pichau</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="terabyte" class="store-checkbox" value="terabyte" checked>
                                <label for="terabyte" class="store-label">⚫ Terabyte</label>
                            </div>
                            <div class="store-option">
                                <input type="checkbox" id="mercadolivre" class="store-checkbox" value="mercadolivre" checked>
                                <label for="mercadolivre" class="store-label">🟡 Mercado Livre</label>
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
                        <p class="results-count" id="total-count"></p>
                    </div>
                    <div class="layout-toggle">
                        <span class="layout-toggle-label">Visualização</span>
                        <button id="layout-btn-compact" onclick="setLayout('compact')" title="Layout compacto em lista">&#9776; Lista</button>
                        <button id="layout-btn-default" onclick="setLayout('default')" title="Layout em cards">&#9638; Cards</button>
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
            <div class="form-group" style="margin-top:1rem">
                <label class="form-label">Preço máximo (R$)</label>
                <input type="number" id="watch-valor-max" class="form-input" placeholder="Sem limite" min="0" step="0.01" />
            </div>
            <div class="modal-stores" style="margin-top:1rem">
                <label class="form-label" style="display:block;margin-bottom:0.5rem">Lojas</label>
                <div class="stores-grid">
                    <div class="store-option"><input type="checkbox" id="wl-kabum" class="store-checkbox wl-store-checkbox" value="kabum" checked><label for="wl-kabum" class="store-label">🟠 KaBuM!</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-pichau" class="store-checkbox wl-store-checkbox" value="pichau" checked><label for="wl-pichau" class="store-label">🔴 Pichau</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-terabyte" class="store-checkbox wl-store-checkbox" value="terabyte" checked><label for="wl-terabyte" class="store-label">⚫ Terabyte</label></div>
                    <div class="store-option"><input type="checkbox" id="wl-mercadolivre" class="store-checkbox wl-store-checkbox" value="mercadolivre" checked><label for="wl-mercadolivre" class="store-label">🟡 Mercado Livre</label></div>
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
            <div class="form-group" style="margin-top:1rem">
                <label class="form-label">Valor Mínimo (R$)</label>
                <input type="number" id="edit-watch-valor-min" class="form-input" placeholder="Sem limite mínimo" min="0" step="0.01" />
            </div>
            <div class="form-group" style="margin-top:1rem">
                <label class="form-label">Valor Máximo (R$)</label>
                <input type="number" id="edit-watch-valor-max" class="form-input" placeholder="Sem limite máximo" min="0" step="0.01" />
            </div>
            <div class="modal-stores" style="margin-top:1rem">
                <label class="form-label" style="display:block;margin-bottom:0.5rem">Lojas</label>
                <div class="stores-grid">
                    <div class="store-option"><input type="checkbox" id="edit-wl-kabum" class="store-checkbox" value="kabum"><label for="edit-wl-kabum" class="store-label">🟠 KaBuM!</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-pichau" class="store-checkbox" value="pichau"><label for="edit-wl-pichau" class="store-label">🔴 Pichau</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-terabyte" class="store-checkbox" value="terabyte"><label for="edit-wl-terabyte" class="store-label">⚫ Terabyte</label></div>
                    <div class="store-option"><input type="checkbox" id="edit-wl-mercadolivre" class="store-checkbox" value="mercadolivre"><label for="edit-wl-mercadolivre" class="store-label">🟡 Mercado Livre</label></div>
                </div>
            </div>
            <div style="display:flex;gap:0.75rem;margin-top:1.5rem">
                <button class="btn btn-primary" style="flex:1" onclick="submitEditWatch()">Salvar</button>
                <button class="btn btn-outline" onclick="closeEditWatchModal()">Cancelar</button>
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

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            updateThemeButtons(localStorage.getItem('theme') || 'dark');

            setupEventListeners();
            loadSearchCache();
            setupScrollToTop();
            checkAutoUpdate();
            _restoreSession();
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
        let _currentLayout = localStorage.getItem('techofertas_layout') || 'compact';
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
                const melhorCompra = (p.sem_juros && Math.abs(totalNum - product.preco) < 0.02)
                    ? '<div class="compact-melhor-compra" title="O total parcelado é igual ao preço à vista — você não paga nada a mais parcelando!">=&nbsp;vista</div>' : '';
                return `<div>${p.parcelas}x R$${val} ${badge}</div>${melhorCompra}`;
            })() : '<span style="color:var(--text-muted)">--</span>';
            const imgHTML = product.imagem
                ? `<img src="${product.imagem}" alt="${product.nome}" class="compact-img" onclick="openImageModal('${product.imagem}')">`
                : '<div class="compact-img-placeholder"></div>';
            return `
                <div class="product-card compact">
                    ${imgHTML}
                    <div class="compact-name" title="${product.nome}">${product.nome}</div>
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

            autoUpdatePaused = true; // pause background auto-updates during manual search
            showResultsSection();

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
                    const titulo = `${getStoreIcon(data.store)} ${getStoreDisplayName(data.store)}`;
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
                            <div class="store-icon ${store}">${getStoreIcon(store)}</div>
                            <div class="store-details">
                                <h3>${getStoreIcon(store)} ${getStoreDisplayName(store)}</h3>
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
                 
                 resultsHTML += createStoreSection('melhores', '⭐ Melhores Ofertas', bestOffers, 'melhores');
             }

             // Add individual store sections (ordem fixa para consistência)
             const storeOrder = ['kabum', 'pichau', 'terabyte', 'mercadolivre'];
             const orderedStores = storeOrder.filter(s => selectedStores.includes(s))
                 .concat(selectedStores.filter(s => !storeOrder.includes(s)));
             orderedStores.forEach(store => {
                 const storeData = data[store] || [];
                 const validProducts = storeData.filter(product => product.preco !== '-');
                 totalProducts += validProducts.length;

                 const storeName = getStoreDisplayName(store);
                 const storeIcon = getStoreIcon(store);
                 
                 resultsHTML += createStoreSection(store, `${storeIcon} ${storeName}`, storeData, store);
             });

             // Update results
             storeResults.innerHTML = resultsHTML;
             // CORREÇÃO PROBLEMA 2: Exibir contagem apenas após carregar novos resultados
             totalCount.textContent = `${totalProducts} produto${totalProducts !== 1 ? 's' : ''} encontrado${totalProducts !== 1 ? 's' : ''}`;

             // Setup store toggles
             setupStoreToggles();
         }

        function createStoreSection(storeId, title, products, storeType) {
            const validProducts = products.filter(product => product.preco !== '-');
            const hasProducts = validProducts.length > 0;
            
            return `
                <div class="store-card" id="store-${storeId}">
                    <div class="store-header" onclick="toggleStore('${storeId}')">
                        <div class="store-info">
                            <div class="store-icon ${storeType}">${getStoreIcon(storeType)}</div>
                            <div class="store-details">
                                <h3>${title}</h3>
                                <p class="store-count">${validProducts.length} produto${validProducts.length !== 1 ? 's' : ''}</p>
                            </div>
                        </div>
                        <button class="store-toggle" id="toggle-${storeId}">
                            <span id="icon-${storeId}">▼</span>
                        </button>
                    </div>
                    <div class="store-content" id="content-${storeId}">
                        <div class="products-grid${_currentLayout === 'compact' ? ' compact' : ''}">
                            ${_currentLayout === 'compact' ? '<div class="compact-header-row"><span></span><span>Produto</span><span>Preço</span><span>Parcelamento</span><span></span></div>' : ''}
                            ${hasProducts ? validProducts.map(product => _currentLayout === 'compact' ? createProductCardCompact(product) : createProductCard(product)).join('') : createEmptyState(storeType)}
                        </div>
                    </div>
                </div>
            `;
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
                const total = totalNum.toLocaleString('pt-BR', {minimumFractionDigits: 2});
                const badge = p.sem_juros
                    ? '<span class="badge-sem-juros">sem juros</span>'
                    : '<span class="badge-com-juros">com juros</span>';
                const melhorCompra = (p.sem_juros && Math.abs(totalNum - product.preco) < 0.02)
                    ? ' <span class="badge-melhor-compra">mesmo preço à vista</span>'
                    : '';
                return `<div class="product-installment">💳 R$ ${total} &nbsp;·&nbsp; ${p.parcelas}x de R$ ${val} ${badge}${melhorCompra}</div>`;
            })() : '';

            return `
                <div class="product-card">
                    ${product.imagem ? `
                        <img src="${product.imagem}" alt="${product.nome}" class="product-image" onclick="openImageModal('${product.imagem}')">
                    ` : ''}
                    <div class="product-details">
                        <h4 class="product-name">${product.nome}</h4>
                        <div>
                            <div class="product-price-label">à vista</div>
                            <div class="product-price">R$ ${product.preco.toLocaleString('pt-BR', {minimumFractionDigits: 2})}</div>
                        </div>
                        ${installmentHTML}
                        ${product.loja ? `<div class="product-store">Loja: ${getStoreDisplayName(product.loja)}</div>` : ''}
                        <div class="product-actions">
                            <button class="btn btn-primary btn-sm" onclick="openProduct('${product.link}')">
                                Ver Produto
                            </button>
                            <button class="btn-watch" onclick='openWatchConfirmModal(${wcId})'>👁️ Acompanhar preço</button>
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
                'melhores': 'Melhores Ofertas'
            };
            return names[store] || store;
        }

        function getStoreIcon(store) {
            const icons = {
                'kabum': '🟠',
                'pichau': '🔴',
                'terabyte': '⚫',
                'mercadolivre': '🟡',
                'Mercadolivre': '🟡',
                'melhores': '⭐'
            };
            return icons[store] || '🏪';
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
            if (data.melhor !== undefined) item.melhor_preco = data.melhor;
            if (data.historico) item.historico = data.historico;
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

        // Display cached results in search view
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


            // Compute melhores from cached data
            const todas = Object.values(resultados).flat();
            const validas = todas.filter(o => typeof o.preco === 'number' && o.preco > 0);
            const melhores = validas.sort((a, b) => a.preco - b.preco).slice(0, 5);
            displayResults({ ...resultados, melhores_ofertas: melhores });

            let total = 0;
            document.querySelectorAll('[id^="content-"]').forEach(el => {
                if (el.id !== 'content-melhores') total += el.querySelectorAll('.product-card').length;
            });
            const tsLabel = item.ultima_busca ? formatTimeSince(item.ultima_busca) : '';
            totalCount.textContent = total > 0
                ? `${total} produto${total !== 1 ? 's' : ''} encontrado${total !== 1 ? 's' : ''}${tsLabel ? ' · ' + tsLabel : ''}`
                : '';
        }

        function updateWatchItem(id) {
            // Nunca iniciar duas buscas simultâneas para o mesmo produto
            if (_updatingIds.has(id)) return;

            const item = watchlistData.find(i => i.id === id);
            if (!item) return;

            if (item.link) {
                // Link-based: background update — pode coexistir com outros simples
                _updatingIds.add(id);
                _updateWatchSimple(id, item);
            } else {
                // Query-based: ocupa o watchUpdateSource global
                // Se updateAll está rodando, cancela e inicia o item específico
                if (watchUpdateSource) { watchUpdateSource.close(); watchUpdateSource = null; }
                _updateAllRunning = false;
                wlCacheClear(id);
                _updatingIds.add(id);
                _updateWatchStream(id, item);
            }
        }

        function _updateWatchSimple(id, item) {
            const el = document.getElementById(`wi-${id}`);
            if (el) {
                el.classList.add('updating');
                const actEl = el.querySelector('.watch-item-actions');
                if (actEl && !actEl.querySelector('.watch-spinner')) {
                    const sp = document.createElement('div'); sp.className = 'watch-spinner'; actEl.prepend(sp);
                }
            }
            const src = new EventSource(`/watchlist/update/${id}`);
            src.onmessage = function(evt) {
                let data; try { data = JSON.parse(evt.data); } catch(e) { return; }
                if (data.type === 'done' || data.type === 'error') {
                    src.close();
                    _updatingIds.delete(id);
                    if (data.type === 'error') {
                        showNotification(data.mensagem || 'Erro ao atualizar.', 'error');
                    } else {
                        _applyUpdateResult(id, data);
                        renderWatchlist();
                    }
                    loadWatchlist();
                    scheduleNextAutoUpdate();
                }
            };
            src.onerror = function() {
                src.close();
                _updatingIds.delete(id);
                loadWatchlist();
                scheduleNextAutoUpdate();
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
                        skCard.outerHTML = createStoreSection(data.store, `${getStoreIcon(data.store)} ${getStoreDisplayName(data.store)}`, data.ofertas, data.store);
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
            fetch(`/watchlist/${id}`, {method: 'DELETE'})
                .then(r => r.json())
                .then(() => { loadWatchlist(); showNotification('Produto removido.', 'info'); })
                .catch(() => showNotification('Erro ao remover.', 'error'));
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

        function showAddWatchModal() {
            document.getElementById('add-watch-modal').style.display = 'flex';
            setTimeout(() => document.getElementById('watch-nome').focus(), 50);
        }

        function closeAddWatchModal() {
            document.getElementById('add-watch-modal').style.display = 'none';
            document.getElementById('watch-nome').value = '';
            document.getElementById('watch-valor-max').value = '';
        }

        let _editingWatchId = null;

        function openEditWatchModal(id) {
            const item = watchlistData.find(i => i.id === id);
            if (!item) return;
            _editingWatchId = id;
            document.getElementById('edit-watch-nome').value = item.query || '';
            document.getElementById('edit-watch-valor-min').value = item.valor_minimo > 0 ? item.valor_minimo : '';
            document.getElementById('edit-watch-valor-max').value = item.valor_maximo != null ? item.valor_maximo : '';
            const lojas = item.lojas || {};
            ['kabum','pichau','terabyte','mercadolivre'].forEach(s => {
                const cb = document.getElementById(`edit-wl-${s}`);
                if (cb) cb.checked = lojas[s] !== false;
            });
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
            const lojas = {};
            ['kabum','pichau','terabyte','mercadolivre'].forEach(s => {
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
                    lojas
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
            ['kabum','pichau','terabyte','mercadolivre'].forEach(s => {
                const cb = document.getElementById(`wl-${s}`);
                lojas[s] = cb ? cb.checked : true;
            });
            const vmRaw = document.getElementById('watch-valor-max').value;
            fetch('/watchlist', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query, lojas, valor_maximo: vmRaw ? parseFloat(vmRaw) : null}),
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
                if (autoUpdatePaused || watchUpdateSource || _updateAllRunning || _updatingIds.size > 0) {
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
        items = (
            payload.get('props', {})
            .get('pageProps', {})
            .get('data', {})
            .get('catalogServer', {})
            .get('data')
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


def _pichau_parse_html(html, vistos, produto, valor_minimo, valor_maximo):
    """Extrai ofertas de uma página de listagem da Pichau."""
    result = []
    partes = re.split(r'(?=<a\s[^>]*data-cy="list-product")', html)
    for parte in partes[1:]:
        hm = re.search(r'data-cy="list-product"\s+href="([^"]+)"', parte)
        if not hm:
            continue
        href = hm.group(1)
        link = 'https://www.pichau.com.br' + href if href.startswith('/') else href
        if link in vistos:
            continue
        h2m = re.search(r'<h2[^>]*>([^<]+)</h2>', parte)
        if not h2m:
            continue
        nome = unescape(re.sub(r'\s+', ' ', h2m.group(1).strip()))
        # Pichau usa PIX como preço à vista (classe pixPrice). Fallback: price_vista (legado).
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
        if not (valor_minimo <= preco_valor <= valor_maximo):
            continue
        imgm = re.search(r'<img[^>]+src="(https://media\.pichau\.com\.br[^"]+)"', parte)
        imagem = imgm.group(1) if imgm else None
        parcelamento = None
        # Converte o bloco para texto puro (resolve tags React fragmentadas)
        texto_bloco = re.sub(r'<!--.*?-->', '', parte, flags=re.DOTALL)  # remove React comments
        texto_bloco = re.sub(r'<[^>]+>', ' ', texto_bloco)
        texto_bloco = re.sub(r'\s+', ' ', texto_bloco)
        # "em até 12 x de R$ 186.27 sem juros no cartão"
        # \b garante início de número inteiro (evita casar "2" dentro de "12")
        im = re.search(
            r'\b([2-9]|[1-9]\d+)\s*x\s*(?:de\s*)?R\$\s*([\d,.]+)(?:[^0-9]{0,60}?(sem juros))?',
            texto_bloco, re.IGNORECASE,
        )
        if im:
            n_parc = int(im.group(1))
            val_parc = formatar_preco(im.group(2))
            if val_parc:
                parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': bool(im.group(3))}
        vistos.add(link)
        result.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})
    return result


def buscar_pichau(produto, valor_minimo, valor_maximo):
    ofertas = []
    vistos = set()
    try:
        q = urllib.parse.quote(produto.strip())
        url = f'https://www.pichau.com.br/search?q={q}'
        scraper = _nova_sessao_scraper()

        html = _scraper_get(scraper, url, 'https://www.pichau.com.br/')
        encontrou = 'data-cy="list-product"' in html
        ofertas.extend(_pichau_parse_html(html, vistos, produto, valor_minimo, valor_maximo))

        html2 = _scraper_get(scraper, f'{url}&page=2', url)
        if html2:
            ofertas.extend(_pichau_parse_html(html2, vistos, produto, valor_minimo, valor_maximo))

        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, len(ofertas) > 0 or encontrou
    except Exception as e:
        print(f'Erro Pichau: {e}')
        return [], False


def buscar_terabyte(produto, valor_minimo, valor_maximo):
    """Abre cada produto individualmente (listagem é JS-rendered — preços não confiáveis)."""
    ofertas = []
    encontrou_produtos = False
    try:
        q = urllib.parse.quote_plus(produto.strip())
        url_busca = f'https://www.terabyteshop.com.br/busca?str={q}'

        scraper = _nova_sessao_scraper()
        html_p1 = _scraper_get(scraper, url_busca, 'https://www.terabyteshop.com.br/')
        html_p2 = _scraper_get(scraper, f'{url_busca}&pagina=2', url_busca) if html_p1 else ''
        html_total = html_p1 + html_p2

        raw = re.findall(
            r'href="(https://www\.terabyteshop\.com\.br/produto/\d+/[^"?#]+)',
            html_total,
        )
        links = list(dict.fromkeys(raw))
        encontrou_produtos = bool(links)

        if not links:
            return [], False

        max_produtos = 20
        links = links[:max_produtos]
        job = (links, url_busca, produto, valor_minimo, valor_maximo, scraper)
        ofertas = _terabyte_processar_links_sequencial(job)
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


def buscar_mercadolivre(produto, valor_minimo, valor_maximo):
    """Scraping do site lista.mercadolivre.com.br (API pública retorna 403)."""
    ofertas = []
    try:
        slug = urllib.parse.quote(re.sub(r'\s+', '-', produto.strip().lower()), safe='-')
        url = f'https://lista.mercadolivre.com.br/{slug}'
        if valor_minimo > 0 or valor_maximo != float('inf'):
            price_min = int(valor_minimo) if valor_minimo > 0 else ''
            price_max = int(valor_maximo) if valor_maximo != float('inf') else ''
            url += f'_PriceRange_{price_min}BRL-{price_max}BRL'

        html = http_get(url, referer='https://www.mercadolivre.com.br/')
        blocos = re.split(r'(?=<li[^>]*class="[^"]*ui-search-layout__item[^"]*")', html)
        encontrou = len(blocos) > 1

        for bloco in blocos[1:]:
            # Nome
            nm = re.search(r'class="poly-component__title"[^>]*>([^<]+)<', bloco)
            if not nm:
                continue
            nome = unescape(re.sub(r'\s+', ' ', nm.group(1).strip()))

            # Filtra produtos que não batem com as especificações da busca (ex: 8GB em vez de 16GB)
            if not _ml_nome_bate_query(produto, nome):
                continue

            # Link — pega o href completo e limpa tracking/fragment depois
            lm = re.search(r'class="poly-component__title[^"]*">\s*<a[^>]*href="([^"]+)"', bloco)
            if not lm:
                continue
            link = lm.group(1).split('#')[0].split('?')[0]
            if not link.startswith('https://'):
                continue

            # Preço atual: remove preço antigo (<s>), pega "Agora: X reais" ou "X reais"
            bloco_sem_old = re.sub(r'<s\b[^>]*>.*?</s>', '', bloco, flags=re.DOTALL)
            pm = re.search(
                r'aria-label="(?:Agora:\s*)?([\d.,]+) reais(?:\s+com\s+(\d+)\s+centavos)?"',
                bloco_sem_old,
            )
            if not pm:
                continue
            preco_str = pm.group(1).replace('.', '').replace(',', '.')
            if pm.group(2):
                preco_str += f'.{pm.group(2).zfill(2)}'
            try:
                preco_valor = float(preco_str)
            except ValueError:
                continue

            if not (valor_minimo <= preco_valor <= valor_maximo):
                continue

            # Imagem
            imgm = re.search(r'<img[^>]*class="poly-component__picture"[^>]*src="([^"]+)"', bloco)
            imagem = imgm.group(1) if imgm else None

            # Parcelamento
            parcelamento = None
            inst_block = re.search(
                r'class="poly-price__installments"[^>]*>(.*?)(?=<div\b|</li>)',
                bloco_sem_old, re.DOTALL,
            )
            if inst_block:
                txt_inst = re.sub(r'<[^>]+>', ' ', inst_block.group(1))
                txt_inst = re.sub(r'\s+', ' ', txt_inst).strip()
                # Ex: "12x R$ 461 , 26" ou "10x R$ 879 , 92 sem juros"
                m_inst = re.match(r'(\d+)x\s*R\$\s*([\d\s,]+?)(?:\s+(sem juros))?$', txt_inst, re.IGNORECASE)
                if m_inst:
                    n_parc = int(m_inst.group(1))
                    val_str = m_inst.group(2).replace(' ', '').replace(',', '.')
                    try:
                        val_parc = float(val_str)
                        sem_juros = bool(m_inst.group(3))
                        parcelamento = {'parcelas': n_parc, 'valor': val_parc, 'sem_juros': sem_juros}
                    except ValueError:
                        pass

            ofertas.append({'nome': nome, 'preco': preco_valor, 'link': link, 'imagem': imagem, 'parcelamento': parcelamento})

        ofertas.sort(key=lambda x: x['preco'])
        return ofertas, encontrou
    except Exception as e:
        print(f'Erro Mercado Livre: {e}')
        return [], False




@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

def _resposta_busca_vazia(mensagem=None):
    out = {'kabum': [], 'pichau': [], 'terabyte': [], 'mercadolivre': [], 'melhores_ofertas': []}
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
    filtros = dados.get('filtros', {'kabum': True, 'pichau': True, 'terabyte': True, 'mercadolivre': True})

    result = {}
    todas_ofertas = []
    todas_lojas = ['kabum', 'pichau', 'terabyte', 'mercadolivre']
    buscadores = {
        'kabum': buscar_kabum,
        'pichau': buscar_pichau,
        'terabyte': buscar_terabyte,
        'mercadolivre': buscar_mercadolivre,
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
        )[:5]
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
        filtros = {s: True for s in ['kabum', 'pichau', 'terabyte', 'mercadolivre']}

    todas_lojas = ['kabum', 'pichau', 'terabyte', 'mercadolivre']
    lojas_ativas = [s for s in todas_lojas if filtros.get(s)]

    if not lojas_ativas:
        return _sse_error('Selecione pelo menos uma loja.')

    buscadores = {
        'kabum': buscar_kabum,
        'pichau': buscar_pichau,
        'terabyte': buscar_terabyte,
        'mercadolivre': buscar_mercadolivre,
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
        )[:5]
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
        'lojas': dados.get('lojas', {'kabum': True, 'pichau': True, 'terabyte': True, 'mercadolivre': True}),
        'valor_minimo': dados.get('valor_minimo', 0),
        'valor_maximo': dados.get('valor_maximo', None),
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
        melhores = sorted(validas, key=lambda x: x['preco'])[:5]
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


import sys
import webbrowser
from threading import Timer

def abrir_navegador():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == '__main__':
    debug = True

    if "--abrir" in sys.argv and os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        Timer(1, abrir_navegador).start()

    app.run(debug=debug)

