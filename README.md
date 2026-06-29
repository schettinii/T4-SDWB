# Shared Distributed Write Board (SDWB)

Quadro branco distribuído. Vários clientes desenham no mesmo quadro em tempo real.
Não há servidor fixo: um **Serviço de Nomes** faz a descoberta e um **Coordenador**
(que é um dos próprios clientes) guarda o estado e repassa as ações.

## Arquivos

- `servico_nomes.py` — processo separado, com IP e porta fixos. Guarda só a tabela
  `(nome do quadro, ip, porta)` dos coordenadores. Nunca falha.
- `coordenador.py` — classe `Coordenador`. Roda dentro do processo de um cliente
  (o que criou o quadro ou o que venceu a eleição). Guarda os objetos do quadro,
  a lista de participantes e as seleções, e repassa tudo para todos.
- `cliente.py` — interface gráfica (Tkinter) + a parte de rede do cliente. É quem
  cada usuário abre.
- `rede.py` — funções de envio/recebimento das mensagens.

Comunicação: **sockets TCP**. Cada mensagem é um JSON terminado por `\n`. A maioria
das chamadas é pergunta-resposta (abre conexão, manda, recebe, fecha).

## Como rodar

Precisa só de Python 3 (Tkinter já vem junto). Numa máquina só, para testar:

```
python servico_nomes.py        # primeiro o serviço de nomes
python cliente.py              # um cliente (cria ou ingressa em um quadro)
python cliente.py              # outro cliente, em outro terminal
```

Em rede, rode o `servico_nomes.py` numa máquina e passe o IP dela para os clientes:

```
python cliente.py 192.168.0.10
```

O serviço de nomes usa a porta fixa **6000**. As portas do coordenador e dos clientes
são escolhidas automaticamente pelo sistema operacional (`bind` na porta 0).

## Fluxo

- **Criar quadro:** o cliente escolhe um nome, sobe um `Coordenador` no próprio
  processo, registra `(nome, ip, porta)` no serviço de nomes e entra como cliente.
- **Ingressar:** o cliente pede a lista de quadros ao serviço de nomes, escolhe um,
  fala com o coordenador (`ingressar`) e recebe o estado atual do quadro + a lista de
  participantes.
- **Desenhar / colorir / remover:** o cliente manda a ação para o coordenador, que
  aplica no estado e faz *broadcast* para todos. Por isso a mudança aparece em todas
  as telas.
- **Exclusão mútua:** antes de colorir ou remover é preciso `selecionar` o objeto. O
  coordenador trava o objeto para um cliente só; se outro tentar selecionar o mesmo
  objeto, recebe erro.
- **Heartbeat:** o coordenador dá `ping` em cada cliente a cada T=3s e tira da lista
  quem não responde em 2T. Cada cliente dá `ping` no coordenador; se ele não responde
  em 2T, começa uma eleição.
- **Eleição (Valentão / Bully):** o cliente com a maior porta entre os vivos vira o
  novo coordenador, carrega o estado que já tinha espelhado, atualiza o serviço de
  nomes e avisa os outros.

## Protocolo de mensagens

### Cliente → Serviço de Nomes

| op | campos | resposta |
| :-- | :-- | :-- |
| `registrar` | `nome`, `ip`, `porta` | `{ok: true}` |
| `listar` | — | `{quadros: [[nome, ip, porta], ...]}` |
| `remover` | `nome` | `{ok: true}` |

### Cliente → Coordenador

| op | campos | resposta |
| :-- | :-- | :-- |
| `ingressar` | `ip`, `porta` | `{objetos: [...], participantes: [...]}` |
| `sair` | `ip`, `porta` | `{ok: true}` |
| `add_linha` | `x1`, `y1`, `x2`, `y2` | `{ok: true}` |
| `add_quadrado` | `x1`, `y1`, `x2`, `y2` | `{ok: true}` |
| `selecionar` | `obj_id`, `ip`, `porta` | `{ok: true}` ou `{ok: false, erro: ...}` |
| `liberar` | `ip`, `porta` | `{ok: true}` |
| `colorir` | `obj_id`, `cor`, `ip`, `porta` | `{ok: true}` ou `{ok: false, erro: ...}` |
| `remover` | `obj_id`, `ip`, `porta` | `{ok: true}` ou `{ok: false, erro: ...}` |
| `ping` | — | `{ok: true}` |

### Coordenador → Cliente (broadcast e heartbeat)

| op | campos | resposta |
| :-- | :-- | :-- |
| `novo_objeto` | `objeto` | `{ok: true}` |
| `obj_colorido` | `obj_id`, `cor` | `{ok: true}` |
| `obj_removido` | `obj_id` | `{ok: true}` |
| `participantes` | `lista` | `{ok: true}` |
| `ping` | — | `{ok: true}` |

### Cliente → Cliente (eleição)

| op | campos | resposta |
| :-- | :-- | :-- |
| `eleicao` | — | `{ok: true}` |
| `coordenador` | `ip`, `porta` | `{ok: true}` |

### Objeto do quadro

```
{ "id": 1, "tipo": "linha"|"quadrado", "x1":.., "y1":.., "x2":.., "y2":.., "cor": "black" }
```

Linha e quadrado são definidos por dois pontos. As cores disponíveis para colorir são
vermelho e azul.

## Testando os cenários

- **Entrada dinâmica:** suba o serviço de nomes, depois um cliente que cria o quadro,
  depois outros que ingressam — cada um recebe o desenho que já existia.
- **Exclusão mútua:** dois clientes selecionam o mesmo objeto; o segundo recebe erro.
- **Morte do coordenador:** mate (Ctrl+C) o processo do cliente que hospeda o
  coordenador. Os outros detectam pelo heartbeat e elegem um novo, que se registra no
  serviço de nomes e continua o quadro.
