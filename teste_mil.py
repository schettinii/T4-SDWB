# teste de escala: joguei 1000 clientes entrando e desenhando pra ver se aguenta.
# usa as coisas que ja estao no testes_estresse.py. roda: python3 teste_mil.py

import threading
import time

import rede
import coordenador
import testes_estresse as te

N = 1000
LOTE = 100   # ingressos por lote, senao estoura o backlog do accept (deu erro qnd tentei tudo de uma vez)

# heartbeat normal do trabalho (3s); com 1000 clientes um ciclo ja e pesado.
coordenador.T = 3


def roda():
    nomes = te.sobe_servico_nomes()
    try:
        coord = te.sobe_coordenador("quadro_mil")
        fakes = [te.FakeCliente() for _ in range(N)]

        # ingresso concorrente, em lotes
        inicio = time.time()
        for i in range(0, N, LOTE):
            grupo = fakes[i:i + LOTE]
            ts = [threading.Thread(target=f.ingressa, args=(coord.porta,)) for f in grupo]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        demora_in = time.time() - inicio

        te.verifica(len(coord.participantes) == N,
                    "%d clientes entraram e todos estao na lista (tem %d)" % (N, len(coord.participantes)))
        enderecos = [tuple(p) for p in coord.participantes]
        te.verifica(len(set(enderecos)) == len(enderecos),
                    "sem duplicatas na lista de participantes")
        print("   (ingresso de %d clientes em %.2fs)" % (N, demora_in))

        # cada um dos primeiros 100 desenha 1 linha (o broadcast e O(n) por acao)
        desenhistas = fakes[:100]
        inicio = time.time()

        def desenha(f):
            rede.envia(te.IP, coord.porta, {"op": "add_linha", "x1": 0, "y1": 0, "x2": 1, "y2": 1})

        ts = [threading.Thread(target=desenha, args=(f,)) for f in desenhistas]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        demora_draw = time.time() - inicio

        te.verifica(len(coord.objetos) == 100, "100 objetos guardados (tem %d)" % len(coord.objetos))
        ids = [o["id"] for o in coord.objetos]
        te.verifica(len(set(ids)) == 100, "ids unicos (contador aguentou a concorrencia)")
        print("   (100 desenhos com broadcast pra %d clientes em %.2fs)" % (N, demora_draw))

        # convergencia: os 1000 tem que receber os 100 objetos
        ok_conv = te.espera_ate(lambda: all(f.conta("novo_objeto") >= 100 for f in fakes), timeout=30)
        te.verifica(ok_conv, "todos os %d clientes receberam os 100 objetos (estado convergente)" % N)

        coord.parar()
        for f in fakes:
            f.morre()

        print("\n==============================")
        print("RESUMO: %d de %d verificacoes passaram" % (sum(te.resultados), len(te.resultados)))
        print("==============================")
    finally:
        nomes.terminate()
        import subprocess
        subprocess.run(["pkill", "-f", "servico_nomes.py"])


if __name__ == "__main__":
    roda()
