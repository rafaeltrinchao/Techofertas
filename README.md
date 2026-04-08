# TechOfertas

Comparador de preços para produtos de tecnologia nas principais lojas brasileiras: **KaBuM!**, **Pichau**, **Terabyte** e **Mercado Livre**.

Busca em tempo real com streaming de resultados, lista de acompanhamento com atualização automática, histórico de preços e suporte a dark mode.

---

## Funcionalidades

- Busca simultânea em até 4 lojas com resultados em tempo real (SSE)
- Filtro por valor mínimo e máximo
- Melhores ofertas automáticas com ranking de preços
- **Aba Acompanhar**: monitora produtos salvos e atualiza preços automaticamente a cada 15 minutos
- Histórico de preços com indicador de tendência (subiu / caiu / estável)
- Dark mode com alternância por botão
- Reordenação de produtos acompanhados por drag and drop
- Interface responsiva para desktop e mobile

---

## Requisitos

- Python 3.8 ou superior
- pip

---

## Como clonar e rodar

### 1. Clonar o repositório

```bash
git clone https://github.com/seu-usuario/TechOfertas.git
cd TechOfertas
```

### 2. Criar e ativar um ambiente virtual (recomendado)

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 4. Rodar o servidor

```bash
python app.py
```

O servidor sobe em `http://127.0.0.1:5000`.

Para abrir o navegador automaticamente ao iniciar:

```bash
python app.py --abrir
```

---

## Estrutura do projeto

```
TechOfertas/
├── app.py              # Aplicação Flask completa (backend + frontend embutido)
├── requirements.txt    # Dependências Python
├── watchlist.json      # Dados persistidos da lista de acompanhamento (gerado automaticamente)
└── logo2.png           # Logo do site
```

---

## Dependências

| Pacote | Versão | Uso |
|--------|--------|-----|
| Flask | 3.0.3 | Servidor web e SSE |
| cloudscraper | 1.2.71 | Scraping com bypass de proteções |
| requests | 2.32.3 | Requisições HTTP |

---

## Executável (sem instalação)

Se não quiser instalar Python e dependências, basta executar o arquivo `TechOfertas.exe` diretamente. Ele já inclui tudo o que é necessário para rodar.

Após abrir, o servidor sobe automaticamente e você pode acessar `http://127.0.0.1:5000` no navegador.

> O `watchlist.json` será criado na mesma pasta do executável.

---

## Observações

- O arquivo `watchlist.json` é criado automaticamente na primeira vez que você adiciona um produto à lista de acompanhamento.
- O scraping depende da estrutura atual dos sites das lojas. Se uma loja alterar seu layout, a busca pode retornar sem resultados para aquela loja específica.
- O projeto foi desenvolvido para uso pessoal. Respeite os termos de uso de cada site.

---

Desenvolvido por Rafael Trinchão
