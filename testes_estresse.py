# Testes de estresse do SDWB. Rodam sem abrir a interface grafica: usam clientes
# "de mentira" (so a parte de rede) e a propria classe Cliente sem a janela.
#
# Uso:
#   python3 testes_estresse.py        roda todos
#   python3 testes_estresse.py 3      roda so o teste 3
#
# Cada teste sobe seu proprio coordenador. O servico de nomes sobe uma vez no inicio.

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

IP = "127.0.0.1"
NOMES = (IP, 6000)

# nos testes deixo o heartbeat mais rapido (1s) so pra nao ter que esperar muito.
# no trabalho de verdade ele e 3s.
cli.T = 1
coordenador.T = 1

resultados = []


def verifica(cond, descricao):
    print(("   OK    " if cond else "   FALHOU  ") + descricao)
    resultados.append(cond)


def espera_ate(cond, timeout=12):
    fim = time.time() + timeout
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.2)
    return False


def sobe_servico_nomes():
    subprocess.run(["pkill", "-f", "servico_nomes.py"])
    time.sleep(0.5)
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "servico_nomes.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    return p


def sobe_coordenador(nome):
    c = coordenador.Coordenador(nome, IP, 0)
    c.iniciar()
    rede.envia(NOMES[0], NOMES[1], {"op": "registrar", "nome": nome, "ip": IP, "porta": c.porta})
    return c


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
            except:
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
        except:
            pass

    def conta(self, op):
        return len([m for m in self.recebidas if m["op"] == op])

    def ingressa(self, coord_porta):
        return rede.envia(IP, coord_porta, {"op": "ingressar", "ip": IP, "porta": self.porta})

    def sai(self, coord_porta):
        rede.envia(IP, coord_porta, {"op": "sair", "ip": IP, "porta": self.porta})


# a classe Cliente DE VERDADE, mas sem abrir a janela. serve pra testar o
# heartbeat e a eleicao com o codigo real do trabalho.
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
        # simula desligar o PC: mata o coordenador (se hospedava) e o servidor do cliente
        self.ligado = False
        if self.coordenador is not None:
            self.coordenador.parar()
        try:
            self.servidor.close()
        except:
            pass


def eh_coordenador(c):
    return c.coordenador is not None and c.coordenador.ativo


# ---------------------------------------------------------------------------

def teste1_muitos_clientes():
    print("\n[1] ESCALA DE NOS: muitos clientes entrando ao mesmo tempo")
    coord = sobe_coordenador("quadro1")
    n = 30
    fakes = [FakeCliente() for _ in range(n)]

    inicio = time.time()
    threads = [threading.Thread(target=f.ingressa, args=(coord.porta,)) for f in fakes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    demora = time.time() - inicio

    verifica(len(coord.participantes) == n, str(n) + " clientes entraram e todos estao na lista do coordenador")
    enderecos = [tuple(p) for p in coord.participantes]
    verifica(len(set(enderecos)) == n, "nao houve duplicata na lista de participantes (entrada concorrente)")
    verifica(espera_ate(lambda: all(f.conta("participantes") >= 1 for f in fakes)),
             "todos os clientes receberam atualizacao da lista de participantes")
    print("   (entrada de %d clientes levou %.2fs)" % (n, demora))

    coord.parar()
    for f in fakes:
        f.morre()


def teste2_carga_desenho():
    print("\n[2] CARGA: varios clientes desenhando muitos objetos em paralelo")
    coord = sobe_coordenador("quadro2")
    n = 8
    por_cliente = 40
    total = n * por_cliente
    fakes = [FakeCliente() for _ in range(n)]
    for f in fakes:
        f.ingressa(coord.porta)

    def desenha_varios(f):
        for i in range(por_cliente):
            rede.envia(IP, coord.porta, {"op": "add_linha", "x1": 0, "y1": 0, "x2": i, "y2": i})

    inicio = time.time()
    threads = [threading.Thread(target=desenha_varios, args=(f,)) for f in fakes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    demora = time.time() - inicio

    verifica(len(coord.objetos) == total, "coordenador guardou os %d objetos (nenhum perdido)" % total)
    ids = [o["id"] for o in coord.objetos]
    verifica(len(set(ids)) == total, "todos os ids sao unicos (o contador aguentou a concorrencia)")
    verifica(espera_ate(lambda: all(f.conta("novo_objeto") >= total for f in fakes), timeout=20),
             "todos os clientes receberam todos os objetos (estado convergente)")
    print("   (%d objetos em %.2fs -> %.0f objetos/s)" % (total, demora, total / demora))

    coord.parar()
    for f in fakes:
        f.morre()


def teste3_exclusao_mutua():
    print("\n[3] EXCLUSAO MUTUA: corrida pela selecao do mesmo objeto")
    coord = sobe_coordenador("quadro3")
    rede.envia(IP, coord.porta, {"op": "add_linha", "x1": 0, "y1": 0, "x2": 9, "y2": 9})  # objeto 1
    n = 20
    fakes = [FakeCliente() for _ in range(n)]
    for f in fakes:
        f.ingressa(coord.porta)

    # todos disparam o "selecionar" no mesmo instante
    barreira = threading.Barrier(n)
    respostas = []
    trava = threading.Lock()

    def tenta(f):
        barreira.wait()
        r = rede.envia(IP, coord.porta, {"op": "selecionar", "obj_id": 1, "ip": IP, "porta": f.porta})
        with trava:
            respostas.append((f, r))

    threads = [threading.Thread(target=tenta, args=(f,)) for f in fakes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ganharam = [f for f, r in respostas if r.get("ok")]
    perderam = [f for f, r in respostas if not r.get("ok")]
    verifica(len(ganharam) == 1, "exatamente 1 cliente conseguiu selecionar (%d tentaram juntos)" % n)
    verifica(len(perderam) == n - 1, "os outros %d receberam erro de objeto em uso" % (n - 1))

    # quem ganhou colore -> libera o objeto -> outro consegue selecionar
    dono = ganharam[0]
    rede.envia(IP, coord.porta, {"op": "colorir", "obj_id": 1, "cor": "blue", "ip": IP, "porta": dono.porta})
    outro = [f for f in fakes if f is not dono][0]
    r = rede.envia(IP, coord.porta, {"op": "selecionar", "obj_id": 1, "ip": IP, "porta": outro.porta})
    verifica(r.get("ok") is True, "depois de colorir, a selecao foi liberada e outro cliente pode pegar")

    # corrida colorir x remover no mesmo objeto: so uma das duas vale
    rede.envia(IP, coord.porta, {"op": "add_linha", "x1": 1, "y1": 1, "x2": 2, "y2": 2})  # objeto 2
    rede.envia(IP, coord.porta, {"op": "selecionar", "obj_id": 2, "ip": IP, "porta": dono.porta})
    res2 = {}

    def faz_colorir():
        res2["cor"] = rede.envia(IP, coord.porta, {"op": "colorir", "obj_id": 2, "cor": "red", "ip": IP, "porta": dono.porta})

    def faz_remover():
        res2["rem"] = rede.envia(IP, coord.porta, {"op": "remover", "obj_id": 2, "ip": IP, "porta": dono.porta})

    ta = threading.Thread(target=faz_colorir)
    tb = threading.Thread(target=faz_remover)
    ta.start(); tb.start(); ta.join(); tb.join()
    aplicou = [1 for r in res2.values() if r.get("ok")]
    verifica(len(aplicou) == 1, "colorir x remover concorrentes: so uma operacao foi aceita (atomicidade)")

    coord.parar()
    for f in fakes:
        f.morre()


def teste4_churn_heartbeat():
    print("\n[4] CHURN: clientes saindo de forma limpa e morrendo de repente")
    coord = sobe_coordenador("quadro4")
    n = 12
    fakes = [FakeCliente() for _ in range(n)]
    for f in fakes:
        f.ingressa(coord.porta)
    verifica(len(coord.participantes) == n, "%d clientes entraram" % n)

    # 4 saem avisando
    saem = fakes[0:4]
    for f in saem:
        f.sai(coord.porta)
    verifica(espera_ate(lambda: len(coord.participantes) == n - 4),
             "quem saiu avisando foi removido na hora (sobraram %d)" % (n - 4))

    # 4 morrem sem avisar -> o heartbeat tem que detectar e remover
    morrem = fakes[4:8]
    inicio = time.time()
    for f in morrem:
        f.morre()
    verifica(espera_ate(lambda: len(coord.participantes) == n - 8, timeout=10),
             "o heartbeat detectou os clientes mortos e removeu (sobraram %d)" % (n - 8))
    print("   (detectou as quedas em ~%.1fs)" % (time.time() - inicio))

    coord.parar()
    for f in fakes:
        f.morre()


def teste5_eleicao():
    print("\n[5] TOLERANCIA A FALHAS: morte do coordenador e eleicao em cascata")
    host = ClienteSimulado("quadro5")
    host.cria_quadro()
    rede.envia(IP, host.coord_porta, {"op": "add_linha", "x1": 0, "y1": 0, "x2": 7, "y2": 7})  # desenho que precisa sobreviver

    n = 6
    clientes = [ClienteSimulado("quadro5") for _ in range(n)]
    for c in clientes:
        c.coord_ip = host.coord_ip
        c.coord_porta = host.coord_porta
        c.ingressa()
    # espera todo mundo enxergar a lista cheia (host + n clientes)
    espera_ate(lambda: all(len(c.participantes) >= n + 1 for c in clientes))

    print("   matando o coordenador (host)...")
    host.desliga()

    vivos = clientes
    ok_eleicao = espera_ate(lambda: sum(1 for c in vivos if eh_coordenador(c)) == 1, timeout=15)
    verifica(ok_eleicao, "apos a queda, exatamente UM cliente virou coordenador (sem dois donos)")
    novo = [c for c in vivos if eh_coordenador(c)]
    if novo:
        novo = novo[0]
        verifica(espera_ate(lambda: all(c.coord_porta == novo.coordenador.porta for c in vivos if c.ligado)),
                 "todos os clientes passaram a apontar para o novo coordenador")
        r = rede.envia(NOMES[0], NOMES[1], {"op": "listar"})
        ent = [q for q in r["quadros"] if q[0] == "quadro5"]
        verifica(len(ent) == 1 and ent[0][2] == novo.coordenador.porta,
                 "o servico de nomes foi atualizado com o endereco do novo coordenador")
        e = rede.envia(IP, novo.coordenador.porta, {"op": "ingressar", "ip": IP, "porta": 1})
        verifica(len(e["objetos"]) >= 1, "o novo coordenador recuperou o desenho que ja existia")

        # cascata: mata tambem o coordenador novo e ve se outro assume
        print("   matando tambem o coordenador recem-eleito (cascata)...")
        novo.desliga()
        restantes = [c for c in vivos if c.ligado]
        ok2 = espera_ate(lambda: sum(1 for c in restantes if eh_coordenador(c)) == 1, timeout=15)
        verifica(ok2, "depois de uma segunda queda, outro cliente assumiu (eleicao em cascata)")

    host.desliga()
    for c in clientes:
        c.desliga()


TESTES = [teste1_muitos_clientes, teste2_carga_desenho, teste3_exclusao_mutua,
          teste4_churn_heartbeat, teste5_eleicao]


if __name__ == "__main__":
    nomes = sobe_servico_nomes()
    try:
        if len(sys.argv) > 1:
            TESTES[int(sys.argv[1]) - 1]()
        else:
            for t in TESTES:
                t()
        print("\n==============================")
        print("RESUMO: %d de %d verificacoes passaram" % (sum(resultados), len(resultados)))
        print("==============================")
    finally:
        nomes.terminate()
        subprocess.run(["pkill", "-f", "servico_nomes.py"])
