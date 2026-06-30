# =============================================================================
# Suite de testes de estresse do SDWB (roda tudo sem abrir interface grafica).
#
# Usa dois tipos de cliente falso:
#   - FakeCliente: so a parte de rede (um socket que guarda os broadcasts).
#     serve pra testar o lado do coordenador (entrada, desenho, exclusao mutua,
#     heartbeat).
#   - ClienteSimulado: a classe Cliente DE VERDADE, sem a janela. serve pra testar
#     a eleicao e a recuperacao com o codigo real do trabalho.
#
# Como rodar:
#   python3 testes_estresse.py          roda todos os testes
#   python3 testes_estresse.py 13       roda so o teste de numero 13
#
# Para mudar a carga/escala, mexa no bloco CONFIG logo abaixo.
#
# TESTES (numero = ordem):
#   --- Servico de Nomes (descoberta) ---
#   01 nomes_basico .......... registrar, listar, remover e sobrescrever endereco
#   02 nomes_concorrente ..... N quadros registrados em paralelo, sem perda
#   03 nomes_resiliencia ..... aguenta muitas operacoes e continua respondendo
#   --- Entrada e sincronizacao de estado ---
#   04 entrada_dinamica ...... quem entra recebe os desenhos que ja existiam
#   05 sync_estado_grande .... entra e recebe um estado grande inteiro
#   06 escala_entrada ........ muitos clientes entrando juntos, sem duplicata
#   07 ingressar_idempotente . o mesmo cliente entrando 2x nao duplica na lista
#   --- Desenho, broadcast e consistencia ---
#   08 carga_desenho ......... muitos objetos em paralelo: ids unicos, convergencia
#   09 tipos_figura .......... linha e quadrado com o tipo certo + broadcast
#   10 colorir_propaga ....... colorir muda a cor e chega em todos os clientes
#   11 remover_propaga ....... remover apaga o objeto em todos os clientes
#   --- Exclusao mutua ---
#   12 corrida_selecao ....... N disputam o mesmo objeto: 1 ganha, o resto erra
#   13 colorir_x_remover ..... operacoes conflitantes: so uma aplica (atomicidade)
#   14 operacao_sem_selecao .. colorir/remover sem selecionar antes da erro
#   15 selecao_unica_liberar . 1 objeto por cliente; liberar e colorir soltam o lock
#   --- Heartbeat e deteccao de falha ---
#   16 saida_limpa ........... quem manda "sair" e removido na hora
#   17 morte_abrupta ......... o heartbeat detecta e remove quem caiu (em ~2T)
#   18 selecao_do_morto ...... o lock de um cliente que caiu e liberado
#   19 churn_combinado ....... desenhar + entrar + sair + cair ao mesmo tempo
#   --- Eleicao e tolerancia a falhas ---
#   20 eleicao_failover ...... mata o coordenador: novo eleito (maior porta),
#                              nomes atualizado, desenho e participantes recuperados,
#                              todos reapontam e o quadro segue funcional
#   21 eleicao_cascata ....... mata tambem o recem-eleito: outro assume
#   --- Borda ---
#   22 reentrancia ........... cliente sai e volta sem duplicar na lista
# =============================================================================

import socket
import threading
import json
import time
import subprocess
import sys
import os

import rede
import coordenador
import cliente as cli

# ============================== CONFIG (mexa aqui) ============================
IP            = "127.0.0.1"   # tudo roda em localhost
PORTA_NOMES   = 6000          # porta fixa do servico de nomes
HEARTBEAT_T   = 1             # T do heartbeat nos testes, em segundos (no trabalho e 3)

# escala / carga
N_CLIENTES_ESCALA = 30        # 06: clientes entrando ao mesmo tempo
N_QUADROS         = 25        # 02: quadros registrados em paralelo no nomes
N_OPS_NOMES       = 200       # 03: operacoes pra estressar o servico de nomes
N_DESENHISTAS     = 8         # 08: clientes desenhando em paralelo
OBJ_POR_CLIENTE   = 40        # 08: objetos que cada um desenha
ESTADO_GRANDE     = 200       # 05: tamanho do estado no teste de sync
N_DISPUTA         = 20        # 12: clientes disputando a selecao do mesmo objeto
N_CHURN           = 12        # 16/17: clientes no teste de entra/sai/morre
N_CHAOS_DESENHOS  = 100       # 19: desenhos disparados durante o caos
N_ELEICAO         = 6         # 20/21: clientes (alem do host) na eleicao

# timeouts (segundos)
TIMEOUT_PADRAO       = 12
TIMEOUT_CONVERGENCIA = 25
TIMEOUT_ELEICAO      = 15
# =============================================================================

NOMES = (IP, PORTA_NOMES)
cli.T = HEARTBEAT_T
coordenador.T = HEARTBEAT_T
cli.IP_NOMES = IP
cli.PORTA_NOMES = PORTA_NOMES

resultados = []


def verifica(cond, descricao):
    print(("   OK    " if cond else "   FALHOU  ") + descricao)
    resultados.append(bool(cond))


def espera_ate(cond, timeout=TIMEOUT_PADRAO):
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def sobe_servico_nomes():
    subprocess.run(["pkill", "-f", "servico_nomes.py"])
    time.sleep(0.5)
    p = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "servico_nomes.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    return p


def sobe_coordenador(nome):
    c = coordenador.Coordenador(nome, IP, 0)
    c.iniciar()
    rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": nome, "ip": IP, "porta": c.porta})
    return c


def desenha(coord, tipo="linha", x1=0, y1=0, x2=5, y2=5):
    # desenha direto no coordenador e devolve o id do objeto criado
    op = "add_linha" if tipo == "linha" else "add_quadrado"
    rede.envia(IP, coord.porta, {"op": op, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return coord.objetos[-1]["id"]


def lista_quadros():
    return rede.envia(NOMES[0], NOMES[1], {"op": "listar"})["quadros"]


def porta_no_nomes(nome):
    for q in lista_quadros():
        if q[0] == nome:
            return q[2]
    return None


# cliente de mentira: so um servidor que guarda os broadcasts que chegam e responde ok
class FakeCliente:
    def __init__(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        self.porta = s.getsockname()[1]
        s.listen()
        self.servidor = s
        self.recebidas = []
        self.viva = True
        threading.Thread(target=self._laco, daemon=True).start()

    def _laco(self):
        while self.viva:
            try:
                c, _ = self.servidor.accept()
            except Exception:
                break
            threading.Thread(target=self._trata, args=(c,), daemon=True).start()

    def _trata(self, c):
        linha = rede.le_linha(c)
        if linha:
            self.recebidas.append(json.loads(linha))
            rede.responde(c, {"ok": True})
        c.close()

    def morre(self):
        # simula queda abrupta: para de responder, sem avisar ninguem
        self.viva = False
        try:
            self.servidor.close()
        except Exception:
            pass

    def conta(self, op):
        return len([m for m in self.recebidas if m["op"] == op])

    def envia(self, coord_porta, msg):
        m = dict(msg)
        m.setdefault("ip", IP)
        m.setdefault("porta", self.porta)
        return rede.envia(IP, coord_porta, m)

    def ingressa(self, coord_porta):
        return self.envia(coord_porta, {"op": "ingressar"})

    def sai(self, coord_porta):
        return self.envia(coord_porta, {"op": "sair"})

    def seleciona(self, coord_porta, oid):
        return self.envia(coord_porta, {"op": "selecionar", "obj_id": oid})

    def libera(self, coord_porta):
        return self.envia(coord_porta, {"op": "liberar"})

    def colore(self, coord_porta, oid, cor):
        return self.envia(coord_porta, {"op": "colorir", "obj_id": oid, "cor": cor})

    def remove(self, coord_porta, oid):
        return self.envia(coord_porta, {"op": "remover", "obj_id": oid})


# a classe Cliente de verdade, so que sem abrir janela. uso pra testar o
# heartbeat e a eleicao com o codigo real.
class ClienteSimulado(cli.Cliente):
    def __init__(self, nome_quadro):
        self.ip = IP
        self.porta = None
        self.nome_quadro = nome_quadro
        self.coord_ip = None
        self.coord_porta = None
        self.coordenador = None
        self.objetos = []
        self.participantes = []
        self.selecionado = None
        self.cor_atual = "red"
        self.modo = None
        self.pontos = []
        self.precisa_redesenhar = False
        self.no_quadro = True
        self.em_eleicao = False
        self.ligado = True
        self.trava = threading.Lock()
        self._inicia_servidor()
        threading.Thread(target=self._laco_ping, daemon=True).start()

    def cria_quadro(self):
        self.coordenador = coordenador.Coordenador(self.nome_quadro, self.ip, 0)
        self.coordenador.iniciar()
        self.coord_ip = self.ip
        self.coord_porta = self.coordenador.porta
        rede.envia(cli.IP_NOMES, cli.PORTA_NOMES,
                   {"op": "registrar", "nome": self.nome_quadro, "ip": self.ip, "porta": self.coordenador.porta})
        self.ingressa()

    def ingressa(self):
        e = rede.envia(self.coord_ip, self.coord_porta, {"op": "ingressar", "ip": self.ip, "porta": self.porta})
        self.objetos = e["objetos"]
        self.participantes = e["participantes"]

    def desliga(self):
        # simula desligar o PC: para o coordenador (se hospedava), fecha o servidor
        # e marca no_quadro=False pra esse no nao ficar pingando depois de morto.
        self.ligado = False
        self.no_quadro = False
        if self.coordenador is not None:
            self.coordenador.parar()
        try:
            self.servidor.close()
        except Exception:
            pass


def eh_coordenador(c):
    return c.coordenador is not None and c.coordenador.ativo


def monta_cluster(nome, n):
    # sobe um host (que cria o quadro) + n clientes que ingressam nele.
    host = ClienteSimulado(nome)
    host.cria_quadro()
    clientes = [ClienteSimulado(nome) for _ in range(n)]
    for c in clientes:
        c.coord_ip = host.coord_ip
        c.coord_porta = host.coord_porta
        c.ingressa()
    espera_ate(lambda: all(len(c.participantes) >= n + 1 for c in clientes))
    return host, clientes


# ============================ SERVICO DE NOMES ===============================

def teste01_nomes_basico():
    print("\n[01] NOMES: registrar, listar, remover e sobrescrever")
    rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": "ns_a", "ip": IP, "porta": 11111})
    rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": "ns_b", "ip": IP, "porta": 22222})
    verifica(porta_no_nomes("ns_a") == 11111 and porta_no_nomes("ns_b") == 22222,
             "dois quadros registrados aparecem no listar com a porta certa")

    rede.envia(NOMES[0], NOMES[1], {"op": "remover", "nome": "ns_a"})
    verifica(porta_no_nomes("ns_a") is None and porta_no_nomes("ns_b") == 22222,
             "remover tira so o quadro pedido, o outro continua")

    rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": "ns_b", "ip": IP, "porta": 33333})
    verifica(porta_no_nomes("ns_b") == 33333,
             "registrar de novo com o mesmo nome sobrescreve o endereco (usado na eleicao)")
    rede.envia(NOMES[0], NOMES[1], {"op": "remover", "nome": "ns_b"})


def teste02_nomes_concorrente():
    print("\n[02] NOMES: muitos quadros registrados em paralelo")
    def reg(i):
        rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": "cc_%d" % i, "ip": IP, "porta": 40000 + i})
    ts = [threading.Thread(target=reg, args=(i,)) for i in range(N_QUADROS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    nomes = [q[0] for q in lista_quadros()]
    presentes = sum(1 for i in range(N_QUADROS) if ("cc_%d" % i) in nomes)
    verifica(presentes == N_QUADROS, "os %d quadros registrados juntos aparecem todos (sem perda)" % N_QUADROS)
    portas_ok = all(porta_no_nomes("cc_%d" % i) == 40000 + i for i in range(N_QUADROS))
    verifica(portas_ok, "nenhum endereco foi trocado/corrompido pela concorrencia")
    for i in range(N_QUADROS):
        rede.envia(NOMES[0], NOMES[1], {"op": "remover", "nome": "cc_%d" % i})


def teste03_nomes_resiliencia():
    print("\n[03] NOMES: aguenta muitas operacoes e continua respondendo")
    def opera(i):
        rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": "res_%d" % i, "ip": IP, "porta": 50000 + (i % 1000)})
        rede.envia(NOMES[0], NOMES[1], {"op": "listar"})
        rede.envia(NOMES[0], NOMES[1], {"op": "remover", "nome": "res_%d" % i})
    ts = [threading.Thread(target=opera, args=(i,)) for i in range(N_OPS_NOMES)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    resp = rede.envia(NOMES[0], NOMES[1], {"op": "listar"})
    verifica(resp is not None and "quadros" in resp,
             "depois de %d operacoes concorrentes o servico de nomes ainda responde" % N_OPS_NOMES)
    nomes = [q[0] for q in resp["quadros"]]
    verifica(not any(n.startswith("res_") for n in nomes),
             "as entradas de teste foram todas removidas (tabela limpa)")


# ===================== ENTRADA E SINCRONIZACAO DE ESTADO =====================

def teste04_entrada_dinamica():
    print("\n[04] ENTRADA DINAMICA: quem entra recebe o que ja foi desenhado")
    coord = sobe_coordenador("din")
    desenha(coord, "linha")
    desenha(coord, "quadrado")
    desenha(coord, "linha")
    f = FakeCliente()
    estado = f.ingressa(coord.porta)
    verifica(len(estado["objetos"]) == 3, "o novo cliente recebeu os 3 objetos que ja existiam")
    verifica([IP, f.porta] in coord.participantes, "e entrou na lista de participantes")
    coord.parar()
    f.morre()


def teste05_sync_estado_grande():
    print("\n[05] SYNC: estado grande (%d objetos) recebido inteiro" % ESTADO_GRANDE)
    coord = sobe_coordenador("grande")
    for i in range(ESTADO_GRANDE):
        desenha(coord, "linha", x2=i, y2=i)
    f = FakeCliente()
    estado = f.ingressa(coord.porta)
    verifica(len(estado["objetos"]) == ESTADO_GRANDE,
             "o cliente recebeu os %d objetos de uma vez no ingresso" % ESTADO_GRANDE)
    coord.parar()
    f.morre()


def teste06_escala_entrada():
    print("\n[06] ESCALA: %d clientes entrando ao mesmo tempo" % N_CLIENTES_ESCALA)
    coord = sobe_coordenador("escala")
    fakes = [FakeCliente() for _ in range(N_CLIENTES_ESCALA)]
    inicio = time.time()
    ts = [threading.Thread(target=f.ingressa, args=(coord.porta,)) for f in fakes]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    demora = time.time() - inicio
    verifica(len(coord.participantes) == N_CLIENTES_ESCALA,
             "os %d clientes entraram e estao todos na lista" % N_CLIENTES_ESCALA)
    enderecos = [tuple(p) for p in coord.participantes]
    verifica(len(set(enderecos)) == N_CLIENTES_ESCALA, "sem duplicata na lista (entrada concorrente)")
    verifica(espera_ate(lambda: all(f.conta("participantes") >= 1 for f in fakes)),
             "todos receberam pelo menos um broadcast de participantes")
    print("   (entrada de %d clientes em %.2fs)" % (N_CLIENTES_ESCALA, demora))
    coord.parar()
    for f in fakes:
        f.morre()


def teste07_ingressar_idempotente():
    print("\n[07] IDEMPOTENCIA: ingressar 2x nao duplica na lista")
    coord = sobe_coordenador("idem")
    f = FakeCliente()
    f.ingressa(coord.porta)
    f.ingressa(coord.porta)
    verifica(coord.participantes.count([IP, f.porta]) == 1,
             "o mesmo cliente entrando duas vezes aparece so uma vez")
    coord.parar()
    f.morre()


# ==================== DESENHO, BROADCAST E CONSISTENCIA ======================

def teste08_carga_desenho():
    print("\n[08] CARGA: %d clientes x %d objetos em paralelo" % (N_DESENHISTAS, OBJ_POR_CLIENTE))
    coord = sobe_coordenador("carga")
    total = N_DESENHISTAS * OBJ_POR_CLIENTE
    fakes = [FakeCliente() for _ in range(N_DESENHISTAS)]
    for f in fakes:
        f.ingressa(coord.porta)

    def desenha_varios(f):
        for i in range(OBJ_POR_CLIENTE):
            rede.envia(IP, coord.porta, {"op": "add_linha", "x1": 0, "y1": 0, "x2": i, "y2": i})

    inicio = time.time()
    ts = [threading.Thread(target=desenha_varios, args=(f,)) for f in fakes]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    demora = time.time() - inicio

    verifica(len(coord.objetos) == total, "o coordenador guardou os %d objetos (nenhum perdido)" % total)
    ids = [o["id"] for o in coord.objetos]
    verifica(len(set(ids)) == total, "todos os ids sao unicos (o contador aguentou a concorrencia)")
    verifica(espera_ate(lambda: all(f.conta("novo_objeto") >= total for f in fakes), timeout=TIMEOUT_CONVERGENCIA),
             "todos os clientes convergiram para os %d objetos" % total)
    print("   (%d objetos em %.2fs -> %.0f obj/s)" % (total, demora, total / demora))
    coord.parar()
    for f in fakes:
        f.morre()


def teste09_tipos_figura():
    print("\n[09] FIGURAS: linha e quadrado com o tipo certo + broadcast")
    coord = sobe_coordenador("figs")
    obs = FakeCliente()
    obs.ingressa(coord.porta)
    id_linha = desenha(coord, "linha")
    id_quad = desenha(coord, "quadrado")
    tipos = {o["id"]: o["tipo"] for o in coord.objetos}
    verifica(tipos.get(id_linha) == "linha" and tipos.get(id_quad) == "quadrado",
             "add_linha vira tipo 'linha' e add_quadrado vira tipo 'quadrado'")
    verifica(espera_ate(lambda: obs.conta("novo_objeto") >= 2),
             "o observador recebeu o broadcast das duas figuras")
    coord.parar()
    obs.morre()


def teste10_colorir_propaga():
    print("\n[10] COLORIR: muda a cor e chega em todos")
    coord = sobe_coordenador("cor")
    ator = FakeCliente()
    obs = FakeCliente()
    ator.ingressa(coord.porta)
    obs.ingressa(coord.porta)
    oid = desenha(coord, "linha")
    ator.seleciona(coord.porta, oid)
    r = ator.colore(coord.porta, oid, "blue")
    verifica(r.get("ok") is True, "o dono conseguiu colorir o objeto que selecionou")
    cor = next(o["cor"] for o in coord.objetos if o["id"] == oid)
    verifica(cor == "blue", "a cor do objeto mudou no coordenador")
    verifica(espera_ate(lambda: obs.conta("obj_colorido") >= 1),
             "o outro cliente recebeu o broadcast de cor")
    coord.parar()
    ator.morre()
    obs.morre()


def teste11_remover_propaga():
    print("\n[11] REMOVER: apaga o objeto em todos")
    coord = sobe_coordenador("rem")
    ator = FakeCliente()
    obs = FakeCliente()
    ator.ingressa(coord.porta)
    obs.ingressa(coord.porta)
    oid = desenha(coord, "linha")
    ator.seleciona(coord.porta, oid)
    r = ator.remove(coord.porta, oid)
    verifica(r.get("ok") is True, "o dono conseguiu remover o objeto que selecionou")
    verifica(all(o["id"] != oid for o in coord.objetos), "o objeto sumiu do estado do coordenador")
    verifica(espera_ate(lambda: obs.conta("obj_removido") >= 1),
             "o outro cliente recebeu o broadcast de remocao")
    coord.parar()
    ator.morre()
    obs.morre()


# ============================== EXCLUSAO MUTUA ===============================

def teste12_corrida_selecao():
    print("\n[12] EXCLUSAO MUTUA: %d clientes disputam o mesmo objeto" % N_DISPUTA)
    coord = sobe_coordenador("disputa")
    oid = desenha(coord, "linha")
    fakes = [FakeCliente() for _ in range(N_DISPUTA)]
    for f in fakes:
        f.ingressa(coord.porta)

    barreira = threading.Barrier(N_DISPUTA)
    respostas = []
    trava = threading.Lock()

    def tenta(f):
        barreira.wait()
        r = f.seleciona(coord.porta, oid)
        with trava:
            respostas.append(r)

    ts = [threading.Thread(target=tenta, args=(f,)) for f in fakes]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    ganharam = [r for r in respostas if r.get("ok")]
    perderam = [r for r in respostas if not r.get("ok")]
    verifica(len(ganharam) == 1, "exatamente 1 cliente conseguiu selecionar (%d tentaram juntos)" % N_DISPUTA)
    verifica(len(perderam) == N_DISPUTA - 1, "os outros %d receberam erro de objeto em uso" % (N_DISPUTA - 1))
    coord.parar()
    for f in fakes:
        f.morre()


def teste13_colorir_x_remover():
    print("\n[13] ATOMICIDADE: colorir x remover concorrentes no mesmo objeto")
    coord = sobe_coordenador("atom")
    dono = FakeCliente()
    dono.ingressa(coord.porta)
    oid = desenha(coord, "linha")
    dono.seleciona(coord.porta, oid)

    res = {}

    def faz_colorir():
        res["cor"] = dono.colore(coord.porta, oid, "red")

    def faz_remover():
        res["rem"] = dono.remove(coord.porta, oid)

    a = threading.Thread(target=faz_colorir)
    b = threading.Thread(target=faz_remover)
    a.start(); b.start(); a.join(); b.join()
    aceitas = [1 for r in res.values() if r.get("ok")]
    verifica(len(aceitas) == 1, "so uma das duas operacoes foi aceita (a outra deu erro)")
    coord.parar()
    dono.morre()


def teste14_operacao_sem_selecao():
    print("\n[14] EXCLUSAO MUTUA: colorir/remover sem selecionar antes")
    coord = sobe_coordenador("semsel")
    f = FakeCliente()
    f.ingressa(coord.porta)
    oid = desenha(coord, "linha")
    rc = f.colore(coord.porta, oid, "blue")
    rr = f.remove(coord.porta, oid)
    verifica(rc.get("ok") is False, "colorir sem selecionar da erro")
    verifica(rr.get("ok") is False, "remover sem selecionar da erro")
    verifica(any(o["id"] == oid for o in coord.objetos), "o objeto continua intacto (nada foi aplicado)")
    coord.parar()
    f.morre()


def teste15_selecao_unica_liberar():
    print("\n[15] EXCLUSAO MUTUA: 1 objeto por cliente, liberar e colorir soltam o lock")
    coord = sobe_coordenador("lock")
    a = FakeCliente(); b = FakeCliente()
    a.ingressa(coord.porta); b.ingressa(coord.porta)
    o1 = desenha(coord, "linha")
    o2 = desenha(coord, "linha")
    o3 = desenha(coord, "linha")

    a.seleciona(coord.porta, o1)
    a.seleciona(coord.porta, o2)            # ao pegar o2, o1 deve ser solto
    verifica(b.seleciona(coord.porta, o1).get("ok") is True,
             "selecionar outro objeto solta o anterior do mesmo cliente")

    verifica(b.seleciona(coord.porta, o2).get("ok") is False,
             "enquanto A segura o2, B nao consegue pega-lo")
    a.libera(coord.porta)
    verifica(b.seleciona(coord.porta, o2).get("ok") is True,
             "depois do 'liberar', o objeto fica livre pra outro")

    a.seleciona(coord.porta, o3)
    a.colore(coord.porta, o3, "red")        # colorir tambem solta a selecao
    verifica(b.seleciona(coord.porta, o3).get("ok") is True,
             "depois de colorir, a selecao e liberada automaticamente")
    coord.parar()
    a.morre(); b.morre()


# ====================== HEARTBEAT E DETECCAO DE FALHA ========================

def teste16_saida_limpa():
    print("\n[16] CHURN: quem manda 'sair' e removido na hora")
    coord = sobe_coordenador("saida")
    fakes = [FakeCliente() for _ in range(N_CHURN)]
    for f in fakes:
        f.ingressa(coord.porta)
    verifica(len(coord.participantes) == N_CHURN, "%d clientes entraram" % N_CHURN)
    metade = N_CHURN // 2
    for f in fakes[:metade]:
        f.sai(coord.porta)
    verifica(len(coord.participantes) == N_CHURN - metade,
             "os %d que sairam avisando foram removidos na hora" % metade)
    coord.parar()
    for f in fakes:
        f.morre()


def teste17_morte_abrupta():
    print("\n[17] HEARTBEAT: detecta e remove quem caiu sem avisar")
    coord = sobe_coordenador("morte")
    fakes = [FakeCliente() for _ in range(N_CHURN)]
    for f in fakes:
        f.ingressa(coord.porta)
    metade = N_CHURN // 2
    inicio = time.time()
    for f in fakes[:metade]:
        f.morre()
    ok = espera_ate(lambda: len(coord.participantes) == N_CHURN - metade, timeout=4 * HEARTBEAT_T + 6)
    verifica(ok, "o heartbeat detectou e removeu os %d clientes mortos" % metade)
    print("   (detectou as quedas em ~%.1fs)" % (time.time() - inicio))
    coord.parar()
    for f in fakes:
        f.morre()


def teste18_selecao_do_morto():
    print("\n[18] HEARTBEAT: lock de cliente que caiu e liberado")
    coord = sobe_coordenador("lockmorto")
    a = FakeCliente(); b = FakeCliente()
    a.ingressa(coord.porta); b.ingressa(coord.porta)
    oid = desenha(coord, "linha")
    verifica(a.seleciona(coord.porta, oid).get("ok") is True, "A selecionou o objeto")
    verifica(b.seleciona(coord.porta, oid).get("ok") is False, "B nao consegue enquanto A segura")
    a.morre()
    liberou = espera_ate(lambda: b.seleciona(coord.porta, oid).get("ok") is True,
                         timeout=4 * HEARTBEAT_T + 6)
    verifica(liberou, "depois que A caiu, o heartbeat liberou o lock e B conseguiu selecionar")
    coord.parar()
    a.morre(); b.morre()


def teste19_churn_combinado():
    print("\n[19] CAOS: desenhar + entrar + sair + cair ao mesmo tempo")
    coord = sobe_coordenador("caos")
    base = [FakeCliente() for _ in range(6)]
    for f in base:
        f.ingressa(coord.porta)

    desenhistas = base[:4]
    por_um = N_CHAOS_DESENHOS // len(desenhistas)
    total_desenhos = por_um * len(desenhistas)

    def desenha_loop(f):
        for i in range(por_um):
            rede.envia(IP, coord.porta, {"op": "add_linha", "x1": 0, "y1": 0, "x2": i, "y2": i})

    novos = [FakeCliente() for _ in range(4)]

    def entra(f):
        f.ingressa(coord.porta)

    ts = []
    ts += [threading.Thread(target=desenha_loop, args=(f,)) for f in desenhistas]
    ts += [threading.Thread(target=entra, args=(f,)) for f in novos]
    ts += [threading.Thread(target=lambda f=f: f.sai(coord.porta)) for f in base[4:6]]
    for t in ts:
        t.start()
    morrem = novos[:2]
    for f in morrem:
        f.morre()
    for t in ts:
        t.join()

    verifica(len(coord.objetos) == total_desenhos,
             "todos os %d desenhos foram guardados, mesmo com o churn no meio" % total_desenhos)
    verifica(len(set(o["id"] for o in coord.objetos)) == total_desenhos, "ids unicos durante o caos")
    espera_ate(lambda: all([IP, f.porta] not in coord.participantes for f in morrem),
               timeout=4 * HEARTBEAT_T + 6)
    enderecos = [tuple(p) for p in coord.participantes]
    verifica(len(set(enderecos)) == len(enderecos), "lista de participantes sem duplicatas no fim")
    verifica(all([IP, f.porta] not in coord.participantes for f in morrem),
             "os clientes que cairam foram retirados da lista")
    coord.parar()
    for f in base + novos:
        f.morre()


# ======================= ELEICAO E TOLERANCIA A FALHAS ======================

def teste20_eleicao_failover():
    print("\n[20] ELEICAO: morte do coordenador e recuperacao completa")
    host, clientes = monta_cluster("falha", N_ELEICAO)
    desenha(host.coordenador, "linha")          # desenho que precisa sobreviver

    print("   matando o coordenador (host)...")
    host.desliga()

    ok = espera_ate(lambda: sum(1 for c in clientes if eh_coordenador(c)) == 1, timeout=TIMEOUT_ELEICAO)
    verifica(ok, "apos a queda, exatamente UM cliente virou coordenador (sem dois donos)")
    eleitos = [c for c in clientes if eh_coordenador(c)]
    if not eleitos:
        host.desliga()
        for c in clientes:
            c.desliga()
        return
    novo = eleitos[0]

    maior_porta = max(c.porta for c in clientes)
    verifica(novo.porta == maior_porta, "venceu o cliente de maior porta (algoritmo do valentao)")

    verifica(espera_ate(lambda: porta_no_nomes("falha") == novo.coordenador.porta, timeout=TIMEOUT_ELEICAO),
             "o servico de nomes foi atualizado com o endereco do novo coordenador")
    verifica(len(novo.coordenador.objetos) >= 1, "o novo coordenador recuperou o desenho que ja existia")
    verifica(len(novo.coordenador.participantes) >= N_ELEICAO - 1,
             "o novo coordenador recuperou a lista de participantes")
    verifica(espera_ate(lambda: all(c.coord_porta == novo.coordenador.porta for c in clientes if c.ligado)),
             "todos os clientes passaram a apontar para o novo coordenador")

    antes = len(novo.coordenador.objetos)
    rede.envia(IP, novo.coord_porta, {"op": "add_linha", "x1": 1, "y1": 1, "x2": 2, "y2": 2})
    verifica(len(novo.coordenador.objetos) == antes + 1, "da pra continuar desenhando no novo coordenador")

    host.desliga()
    for c in clientes:
        c.desliga()


def teste21_eleicao_cascata():
    print("\n[21] ELEICAO: cascata (mata tambem o recem-eleito)")
    host, clientes = monta_cluster("cascata", N_ELEICAO)
    host.desliga()
    ok1 = espera_ate(lambda: sum(1 for c in clientes if eh_coordenador(c)) == 1, timeout=TIMEOUT_ELEICAO)
    verifica(ok1, "primeira eleicao elegeu um coordenador")
    eleitos = [c for c in clientes if eh_coordenador(c)]
    if eleitos:
        novo1 = eleitos[0]
        print("   matando tambem o coordenador recem-eleito...")
        novo1.desliga()
        restantes = [c for c in clientes if c.ligado]
        ok2 = espera_ate(lambda: sum(1 for c in restantes if eh_coordenador(c)) == 1, timeout=TIMEOUT_ELEICAO)
        verifica(ok2, "depois da segunda queda, outro cliente assumiu (eleicao em cascata)")
    host.desliga()
    for c in clientes:
        c.desliga()


# ================================== BORDA ====================================

def teste22_reentrancia():
    print("\n[22] BORDA: cliente sai e volta sem duplicar")
    coord = sobe_coordenador("reentra")
    f = FakeCliente()
    f.ingressa(coord.porta)
    verifica([IP, f.porta] in coord.participantes, "entrou no quadro")
    f.sai(coord.porta)
    verifica([IP, f.porta] not in coord.participantes, "saiu do quadro")
    f.ingressa(coord.porta)
    verifica(coord.participantes.count([IP, f.porta]) == 1, "voltou e aparece exatamente uma vez")
    coord.parar()
    f.morre()


TESTES = [
    teste01_nomes_basico,
    teste02_nomes_concorrente,
    teste03_nomes_resiliencia,
    teste04_entrada_dinamica,
    teste05_sync_estado_grande,
    teste06_escala_entrada,
    teste07_ingressar_idempotente,
    teste08_carga_desenho,
    teste09_tipos_figura,
    teste10_colorir_propaga,
    teste11_remover_propaga,
    teste12_corrida_selecao,
    teste13_colorir_x_remover,
    teste14_operacao_sem_selecao,
    teste15_selecao_unica_liberar,
    teste16_saida_limpa,
    teste17_morte_abrupta,
    teste18_selecao_do_morto,
    teste19_churn_combinado,
    teste20_eleicao_failover,
    teste21_eleicao_cascata,
    teste22_reentrancia,
]


def roda_um(t):
    try:
        t()
    except Exception as e:
        verifica(False, "EXCECAO em %s: %s" % (t.__name__, e))


if __name__ == "__main__":
    nomes = sobe_servico_nomes()
    try:
        if len(sys.argv) > 1:
            roda_um(TESTES[int(sys.argv[1]) - 1])
        else:
            for t in TESTES:
                roda_um(t)
        print("\n==============================")
        print("RESUMO: %d de %d verificacoes passaram" % (sum(resultados), len(resultados)))
        print("==============================")
    finally:
        nomes.terminate()
        subprocess.run(["pkill", "-f", "servico_nomes.py"])
