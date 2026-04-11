# TechOfertas

Comparador de preços para produtos de tecnologia nas principais lojas brasileiras: **KaBuM!**, **Pichau**, **Terabyte**, **Mercado Livre**, **Magazine Luiza**, **Amazon**, **Shopee** e **Casas Bahia**.

Busca em tempo real com streaming de resultados, lista de acompanhamento com atualização automática, histórico de preços e suporte a dark mode.

---

## Funcionalidades

- Busca simultânea em até 8 lojas com resultados em tempo real (SSE)
- Filtro por valor mínimo e máximo
- Melhores ofertas automáticas com ranking de preços
- **Aba Acompanhar**: monitora produtos salvos e atualiza preços automaticamente a cada 15 minutos
- Acompanhamento por link direto do produto ou por nome/query
- Histórico de preços com indicador de tendência (subiu / caiu / estável)
- Alertas de preço no navegador: notificação quando o preço cai ou atinge valor alvo
- Notificações via Telegram (configurável por usuário, sem custo)
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
git clone https://github.com/rafaeltrinchao/Techofertas.git
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
├── app.py                  # Aplicação Flask completa (backend + frontend embutido)
├── requirements.txt        # Dependências Python
├── watchlist.json          # Lista de acompanhamento (gerado automaticamente)
├── telegram_config.json    # Configuração do Telegram (gerado automaticamente ao configurar)
└── logo2.png               # Logo do site
```

---

## Dependências

| Pacote | Versão | Uso |
|--------|--------|-----|
| Flask | 3.0.3 | Servidor web e SSE |
| cloudscraper | 1.2.71 | Scraping com bypass de proteções |
| requests | 2.32.3 | Requisições HTTP |

---

## Notificações via Telegram

É possível receber alertas de queda de preço diretamente no Telegram, sem nenhum custo. Para configurar:

1. Na aba **Acompanhar**, clique no botão **Telegram**
2. Siga o guia passo a passo dentro do modal para criar seu bot no BotFather
3. Cole o Token, clique em **Detectar** para preencher o Chat ID automaticamente
4. Salve e clique em **Testar** para confirmar

A configuração é individual — cada usuário cria o próprio bot e o `telegram_config.json` é gerado localmente.

---

## Observações

- Os arquivos `watchlist.json` e `telegram_config.json` são criados automaticamente na primeira utilização e ficam na mesma pasta do `app.py`.
- O scraping depende da estrutura atual dos sites das lojas. Se uma loja alterar seu layout, a busca pode retornar sem resultados para aquela loja específica.
- O projeto foi desenvolvido para uso pessoal. Respeite os termos de uso de cada site.

---

Desenvolvido por Rafael Trinchão
