# SDWB (Shared Distributed Write Board)

Quadro branco que vários clientes editam juntos, em tempo real. A ideia é não ter
servidor fixo: existe um serviço de nomes só para a descoberta e um coordenador, que é
um dos próprios clientes, que guarda o estado do quadro e repassa as ações para os
outros.

## Arquivos

- `servico_nomes.py`: processo à parte, com IP e porta fixos. Guarda só a tabela
  `(nome do quadro, ip, porta)` dos coordenadores. A gente assume que ele não cai.
- `coordenador.py`: a classe `Coordenador`. Ela roda dentro do processo de um cliente
  (o que criou o quadro, ou o que ganhou a eleição). Guarda os objetos, a lista de
  participantes e as seleções, e manda tudo para todo mundo.
- `cliente.py`: a interface (Tkinter) junto com a parte de rede do cliente. É o que
  cada pessoa abre.
- `rede.py`: as funções de mandar e receber mensagem, que os outros arquivos usam.

A comunicação é por socket TCP. Cada mensagem é um JSON terminado com `\n` (esse `\n` é
o que marca onde uma mensagem acaba). Quase tudo é pergunta e resposta: abre a conexão,
manda, lê a resposta e fecha.

## Como rodar

Só precisa de Python 3, o Tkinter já vem com ele. Para testar numa máquina só:

```
python servico_nomes.py        # primeiro o serviço de nomes
python cliente.py              # um cliente (cria ou entra num quadro)
python cliente.py              # outro cliente, em outro terminal
```

Para rodar em rede, sobe o `servico_nomes.py` numa máquina e passa o IP dela para os
clientes:

```
python cliente.py 192.168.0.10
```

O serviço de nomes fica na porta 6000 (fixa). As portas do coordenador e dos clientes
quem escolhe é o sistema operacional (o `bind` é na porta 0).

## Como funciona

Criar um quadro: o cliente digita um nome, sobe um `Coordenador` no próprio processo,
registra `(nome, ip, porta)` no serviço de nomes e entra no quadro como um cliente
qualquer.

Entrar num quadro que já existe: o cliente pede a lista para o serviço de nomes, escolhe
um e fala com o coordenador daquele quadro (`ingressar`). Já nessa resposta vem o estado
atual, todos os objetos que já tinham sido desenhados, mais a lista de participantes.

Desenhar, colorir ou remover: o cliente manda a ação para o coordenador. Ele aplica no
estado e faz broadcast para todos, por isso a mudança aparece em todas as telas.

Exclusão mútua: antes de colorir ou remover é preciso selecionar o objeto. O coordenador
trava o objeto para um cliente só; se outro tentar selecionar o mesmo, leva um erro.

Heartbeat: o coordenador dá `ping` em cada cliente a cada 3 segundos e tira da lista quem
não responde em dois ciclos. Do outro lado, cada cliente dá `ping` no coordenador; se ele
sumir por dois ciclos, o cliente começa uma eleição.

Eleição (algoritmo do valentão): entre os que estão vivos, quem tem a maior porta vira o
novo coordenador. Ele já tinha o estado espelhado, então sobe um coordenador novo com ele,
se registra de novo no serviço de nomes e avisa os outros.

## Protocolo de mensagens

Tudo é JSON com um campo `op`. As tabelas abaixo são as mensagens que existem.

### Cliente para o serviço de nomes

| op | campos | resposta |
| :-- | :-- | :-- |
| `registrar` | `nome`, `ip`, `porta` | `{ok: true}` |
| `listar` | - | `{quadros: [[nome, ip, porta], ...]}` |
| `remover` | `nome` | `{ok: true}` |

### Cliente para o coordenador

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
| `ping` | - | `{ok: true}` |

### Coordenador para o cliente (broadcast e heartbeat)

| op | campos | resposta |
| :-- | :-- | :-- |
| `novo_objeto` | `objeto` | `{ok: true}` |
| `obj_colorido` | `obj_id`, `cor` | `{ok: true}` |
| `obj_removido` | `obj_id` | `{ok: true}` |
| `participantes` | `lista` | `{ok: true}` |
| `ping` | - | `{ok: true}` |

### Cliente para cliente (eleição)

| op | campos | resposta |
| :-- | :-- | :-- |
| `eleicao` | - | `{ok: true}` |
| `coordenador` | `ip`, `porta` | `{ok: true}` |

### Como é um objeto do quadro

```
{ "id": 1, "tipo": "linha"|"quadrado", "x1":.., "y1":.., "x2":.., "y2":.., "cor": "black" }
```

Linha e quadrado são dois pontos. As cores que dá para aplicar são vermelho e azul.

## Testando os cenários do enunciado

- Entrada dinâmica: sobe o serviço de nomes, um cliente cria o quadro e desenha, os
  outros entram depois. Cada um que entra já recebe o que tinha sido desenhado antes.
- Exclusão mútua: dois clientes selecionam o mesmo objeto ao mesmo tempo; o segundo
  recebe erro.
- Morte do coordenador: mata (Ctrl+C) o cliente que está hospedando o coordenador. Os
  outros percebem pelo heartbeat, elegem um novo, ele se registra no serviço de nomes e
  o quadro continua.

## Observações

Testei tudo numa máquina só (localhost) e também em duas máquinas na mesma rede do lab. A
parte da eleição às vezes demora uns segundos para estabilizar quando o coordenador cai,
mas no fim sempre converge. O 2PC não foi implementado porque foi retirado do enunciado.
